"""Bound and supervise subprocesses launched by the tool-version adapter."""

from __future__ import annotations

import errno
import os
import selectors
import signal
import stat
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from types import FrameType

OUTPUT_LIMIT = 16_384
INTERNAL_TIMEOUT_SECONDS = 8.0
CLEANUP_GRACE_SECONDS = 0.04


def _probe_environment(path: str = os.defpath) -> dict[str, str]:
    return {
        "PATH": path,
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONNOUSERSITE": "1",
    }


def _env_node_runtime(argv: list[str]) -> str | None:
    try:
        with open(argv[0], "rb") as stream:
            first_line = stream.readline(128).rstrip(b"\r\n")
    except (OSError, ValueError) as error:
        raise ProbeProcessError(
            f"version probe launcher is unreadable: {error}"
        ) from error
    if first_line != b"#!/usr/bin/env node":
        return None
    candidate_root = os.path.realpath(os.getcwd())
    for directory in os.environ.get("PATH", os.defpath).split(os.pathsep):
        search_directory = directory or os.curdir
        candidate = os.path.abspath(os.path.join(search_directory, "node"))
        try:
            resolved = os.path.realpath(candidate, strict=True)
            mode = os.stat(resolved).st_mode
        except OSError:
            continue
        if not stat.S_ISREG(mode) or not os.access(candidate, os.X_OK):
            continue
        controlled = os.path.commonpath((candidate_root, candidate)) == candidate_root
        escaped = os.path.commonpath((candidate_root, resolved)) == candidate_root
        if not directory or not os.path.isabs(directory) or controlled or escaped:
            raise ProbeProcessError("first node runtime winner is untrusted")
        return resolved
    raise ProbeProcessError("trusted node runtime is unavailable")


def _launch_plan(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    runtime = _env_node_runtime(argv)
    if runtime is None:
        return argv, _probe_environment()
    runtime_directory = os.path.dirname(runtime)
    system_directories = [
        path
        for path in os.defpath.split(os.pathsep)
        if path and path != runtime_directory
    ]
    path = os.pathsep.join((runtime_directory, *system_directories))
    return [runtime, *argv], _probe_environment(path)


class ProbeProcessError(ValueError):
    """An inner version process could not be supervised conclusively."""


class _OwnershipLost(ProbeProcessError):
    """The child leader can no longer anchor safe group signaling."""


@dataclass
class _Cancellation:
    signum: int = 0

    def handler(self, signum: int, _frame: FrameType | None) -> None:
        if not self.signum:
            self.signum = signum


def _supported() -> bool:
    primitives = (
        os.name == "posix"
        and hasattr(os, "killpg")
        and hasattr(os, "waitid")
        and hasattr(os, "WNOWAIT")
        and hasattr(os, "set_blocking")
        and hasattr(signal, "SIGTERM")
        and hasattr(signal, "SIGKILL")
        and hasattr(signal, "SIGCHLD")
        and threading.current_thread() is threading.main_thread()
    )
    if not primitives or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        return False
    try:
        os.waitid(os.P_PID, os.getpid(), os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return True
    except OSError:
        return False
    return True


def _signal_group(process: subprocess.Popen[bytes], signum: int) -> OSError | None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        return None
    except OSError as error:
        return error
    return None


def _observe_leader(
    process: subprocess.Popen[bytes], deadline: float, cancellation: _Cancellation
) -> tuple[bool, bool, str]:
    while True:
        if cancellation.signum:
            return (
                False,
                True,
                f"version probe interrupted by signal {cancellation.signum}",
            )
        if _leader_exited(process):
            return True, True, ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, True, "version probe timed out"
        time.sleep(min(0.01, remaining))


def _leader_exited(process: subprocess.Popen[bytes]) -> bool:
    try:
        result = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError as error:
        raise _OwnershipLost(
            "version probe ownership observation failed: leader is not waitable"
        ) from error
    except OSError as error:
        raise _OwnershipLost(
            f"version probe ownership observation failed: {error}"
        ) from error
    return result is not None


def _cleanup_family(
    process: subprocess.Popen[bytes],
    *,
    group_owned: bool = True,
    leader_exited: bool = False,
) -> str:
    errors = []
    if group_owned:
        term_error = _signal_group(process, signal.SIGTERM)
        if term_error and not (leader_exited and term_error.errno == errno.EPERM):
            errors.append(str(term_error))
        time.sleep(CLEANUP_GRACE_SECONDS)
        kill_error = _signal_group(process, signal.SIGKILL)
        if kill_error and not (leader_exited and kill_error.errno == errno.EPERM):
            errors.append(str(kill_error))
    try:
        process.wait(timeout=CLEANUP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        errors.append("version probe leader did not exit")
        if group_owned:
            with suppress(OSError):
                os.kill(process.pid, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=CLEANUP_GRACE_SECONDS)
    return "; ".join(errors)


def _close_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            with suppress(OSError):
                stream.close()


def _read_chunk(fd: int, count: int) -> bytes:
    return os.read(fd, count)


def _capture(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    deadline: float,
    cancellation: _Cancellation,
) -> tuple[bytes, str]:
    output = bytearray()
    for stream in (process.stdout, process.stderr):
        if stream is None:
            raise ProbeProcessError("version probe pipe was not created")
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    while selector.get_map():
        if cancellation.signum:
            return bytes(
                output
            ), f"version probe interrupted by signal {cancellation.signum}"
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            return bytes(output), "version probe timed out"
        for key, _ in selector.select(min(0.05, remaining_time)):
            data = _read_chunk(key.fd, min(8192, OUTPUT_LIMIT + 1))
            if not data:
                selector.unregister(key.fileobj)
                continue
            remaining = OUTPUT_LIMIT - len(output)
            output.extend(data[: max(0, remaining)])
            if len(data) > remaining:
                return bytes(output), "version probe output exceeded limit"
    return bytes(output), ""


def _finalize(
    process: subprocess.Popen[bytes] | None,
    selector: selectors.BaseSelector,
    previous: dict[int, signal.Handlers],
    failure: ProbeProcessError | None,
    cancellation: _Cancellation | None = None,
    group_owned: bool = True,
    leader_exited: bool = False,
) -> ProbeProcessError | None:
    cleanup_error = ""
    try:
        if process is not None:
            cleanup_error = _cleanup_family(
                process, group_owned=group_owned, leader_exited=leader_exited
            )
    except Exception as error:
        cleanup_error = f"version probe cleanup failed: {error}"
    finally:
        if process is not None:
            _close_pipes(process)
    close_error = ""
    try:
        selector.close()
    except Exception as error:
        close_error = str(error)
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except Exception as error:
                close_error = "; ".join(
                    item for item in (close_error, str(error)) if item
                )
    if cancellation is not None and cancellation.signum:
        failure = ProbeProcessError(
            f"version probe interrupted by signal {cancellation.signum}"
        )
    final_error = "; ".join(item for item in (cleanup_error, close_error) if item)
    if not final_error:
        return failure
    detail = str(failure) if failure is not None else ""
    return ProbeProcessError("; ".join(item for item in (detail, final_error) if item))


def _capture_outcome(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    deadline: float,
    cancellation: _Cancellation,
) -> tuple[str, bool, bool]:
    output, reason = _capture(process, selector, deadline, cancellation)
    if reason:
        raise ProbeProcessError(reason)
    leader_exited, group_owned, reason = _observe_leader(
        process, deadline, cancellation
    )
    if reason:
        raise ProbeProcessError(reason)
    return output.decode("utf-8", errors="replace"), leader_exited, group_owned


def _launch(
    argv: list[str], executable: str | None, pass_fds: tuple[int, ...]
) -> subprocess.Popen[bytes]:
    launch_argv, environment = _launch_plan(argv)
    return subprocess.Popen(
        launch_argv,
        executable=executable,
        pass_fds=pass_fds,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=environment,
    )


def _selector() -> selectors.BaseSelector:
    try:
        return selectors.DefaultSelector()
    except Exception as error:
        raise ProbeProcessError(
            f"version probe selector unavailable: {error}"
        ) from error


def _cancellation_state() -> tuple[dict[int, signal.Handlers], _Cancellation]:
    previous = {
        signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    return previous, _Cancellation()


def run_probe(
    argv: list[str],
    timeout_seconds: float = INTERNAL_TIMEOUT_SECONDS,
    *,
    executable: str | None = None,
    pass_fds: tuple[int, ...] = (),
) -> str:
    """Run one inner probe with bounded capture and nested-family cleanup."""
    if not _supported():
        raise ProbeProcessError("required version-probe supervision is unsupported")
    selector = _selector()
    process: subprocess.Popen[bytes] | None = None
    output_text = ""
    failure: ProbeProcessError | None = None
    previous, cancellation = _cancellation_state()
    deadline = time.monotonic() + timeout_seconds
    group_owned = True
    leader_exited = False
    ownership_checked = False
    try:
        for signum in previous:
            signal.signal(signum, cancellation.handler)
        process = _launch(argv, executable, pass_fds)
        output_text, leader_exited, group_owned = _capture_outcome(
            process, selector, deadline, cancellation
        )
        ownership_checked = True
    except _OwnershipLost as error:
        group_owned = False
        ownership_checked = True
        failure = ProbeProcessError(str(error))
    except ProbeProcessError as error:
        failure = error
    except Exception as error:
        failure = ProbeProcessError(f"version probe supervision failed: {error}")
    finally:
        if process is not None and not ownership_checked:
            try:
                leader_exited = _leader_exited(process)
            except _OwnershipLost as error:
                group_owned = False
                detail = str(failure) if failure is not None else ""
                failure = ProbeProcessError(
                    "; ".join(item for item in (detail, str(error)) if item)
                )
        failure = _finalize(
            process,
            selector,
            previous,
            failure,
            cancellation,
            group_owned,
            leader_exited,
        )
    if failure is None and process is not None and process.returncode != 0:
        failure = ProbeProcessError("version probe exited non-zero")
    if failure is not None:
        raise failure
    return output_text
