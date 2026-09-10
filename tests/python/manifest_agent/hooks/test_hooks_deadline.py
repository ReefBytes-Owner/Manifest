"""The adapter's deadline is `checks.process.run_argv`'s real process-group
kill — the exact function `hooks/runner.py::run_manifest_check` calls, not a
reimplementation. This proves that mechanism kills a grandchild that
outlives its own parent, using real subprocesses (no mocking of `os.killpg`,
`subprocess`, or time)."""

from __future__ import annotations

import errno
import os
import sys
import time


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


def test_deadline_kills_a_grandchild_that_outlives_its_parent(tmp_path):
    from manifest_agent.checks.process import run_argv

    grandchild_marker = tmp_path / "grandchild-ran"
    pid_file = tmp_path / "pids.json"
    script = tmp_path / "spawn_and_block.py"
    script.write_text(
        "import json, os, subprocess, sys, time\n"
        "grandchild = subprocess.Popen([sys.executable, '-c',\n"
        "    'import pathlib,time; time.sleep(6); "
        "pathlib.Path(%r).write_text(\"done\")' % sys.argv[1]])\n"
        "with open(sys.argv[2], 'w') as fh:\n"
        "    json.dump({'self': os.getpid(), 'grandchild': grandchild.pid}, fh)\n"
        "time.sleep(20)\n",
        encoding="utf-8",
    )

    start = time.monotonic()
    result = run_argv(
        (sys.executable, str(script), str(grandchild_marker), str(pid_file)),
        cwd=tmp_path,
        env={"PATH": os.defpath},
        timeout_seconds=1.5,
    )
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert elapsed < 6, "run_argv must return at its deadline, not wait for the grandchild"

    import json

    for _ in range(50):
        if pid_file.exists():
            break
        time.sleep(0.05)
    pids = json.loads(pid_file.read_text(encoding="utf-8"))

    # Give the killed grandchild the time it would have needed to finish its
    # sleep-then-write if it had survived; the marker must never appear.
    time.sleep(6)
    assert not grandchild_marker.exists(), "grandchild survived the deadline kill"
    assert not _pid_exists(pids["self"])
    assert not _pid_exists(pids["grandchild"])
