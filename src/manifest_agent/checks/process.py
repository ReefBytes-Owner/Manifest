"""Bounded argv execution with an explicit environment and process-group cleanup.

Check bodies spawned through ``run_argv`` are not SIGINT-interruptible by
design: ``start_new_session=True`` detaches each body into its own session,
and whatever launched the whole check tree may itself run with SIGINT
ignored (SIG_IGN survives exec, unlike a caught handler, so an ignoring
ancestor's disposition propagates all the way down). Cancellation therefore
never relies on SIGINT reaching a body -- ``_cleanup`` below always drives it
through SIGTERM, then SIGKILL, against the whole process group. A caller
that needs to prove SIGINT semantics for its OWN spawned process (as
``tests/python/manifest_agent/test_check_process.py``'s cancellation test
does) must reset SIGINT to its default disposition in that process itself
(``preexec_fn``), not assume the ambient one.
"""

from __future__ import annotations

import math
import os
import re
import selectors
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from manifest_agent.process import redact_text

CAPTURE_LIMIT = 65536
TRUNCATION_MARKER = b"\n...[truncated]\n"

# Correction 16 (phase-3-5-decisions.md): a check's own failure lines (bats'
# ``not ok ``, pytest's ``FAILED ``) can appear anywhere in a long stream, not
# only at the end. `_capture`'s per-stream tail buffer already drops the head
# once a stream exceeds CAPTURE_LIMIT, so post-processing the already-bounded
# result (as C7p's bats-only hook did) can lose an early failure entirely.
# Matching happens WHILE STREAMING, before any bytes are dropped, and the
# accumulated lines are appended as their own trailer bounded independently
# of the tail window.
_FAILURE_TRAILER_LIMIT = 16384
_FAILURE_LINE_STORE_LIMIT = 4096


def tail_bounded(
    encoded: bytes, limit: int = CAPTURE_LIMIT, original_len: int | None = None
) -> bytes:
    """Bound ``encoded`` to ``limit`` bytes, keeping the LAST bytes.

    Test-runner summaries (pytest, bats) are emitted at the end of output, so
    truncation must drop the head, not the tail, and say so explicitly. The
    marker's own size depends on the truncated-byte count it reports; one
    correction pass is enough to converge (the digit count of that number
    only changes at decade boundaries).

    ``original_len`` lets a caller report a size larger than ``len(encoded)``
    when bytes were already dropped upstream (e.g. streaming capture already
    kept only the tail) -- the marker must still name the true drop, not zero.
    """
    if original_len is None:
        original_len = len(encoded)
    if original_len <= limit and len(encoded) <= limit:
        return encoded
    marker = f"[head truncated: {original_len} bytes]\n".encode()
    tail_len = max(0, limit - len(marker))
    truncated_bytes = original_len - tail_len
    marker = f"[head truncated: {truncated_bytes} bytes]\n".encode()
    tail_len = max(0, limit - len(marker))
    tail = encoded[-tail_len:] if tail_len else b""
    return (marker + tail)[:limit]


@dataclass(frozen=True)
class ProcessResult:
    """Captured command outcome, including failures to start or finish."""

    returncode: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    error: str = ""


def clean_git_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Remove inherited Git controls and suppress user/system configuration."""
    if any(
        not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v
        for k, v in env.items()
    ):
        raise ValueError("environment must contain non-null strings")
    result = {k: v for k, v in env.items() if not k.startswith("GIT_")}
    result.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
        GIT_NO_LAZY_FETCH="1",
        GIT_ALLOW_PROTOCOL="",
        GIT_PROTOCOL_FROM_USER="0",
    )
    return result


def git_output(
    root: Path,
    *args: str,
    data: bytes | None = None,
    allowed_codes: tuple[int, ...] = (0,),
) -> bytes:
    """Run an internal Git plumbing command with no inherited Git controls."""
    command = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "submodule.recurse=false",
        "-C",
        str(root),
        *args,
    ]
    result = subprocess.run(
        command,
        input=data,
        capture_output=True,
        env=clean_git_environment({"PATH": os.defpath, "LC_ALL": "C"}),
        timeout=120,
        check=False,
    )
    if result.returncode not in allowed_codes:
        raise subprocess.CalledProcessError(result.returncode, command)
    return result.stdout


def _kill_group(process: subprocess.Popen[bytes], sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        # The group has already exited; the leader is still reaped below.
        return


def _consume_ready(
    key, selector, buffers: list[bytearray], truncated: list[bool], received: list[int]
) -> bytes:
    """Read one ready stream and fold it into its bounded tail buffer."""
    data = os.read(key.fd, 8192)
    if not data:
        selector.unregister(key.fileobj)
        return b""
    received[key.data] += len(data)
    buffer = buffers[key.data]
    buffer.extend(data)
    if len(buffer) > CAPTURE_LIMIT:
        del buffer[: len(buffer) - CAPTURE_LIMIT]
        truncated[key.data] = True
    return data


def _accumulate_failure_lines(
    carry: bytearray, data: bytes, pattern: re.Pattern[bytes], sink: list[bytes]
) -> None:
    """Fold ``data`` into ``carry`` and move every complete line matching
    ``pattern`` into ``sink``, independent of any tail-window truncation."""
    carry.extend(data)
    while True:
        newline = carry.find(b"\n")
        if newline == -1:
            break
        line = bytes(carry[:newline])
        del carry[: newline + 1]
        if pattern.match(line) and len(sink) < _FAILURE_LINE_STORE_LIMIT:
            sink.append(line)


def _capture(
    process: subprocess.Popen[bytes],
    deadline: float,
    failure_pattern: re.Pattern[bytes] | None = None,
) -> tuple[bytes, bytes, bool, bool, bool, int, int, list[bytes]]:
    buffers = [bytearray(), bytearray()]
    truncated = [False, False]
    received = [0, 0]
    timed_out = False
    failure_lines: list[bytes] = []
    failure_carry = bytearray()
    with selectors.DefaultSelector() as selector:
        for index, stream in enumerate((process.stdout, process.stderr)):
            assert stream is not None
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if now >= deadline:
                timed_out = True
                break
            for key, _ in selector.select(min(0.05, deadline - now)):
                data = _consume_ready(key, selector, buffers, truncated, received)
                if data and key.data == 0 and failure_pattern is not None:
                    _accumulate_failure_lines(
                        failure_carry, data, failure_pattern, failure_lines
                    )
    if failure_pattern is not None and failure_carry:
        leftover = bytes(failure_carry)
        if (
            failure_pattern.match(leftover)
            and len(failure_lines) < _FAILURE_LINE_STORE_LIMIT
        ):
            failure_lines.append(leftover)
    return (
        bytes(buffers[0]),
        bytes(buffers[1]),
        timed_out,
        truncated[0],
        truncated[1],
        received[0],
        received[1],
        failure_lines,
    )


def _reportable(
    data: bytes, truncated: bool, received: int, limit: int = CAPTURE_LIMIT
) -> str:
    encoded = redact_text(data.decode("utf-8", errors="replace")).encode("utf-8")
    original_len = max(received, len(encoded)) if truncated else len(encoded)
    if truncated or len(encoded) > limit:
        encoded = tail_bounded(encoded, limit=limit, original_len=original_len)
    return encoded[:limit].decode("utf-8", errors="ignore")


def _failure_trailer_text(failure_lines: Sequence[bytes]) -> str:
    """Render ``# failure summary: N`` plus the matched lines, bounded
    independently of the main tail window so an early failure is never
    dropped by truncation applied to the rest of the stream."""
    total = len(failure_lines)
    header = f"# failure summary: {total}\n"
    size = len(header.encode("utf-8"))
    rendered: list[str] = []
    included = 0
    for raw in failure_lines:
        text = redact_text(raw.decode("utf-8", errors="replace")) + "\n"
        text_size = len(text.encode("utf-8"))
        if size + text_size > _FAILURE_TRAILER_LIMIT:
            break
        rendered.append(text)
        size += text_size
        included += 1
    remaining = total - included
    if remaining:
        rendered.append(f"... and {remaining} more\n")
    return header + "".join(rendered)


def _stdout_with_failure_trailer(
    data: bytes,
    truncated: bool,
    received: int,
    failure_lines: list[bytes] | None,
) -> str:
    if failure_lines is None:
        return _reportable(data, truncated, received)
    trailer = _failure_trailer_text(failure_lines)
    main_limit = max(0, CAPTURE_LIMIT - len(trailer.encode("utf-8")))
    main = _reportable(data, truncated, received, limit=main_limit)
    if main and not main.endswith("\n"):
        main += "\n"
    return main + trailer


def _cleanup(process: subprocess.Popen[bytes]) -> str:
    error = ""
    try:
        _kill_group(process, signal.SIGTERM)
    except OSError as failure:
        error = redact_text(str(failure))
        process.terminate()
    try:
        process.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        try:
            _kill_group(process, signal.SIGKILL)
        except OSError as failure:
            error = redact_text(str(failure))
            process.kill()
        try:
            process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            error = error or "process leader did not exit after SIGKILL"
    try:
        _kill_group(process, signal.SIGKILL)
    except OSError as failure:
        error = redact_text(str(failure))
    return error


def _validate_run_argv_inputs(argv: Sequence[str], timeout_seconds: float) -> None:
    if (
        isinstance(argv, (str, bytes))
        or not argv
        or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
    ):
        raise ValueError("argv must be non-null strings")
    if (
        isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be finite and positive")


def _spawn(
    argv: Sequence[str], cwd: Path, environment: dict[str, str]
) -> subprocess.Popen[bytes] | str:
    """Start argv in its own session; return the error string on failure."""
    try:
        return subprocess.Popen(
            tuple(argv),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return redact_text(str(error))


def _finalize_result(
    process: subprocess.Popen[bytes],
    start: float,
    captured: tuple[bytes, bytes, bool, bool, bool, int, int, list[bytes]],
    failure_pattern: re.Pattern[bytes] | None,
    error: str,
) -> ProcessResult:
    (stdout, stderr, timed_out, out_trunc, err_trunc, out_recv, err_recv, lines) = (
        captured
    )
    reported_stdout = _stdout_with_failure_trailer(
        stdout,
        out_trunc,
        out_recv,
        lines if failure_pattern and not timed_out else None,
    )
    return ProcessResult(
        process.returncode,
        reported_stdout,
        _reportable(stderr, err_trunc, err_recv),
        time.monotonic() - start,
        timed_out,
        error,
    )


def run_argv(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    failure_line_regex: str = "",
) -> ProcessResult:
    """Run argv without a shell; bound both streams and terminate the whole group.

    ``failure_line_regex`` (Correction 16), when non-empty, is matched against
    every complete stdout line as it streams in, independent of the tail
    window -- see the module docstring comment above ``_capture``. An empty
    string means the check declared no failure-line pattern, so no trailer is
    appended at all.
    """
    _validate_run_argv_inputs(argv, timeout_seconds)
    environment = clean_git_environment(env)
    failure_pattern = (
        re.compile(failure_line_regex.encode()) if failure_line_regex else None
    )
    start = time.monotonic()
    if os.name != "posix" or not hasattr(os, "killpg"):
        return ProcessResult(
            None,
            "",
            "",
            time.monotonic() - start,
            error="required POSIX process-group lifecycle is unsupported",
        )
    process = _spawn(argv, cwd, environment)
    if isinstance(process, str):
        return ProcessResult(None, "", "", time.monotonic() - start, error=process)
    with process:
        try:
            captured = _capture(process, start + timeout_seconds, failure_pattern)
        finally:
            # Also kill descendants that closed their pipes before the leader exited.
            error = _cleanup(process)
    return _finalize_result(process, start, captured, failure_pattern, error)
