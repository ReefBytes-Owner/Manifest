"""Process-family supervision contracts for nested version probes."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from manifest_agent.checks.process import run_argv
from tools.project_checks import tool_versions

VERSION_ADAPTER = Path(__file__).parents[3] / "tools/project_checks/tool_versions.py"


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


def _assert_gone(pid: int) -> None:
    deadline = time.monotonic() + 1
    while _pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _pid_exists(pid)


def _assert_descendant_stopped(marker: Path) -> None:
    time.sleep(1.3)
    assert not marker.exists()


def _family_script(path: Path, *, parent_exits: bool) -> Path:
    script = path / "family.py"
    child_pid = path / "child.pid"
    parent_body = "raise SystemExit(0)" if parent_exits else "time.sleep(10)"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, sys, time\n"
        "from pathlib import Path\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"    Path({str(child_pid)!r}).write_text(str(os.getpid()))\n"
        "    os.close(sys.stdout.fileno())\n"
        "    os.close(sys.stderr.fileno())\n"
        "    time.sleep(10)\n"
        "else:\n"
        f"    while not Path({str(child_pid)!r}).exists(): time.sleep(0.001)\n"
        "    print('GNU bash, version 5.2.0', flush=True)\n"
        f"    {parent_body}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _failing_capture_script(path: Path) -> tuple[Path, Path, Path]:
    script = path / "capture-family.py"
    child_pid = path / "capture-child.pid"
    marker = path / "capture-continued"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, sys, time\n"
        "from pathlib import Path\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"    Path({str(child_pid)!r}).write_text(str(os.getpid()))\n"
        "    os.close(sys.stdout.fileno())\n"
        "    os.close(sys.stderr.fileno())\n"
        "    time.sleep(1.2)\n"
        f"    Path({str(marker)!r}).write_text('survived')\n"
        "else:\n"
        f"    while not Path({str(child_pid)!r}).exists(): time.sleep(0.001)\n"
        "    print('ready', flush=True)\n"
        "    raise SystemExit(0)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, child_pid, marker


def test_timeout_kills_descendant_after_term_makes_leader_exit(tmp_path: Path):
    script = _family_script(tmp_path, parent_exits=False)
    original = signal.getsignal(signal.SIGTERM)

    with pytest.raises(tool_versions.ProbeError, match="timed out"):
        tool_versions._run_probe([str(script)], timeout_seconds=0.3)

    assert signal.getsignal(signal.SIGTERM) == original
    _assert_gone(int((tmp_path / "child.pid").read_text()))


def test_successful_leader_cannot_leave_surviving_descendant(tmp_path: Path):
    script = _family_script(tmp_path, parent_exits=True)

    output = tool_versions._run_probe([str(script)], timeout_seconds=1)

    assert "GNU bash, version 5.2.0" in output
    _assert_gone(int((tmp_path / "child.pid").read_text()))


def test_selector_registration_failure_cleans_family_and_restores_signals(
    tmp_path: Path, monkeypatch
):
    script, child_pid, marker = _failing_capture_script(tmp_path)
    original = signal.getsignal(signal.SIGTERM)

    class RegistrationFailure:
        def register(self, *_args, **_kwargs):
            deadline = time.monotonic() + 0.5
            while not child_pid.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            raise OSError("registration failed")

        def close(self):
            return None

    monkeypatch.setattr(
        tool_versions._PROCESS.selectors, "DefaultSelector", RegistrationFailure
    )

    with pytest.raises(tool_versions.ProbeError, match="registration failed"):
        tool_versions._run_probe([str(script)], timeout_seconds=1)

    assert signal.getsignal(signal.SIGTERM) == original
    _assert_descendant_stopped(marker)


def test_read_failure_cleans_family_without_waiting_for_child(
    tmp_path: Path, monkeypatch
):
    script, child_pid, marker = _failing_capture_script(tmp_path)

    def fail_read(*_args, **_kwargs):
        deadline = time.monotonic() + 0.5
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        raise OSError("read failed")

    monkeypatch.setattr(tool_versions._PROCESS, "_read_chunk", fail_read)

    with pytest.raises(tool_versions.ProbeError, match="read failed"):
        tool_versions._run_probe([str(script)], timeout_seconds=1)

    _assert_descendant_stopped(marker)


def test_selector_close_failure_still_restores_handlers_and_cleans_family(
    tmp_path: Path, monkeypatch
):
    script, child_pid, marker = _failing_capture_script(tmp_path)
    original = signal.getsignal(signal.SIGTERM)
    real_selector = tool_versions._PROCESS.selectors.DefaultSelector

    class CloseFailure:
        def __init__(self):
            self._selector = real_selector()

        def __getattr__(self, name):
            return getattr(self._selector, name)

        def close(self):
            deadline = time.monotonic() + 0.5
            while not child_pid.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self._selector.close()
            raise OSError("close failed")

    monkeypatch.setattr(
        tool_versions._PROCESS.selectors, "DefaultSelector", CloseFailure
    )

    with pytest.raises(tool_versions.ProbeError, match="close failed"):
        tool_versions._run_probe([str(script)], timeout_seconds=0.2)

    assert signal.getsignal(signal.SIGTERM) == original
    _assert_descendant_stopped(marker)


def test_unsupported_supervision_blocks_before_launch(tmp_path: Path, monkeypatch):
    marker = tmp_path / "launched"
    script = tmp_path / "marker.py"
    script.write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    monkeypatch.setattr(tool_versions._PROCESS.os, "name", "nt")

    with pytest.raises(tool_versions.ProbeError, match="supervision is unsupported"):
        tool_versions._run_probe([sys.executable, str(script)])

    assert not marker.exists()


def test_outer_runner_timeout_cannot_orphan_inner_probe(tmp_path: Path):
    child_pid = tmp_path / "nested-child.pid"
    marker = tmp_path / "nested-child-survived"
    executable = tmp_path / "bash"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, time\n"
        "from pathlib import Path\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"    Path({str(child_pid)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(1.2)\n"
        f"    Path({str(marker)!r}).write_text('survived')\n"
        "else:\n"
        f"    while not Path({str(child_pid)!r}).exists(): time.sleep(0.001)\n"
        "    time.sleep(10)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    result = run_argv(
        (sys.executable, "-I", str(VERSION_ADAPTER), "command-version", "bash"),
        cwd=tmp_path,
        env={"PATH": str(tmp_path) + os.pathsep + os.defpath},
        timeout_seconds=0.4,
    )

    assert result.timed_out
    assert child_pid.exists()
    _assert_descendant_stopped(marker)


def test_internal_probe_deadline_precedes_outer_preflight_deadline():
    from manifest_agent.checks import runner

    assert (
        tool_versions._PROCESS.INTERNAL_TIMEOUT_SECONDS
        + 2 * tool_versions._PROCESS.CLEANUP_GRACE_SECONDS
        < runner.VERSION_PREFLIGHT_TIMEOUT_SECONDS
    )


def test_outer_runner_cancellation_cannot_orphan_inner_probe(tmp_path: Path):
    child_pid = tmp_path / "cancel-child.pid"
    marker = tmp_path / "cancel-child-survived"
    executable = tmp_path / "bash"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, time\n"
        "from pathlib import Path\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"    Path({str(child_pid)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(1.2)\n"
        f"    Path({str(marker)!r}).write_text('survived')\n"
        "else:\n"
        f"    while not Path({str(child_pid)!r}).exists(): time.sleep(0.001)\n"
        "    time.sleep(10)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "from manifest_agent.checks.process import run_argv\n"
        "try:\n"
        f"    run_argv((sys.executable, '-I', {str(VERSION_ADAPTER)!r}, "
        "'command-version', 'bash'), cwd=Path('.'), "
        "env={'PATH': str(Path('.').resolve()) + os.pathsep + os.defpath}, "
        "timeout_seconds=20)\n"
        "except KeyboardInterrupt:\n"
        "    Path('cancelled').write_text('yes')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(Path(__file__).parents[3] / "src"), *sys.path)
    )
    worker_process = subprocess.Popen(
        (sys.executable, str(worker)), cwd=tmp_path, env=environment
    )
    deadline = time.monotonic() + 3
    while not child_pid.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child_pid.exists()

    worker_process.send_signal(signal.SIGINT)
    worker_process.wait(timeout=3)

    assert (tmp_path / "cancelled").exists()
    _assert_descendant_stopped(marker)


def test_group_anchor_is_not_reaped_before_all_group_signals(monkeypatch):
    events = []

    class Process:
        pid = 777
        stdout = None
        stderr = None
        returncode = None

        def wait(self, timeout):
            events.append(("wait", timeout))
            self.returncode = 0
            return 0

        def terminate(self):
            events.append(("terminate", None))

        def kill(self):
            events.append(("kill", None))

    def record_signal(_process, signum):
        events.append(("signal", signum))
        return ""

    monkeypatch.setattr(tool_versions._PROCESS, "_signal_group", record_signal)
    tool_versions._PROCESS._cleanup_family(Process())

    assert events[:2] == [
        ("signal", signal.SIGTERM),
        ("signal", signal.SIGKILL),
    ]


def test_term_group_failure_does_not_reap_or_signal_leader_early(monkeypatch):
    events = []

    class Process:
        pid = 777
        stdout = None
        stderr = None
        returncode = None

        def wait(self, timeout):
            events.append(("wait", timeout))
            self.returncode = 0
            return 0

        def terminate(self):
            events.append(("terminate", None))

        def kill(self):
            events.append(("kill", None))

    errors = iter((PermissionError(errno.EPERM, "leader exited"), None))

    def record_signal(_process, signum):
        events.append(("signal", signum))
        return next(errors)

    monkeypatch.setattr(tool_versions._PROCESS, "_signal_group", record_signal)
    tool_versions._PROCESS._cleanup_family(Process(), leader_exited=True)

    assert events[:3] == [
        ("signal", signal.SIGTERM),
        ("signal", signal.SIGKILL),
        ("wait", tool_versions._PROCESS.CLEANUP_GRACE_SECONDS),
    ]


@pytest.mark.parametrize("capture_failure", ("timeout", "read-error"))
def test_capture_failure_observes_anchor_before_group_cleanup(
    monkeypatch, capture_failure: str
):
    waitid_calls = 0
    group_signals = []
    leader_calls = []

    class Process:
        pid = 777
        stdout = None
        stderr = None
        returncode = None

        def wait(self, timeout):
            leader_calls.append(("wait", timeout))
            return 0

        def terminate(self):
            leader_calls.append(("terminate", None))

        def kill(self):
            leader_calls.append(("kill", None))

    class Selector:
        def close(self):
            return None

    def lose_anchor(*_args):
        nonlocal waitid_calls
        waitid_calls += 1
        raise ChildProcessError

    def fail_capture(*_args):
        if capture_failure == "timeout":
            return b"", "version probe timed out"
        raise OSError("read failed")

    monkeypatch.setattr(tool_versions._PROCESS.os, "waitid", lose_anchor)
    monkeypatch.setattr(tool_versions._PROCESS.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(tool_versions._PROCESS, "_launch", lambda *_args: Process())
    monkeypatch.setattr(tool_versions._PROCESS, "_capture", fail_capture)
    monkeypatch.setattr(
        tool_versions._PROCESS,
        "_signal_group",
        lambda *_args: group_signals.append("unsafe"),
    )

    with pytest.raises(tool_versions.ProbeError):
        tool_versions._run_probe(["probe"], timeout_seconds=0.1)

    assert waitid_calls == 2
    assert group_signals == []
    assert not {"terminate", "kill"}.intersection(name for name, _ in leader_calls)


def test_cancellation_handlers_own_launch_window_and_ignore_repeats(
    tmp_path: Path, monkeypatch
):
    script = _family_script(tmp_path, parent_exits=False)
    installed = {}
    real_popen = tool_versions._PROCESS.subprocess.Popen

    def remember_handler(signum, handler):
        installed[signum] = handler

    def cancel_during_launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        assert signal.SIGTERM in installed
        installed[signal.SIGTERM](signal.SIGTERM, None)
        installed[signal.SIGINT](signal.SIGINT, None)
        return process

    monkeypatch.setattr(tool_versions._PROCESS.signal, "signal", remember_handler)
    monkeypatch.setattr(
        tool_versions._PROCESS.subprocess, "Popen", cancel_during_launch
    )

    with pytest.raises(tool_versions.ProbeError, match="interrupted by signal"):
        tool_versions._run_probe([str(script)], timeout_seconds=1)
