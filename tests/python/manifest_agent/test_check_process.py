"""Real subprocess contracts for bounded check execution."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


def test_capture_is_redacted_bounded_and_marks_each_truncated_stream(tmp_path):
    from manifest_agent.checks.process import CAPTURE_LIMIT, run_argv

    # The secret lands at the END of each stream: capture keeps the tail
    # (test-runner summaries live there too), so redaction must still fire
    # on bytes that survive truncation, not just on ones that get dropped.
    script = tmp_path / "excess.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('x' * 100000 + 'password=fixture-secret \\n')\n"
        "sys.stderr.write('y' * 100000 + 'api_key=fixture-secret \\n')\n"
    )

    # subprocess-env: exempt -- proves run_argv's own explicit-only env
    # contract (a synthetic tmp_path script, no manifest_agent/tools import).
    result = run_argv(
        (sys.executable, str(script)), cwd=tmp_path, env={}, timeout_seconds=2
    )

    assert result.returncode == 0
    assert "fixture-secret" not in result.stdout + result.stderr
    assert "[REDACTED]" in result.stdout + result.stderr
    assert result.stdout.startswith("[head truncated: ")
    assert result.stderr.startswith("[head truncated: ")
    assert result.stdout.endswith("[REDACTED] \n")
    assert result.stderr.endswith("[REDACTED] \n")
    assert len(result.stdout.encode()) <= CAPTURE_LIMIT
    assert len(result.stderr.encode()) <= CAPTURE_LIMIT


def test_capture_keeps_a_trailing_test_summary_line(tmp_path):
    """A 200KB+ stream keeps its trailing pytest/bats-shaped summary line.

    Test-runner summaries land at the very end of output; a capture that
    truncates the tail instead of the head silently discards the one line a
    receipt reader or a re-run decision actually needs.
    """
    from manifest_agent.checks.process import CAPTURE_LIMIT, run_argv

    script = tmp_path / "excess-summary.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('x' * 200000)\n"
        "sys.stdout.write('\\nFAILED tests/fake.py::test_thing - AssertionError\\n')\n"
    )

    # subprocess-env: exempt -- proves run_argv's own explicit-only env
    # contract (a synthetic tmp_path script, no manifest_agent/tools import).
    result = run_argv(
        (sys.executable, str(script)), cwd=tmp_path, env={}, timeout_seconds=2
    )

    assert result.returncode == 0
    assert result.stdout.startswith("[head truncated: ")
    assert "FAILED tests/fake.py::test_thing - AssertionError" in result.stdout
    assert len(result.stdout.encode()) <= CAPTURE_LIMIT


def test_timeout_kills_parent_and_sleeping_child(tmp_path):
    from manifest_agent.checks.process import run_argv

    script = tmp_path / "sleeping-family.py"
    script.write_text(
        "import os, time\n"
        "from pathlib import Path\n"
        "Path('parent.pid').write_text(str(os.getpid()))\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    Path('child.pid').write_text(str(os.getpid()))\n"
        "    time.sleep(10)\n"
        "else:\n"
        "    time.sleep(10)\n"
    )

    # subprocess-env: exempt -- synthetic tmp_path script, no
    # manifest_agent/tools import.
    result = run_argv(
        (sys.executable, str(script)), cwd=tmp_path, env={}, timeout_seconds=0.2
    )

    assert result.timed_out
    assert result.returncode is not None
    assert result.duration_seconds < 2
    assert not _pid_exists(int((tmp_path / "parent.pid").read_text()))
    child_pid = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 1
    while _pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _pid_exists(child_pid)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal cancellation fixture")
def test_cancellation_reaps_process_family_and_emits_no_success_receipt(tmp_path):
    worker = tmp_path / "worker.py"
    checker = tmp_path / "checker.py"
    # 600s: under load the fixture must never finish "on time" (measured
    # 10.05s to signal+reap under real contention below), or it races
    # cancellation instead of proving it.
    checker.write_text(
        "import os, signal, sys, time\n"
        "from pathlib import Path\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    Path('child.pid').write_text(str(os.getpid()))\n"
        "    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "    time.sleep(600)\n"
        "else:\n"
        "    Path('parent.pid').write_text(str(os.getpid()))\n"
        "    def stop(*_):\n"
        "        os.waitpid(child, 0)\n"
        "        raise SystemExit(0)\n"
        "    signal.signal(signal.SIGTERM, stop)\n"
        "    time.sleep(600)\n"
    )
    worker.write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "from manifest_agent.checks.process import run_argv\n"
        "try:\n"
        "    run_argv((sys.executable, 'checker.py'), cwd=Path('.'), env={'PATH': os.defpath}, timeout_seconds=120)\n"
        "except KeyboardInterrupt:\n"
        "    Path('cancelled').write_text('yes')\n"
        "else:\n"
        "    Path('success-receipt').write_text('wrong')\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(Path(__file__).parents[3] / "src"), *sys.path)
    )
    process = subprocess.Popen(
        (sys.executable, str(worker)), cwd=tmp_path, env=environment
    )
    deadline = time.monotonic() + 3
    while (
        not all((tmp_path / name).exists() for name in ("parent.pid", "child.pid"))
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    parent_pid = int((tmp_path / "parent.pid").read_text())
    child_pid = int((tmp_path / "child.pid").read_text())
    assert parent_pid != child_pid

    process.send_signal(signal.SIGINT)
    # Budget measured, not guessed: a real run of the full suite under a
    # store-provisioned interpreter (2026-09-11, 16-core host, ~3650
    # concurrent-load tests) needed 10.05s from SIGINT to reap; 30s leaves
    # ~3x headroom under that measured contention.
    assert process.wait(timeout=30) == 0

    assert (tmp_path / "cancelled").read_text() == "yes"
    assert not (tmp_path / "success-receipt").exists()
    assert not _pid_exists(parent_pid)
    assert not _pid_exists(child_pid)


def test_unsupported_process_group_lifecycle_is_observable_and_does_not_spawn(
    tmp_path, monkeypatch
):
    import manifest_agent.checks.process as implementation

    marker = tmp_path / "spawned"
    monkeypatch.setattr(implementation.os, "name", "nt")

    # subprocess-env: exempt -- windows-mocked path never actually spawns
    # (os.name patched to "nt" before run_argv's POSIX guard returns early).
    result = implementation.run_argv(
        (sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"),
        cwd=tmp_path,
        env={},
        timeout_seconds=1,
    )

    assert result.returncode is None
    assert "process-group lifecycle" in result.error
    assert not marker.exists()


def test_child_receives_only_explicit_environment_and_literal_argv(
    tmp_path, monkeypatch
):
    from manifest_agent.checks.process import run_argv

    monkeypatch.setenv("AMBIENT_CHECK_SECRET", "must-not-expand-or-inherit")
    script = tmp_path / "environment.py"
    script.write_text(
        "import os, sys\n"
        "print(os.environ.get('AMBIENT_CHECK_SECRET'))\n"
        "print(os.environ['DECLARED_CHECK_VALUE'])\n"
        "print(sys.argv[1])\n"
    )

    # subprocess-env: exempt -- proves run_argv's env is explicit-only (no
    # ambient inheritance); adding isolation keys here would blur the
    # contract this test pins.
    result = run_argv(
        (sys.executable, str(script), "$AMBIENT_CHECK_SECRET"),
        cwd=tmp_path,
        env={"DECLARED_CHECK_VALUE": "present"},
        timeout_seconds=2,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "None",
        "present",
        "$AMBIENT_CHECK_SECRET",
    ]
