"""Adversarial resource-bound tests for the thin version-probe adapter.

Spec amendment 2026-09-09 descopes process-family supervision and
RECORD/console provenance verification to Phase 4. What remains here is the
thin-probe security contract that must survive: an unrelated executable is
never substituted for the probed command, a hung probe is terminated (whole
process group) and reported BLOCKED rather than hanging or silently passing,
and a missing/unsupported tool is BLOCKED, never PASS.
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
VERSION_ADAPTER = ROOT / "tools/project_checks/tool_versions.py"


def _adapter(
    *argv: str, cwd: Path, path: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERSION_ADAPTER), *argv],
        cwd=cwd,
        env={
            "HOME": str(cwd),
            "PATH": path or os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


def test_command_probe_rejects_contained_unrelated_executable(tmp_path: Path):
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    fake = fake_dir / "bash"
    fake.write_text("#!/bin/sh\necho 'GNU bash, version 5.2.0'\n", encoding="utf-8")
    fake.chmod(0o755)

    result = _adapter(
        "command-version",
        "bash",
        "--executable",
        "fake/bash",
        cwd=tmp_path,
    )

    assert result.returncode == 3
    assert "does not match probe" in result.stderr


def test_missing_command_is_blocked_never_pass(tmp_path: Path):
    result = _adapter("command-version", "bash", cwd=tmp_path, path=str(tmp_path))

    assert result.returncode == 3
    assert "command is not installed" in result.stderr


def test_missing_distribution_is_blocked_never_pass(tmp_path: Path):
    result = _adapter("distribution-version", "not-a-real-distribution", cwd=tmp_path)

    assert result.returncode == 3
    assert "distribution is not installed" in result.stderr


def test_probe_timeout_terminates_and_reaps_process_family(tmp_path: Path):
    from tools.project_checks import tool_versions

    sleeper = tmp_path / "sleeper"
    child_pid = tmp_path / "timeout-child.pid"
    sleeper.write_text(
        f"#!{sys.executable}\n"
        "import os, time\n"
        "from pathlib import Path\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    time.sleep(10)\n"
        "else:\n"
        f"    Path({str(child_pid)!r}).write_text(str(child))\n"
        "    time.sleep(10)\n",
        encoding="utf-8",
    )
    sleeper.chmod(0o755)

    with pytest.raises(tool_versions.ProbeError, match="timed out"):
        tool_versions._run_probe([str(sleeper)], timeout_seconds=1.0)

    pid = int(child_pid.read_text())
    deadline = time.monotonic() + 1
    while _pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _pid_exists(pid)
