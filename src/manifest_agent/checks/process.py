"""Bounded argv execution with an explicit environment and process-group cleanup."""

from __future__ import annotations

import math
import os
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


def _capture(
    process: subprocess.Popen[bytes], deadline: float
) -> tuple[bytes, bytes, bool, bool, bool]:
    buffers = [bytearray(), bytearray()]
    truncated = [False, False]
    timed_out = False
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
                data = os.read(key.fd, 8192)
                if not data:
                    selector.unregister(key.fileobj)
                else:
                    buffer = buffers[key.data]
                    remaining = max(0, CAPTURE_LIMIT - len(buffer))
                    buffer.extend(data[:remaining])
                    truncated[key.data] |= len(data) > remaining
    return (
        bytes(buffers[0]),
        bytes(buffers[1]),
        timed_out,
        truncated[0],
        truncated[1],
    )


def _reportable(data: bytes, truncated: bool) -> str:
    encoded = redact_text(data.decode("utf-8", errors="replace")).encode("utf-8")
    if truncated or len(encoded) > CAPTURE_LIMIT:
        encoded = encoded[: CAPTURE_LIMIT - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    return encoded[:CAPTURE_LIMIT].decode("utf-8", errors="ignore")


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


def run_argv(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout_seconds: float
) -> ProcessResult:
    """Run argv without a shell; bound both streams and terminate the whole group."""
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
    environment = clean_git_environment(env)
    start = time.monotonic()
    if os.name != "posix" or not hasattr(os, "killpg"):
        return ProcessResult(
            None,
            "",
            "",
            time.monotonic() - start,
            error="required POSIX process-group lifecycle is unsupported",
        )
    try:
        process = subprocess.Popen(
            tuple(argv),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return ProcessResult(
            None, "", "", time.monotonic() - start, error=redact_text(str(error))
        )
    with process:
        try:
            stdout, stderr, timed_out, stdout_truncated, stderr_truncated = _capture(
                process, start + timeout_seconds
            )
        finally:
            # Also kill descendants that closed their pipes before the leader exited.
            error = _cleanup(process)
    return ProcessResult(
        process.returncode,
        _reportable(stdout, stdout_truncated),
        _reportable(stderr, stderr_truncated),
        time.monotonic() - start,
        timed_out,
        error,
    )
