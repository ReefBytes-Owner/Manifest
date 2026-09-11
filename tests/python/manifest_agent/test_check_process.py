"""Real subprocess contracts for bounded check execution."""

from __future__ import annotations

import contextlib
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


def _write_cancellation_fixture(tmp_path: Path) -> None:
    """Write `worker.py` (runs `checker.py` under `run_argv`) and
    `checker.py` (a forking process family) into `tmp_path`.

    Both `checker.py` processes carry a 600s budget so the fixture never
    finishes before SIGINT under load -- but as short, bounded polls (not
    one blind `time.sleep(600)`): each re-checks its own parent pid every
    0.1s and exits the moment it is orphaned (SIGTERM handling failed, or
    the ancestor above it died outright). Worst case is one 0.1s step late,
    never minutes (Correction 14 / C7o rule 2) -- a sleeping grandchild must
    never be able to hold the whole test.python budget hostage if the
    SIGTERM path misfires.
    """
    (tmp_path / "checker.py").write_text(
        "import os, signal, sys, time\n"
        "from pathlib import Path\n"
        "BUDGET, STEP = 600, 0.1\n"
        "def survive_while_parent_alive(expected_ppid):\n"
        "    deadline = time.monotonic() + BUDGET\n"
        "    while time.monotonic() < deadline and os.getppid() == expected_ppid:\n"
        "        time.sleep(STEP)\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    Path('child.pid').write_text(str(os.getpid()))\n"
        "    leader_pid = os.getppid()\n"
        "    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "    survive_while_parent_alive(leader_pid)\n"
        "else:\n"
        "    Path('parent.pid').write_text(str(os.getpid()))\n"
        "    worker_pid = os.getppid()\n"
        "    def stop(*_):\n"
        "        os.waitpid(child, 0)\n"
        "        raise SystemExit(0)\n"
        "    signal.signal(signal.SIGTERM, stop)\n"
        "    survive_while_parent_alive(worker_pid)\n"
    )
    (tmp_path / "worker.py").write_text(
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


def _spawn_cancellation_worker(tmp_path: Path) -> subprocess.Popen:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(Path(__file__).parents[3] / "src"), *sys.path)
    )
    return subprocess.Popen(
        (sys.executable, str(tmp_path / "worker.py")),
        cwd=tmp_path,
        env=environment,
        # Correction 15 rule 4: `manifest check` bodies run under the
        # runner with SIGINT ignored (SIG_IGN, inherited from the
        # start_new_session'd process that launched the whole check tree --
        # ignored dispositions, unlike caught ones, survive exec). This
        # fixture's own `process.send_signal(SIGINT)` below needs the
        # *worker* to actually raise KeyboardInterrupt regardless of what
        # its own ancestry did, so reset SIGINT to its default disposition
        # in the worker itself rather than depend on the ambient one.
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal cancellation fixture")
def test_cancellation_reaps_process_family_and_emits_no_success_receipt(tmp_path):
    """SIGINT to the worker must reap the whole checker process family (the
    forked grandchild included) and never leave a success receipt behind."""
    _write_cancellation_fixture(tmp_path)
    process = _spawn_cancellation_worker(tmp_path)
    parent_pid = child_pid = None
    try:
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
        # 30s = ~3x the 10.05s SIGINT-to-reap measured under full-suite load
        # (2026-09-11).
        assert process.wait(timeout=30) == 0

        assert (tmp_path / "cancelled").read_text() == "yes"
        assert not (tmp_path / "success-receipt").exists()
        assert not _pid_exists(parent_pid)
        assert not _pid_exists(child_pid)
    finally:
        # Reap on every exit path (Correction 15 rule 4), not only the
        # happy path above: an assertion failure or timeout before this
        # point must never leave the worker, `checker.py` (its own
        # process-group leader, spawned via run_argv's start_new_session),
        # or the forked grandchild (a bounded 600s poller -- C7o rule 2)
        # running past the test.
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for pid in (parent_pid, child_pid):
            if pid is None:
                continue
            # Already reaped by the happy path or the worker's own SIGTERM
            # handler above -- ESRCH here means cleanup, not a leak.
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


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


def test_failure_trailer_names_an_early_line_in_a_one_megabyte_stream(tmp_path):
    """Correction 16 rule 1: the trailer must be built WHILE STREAMING.

    The failure line is the very first thing written; a 1 MB stream after it
    would push it out of the tail-bounded capture window entirely if the
    trailer were built from the already-truncated result (C7p's defect).
    """
    from manifest_agent.checks.process import run_argv

    script = tmp_path / "early-failure.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('not ok 1 something broke\\n')\n"
        "sys.stdout.write('x' * (1024 * 1024))\n"
        "sys.stdout.write('\\n')\n"
    )

    # subprocess-env: exempt -- synthetic tmp_path script, no manifest_agent/
    # tools import; proves run_argv's own explicit-only env contract.
    result = run_argv(
        (sys.executable, str(script)),
        cwd=tmp_path,
        env={},
        timeout_seconds=10,
        failure_line_regex="^not ok ",
    )

    assert result.returncode == 0
    assert result.stdout.rstrip().endswith(
        "# failure summary: 1\nnot ok 1 something broke"
    )


def test_failure_trailer_is_zero_when_nothing_matches(tmp_path):
    from manifest_agent.checks.process import run_argv

    script = tmp_path / "all-ok.py"
    script.write_text("import sys\nsys.stdout.write('ok 1 fine\\nok 2 fine\\n')\n")

    # subprocess-env: exempt -- synthetic tmp_path script, no manifest_agent/
    # tools import; proves run_argv's own explicit-only env contract.
    result = run_argv(
        (sys.executable, str(script)),
        cwd=tmp_path,
        env={},
        timeout_seconds=5,
        failure_line_regex="^not ok ",
    )

    assert result.returncode == 0
    assert result.stdout.rstrip().endswith("# failure summary: 0")


def test_no_trailer_when_check_declares_no_failure_line_regex(tmp_path):
    from manifest_agent.checks.process import run_argv

    script = tmp_path / "untracked.py"
    script.write_text("import sys\nsys.stdout.write('not ok 1 whatever\\n')\n")

    # subprocess-env: exempt -- synthetic tmp_path script, no manifest_agent/
    # tools import; proves run_argv's own explicit-only env contract.
    result = run_argv(
        (sys.executable, str(script)), cwd=tmp_path, env={}, timeout_seconds=5
    )

    assert result.returncode == 0
    assert "# failure summary" not in result.stdout


def test_failure_trailer_reports_multiple_matches_in_order(tmp_path):
    from manifest_agent.checks.process import run_argv

    script = tmp_path / "multi-failure.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('not ok 1 a\\n')\n"
        "sys.stdout.write('ok 2 b\\n')\n"
        "sys.stdout.write('not ok 3 c\\n')\n"
    )

    # subprocess-env: exempt -- synthetic tmp_path script, no manifest_agent/
    # tools import; proves run_argv's own explicit-only env contract.
    result = run_argv(
        (sys.executable, str(script)),
        cwd=tmp_path,
        env={},
        timeout_seconds=5,
        failure_line_regex="^not ok ",
    )

    assert result.stdout.rstrip().splitlines()[-3:] == [
        "# failure summary: 2",
        "not ok 1 a",
        "not ok 3 c",
    ]


def test_timed_out_run_gets_no_failure_trailer(tmp_path):
    """A timed-out run has no complete stream to summarize honestly."""
    from manifest_agent.checks.process import run_argv

    script = tmp_path / "hangs.py"
    script.write_text(
        "import sys, time\n"
        "sys.stdout.write('not ok 1 hung\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(10)\n"
    )

    # subprocess-env: exempt -- synthetic tmp_path script, no manifest_agent/
    # tools import; proves run_argv's own explicit-only env contract.
    result = run_argv(
        (sys.executable, str(script)),
        cwd=tmp_path,
        env={},
        timeout_seconds=0.3,
        failure_line_regex="^not ok ",
    )

    assert result.timed_out
    assert "# failure summary" not in result.stdout
