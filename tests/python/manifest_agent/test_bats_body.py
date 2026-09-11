"""Behavior tests for the bats not-ok trailer body (Correction 15 rule 3).

A fake `bats` on PATH stands in for the real binary so these tests never
need the toolchain store: `bats_body.py` only cares that SOME executable
named `bats` answers on PATH, exactly like the real one resolved there by
the runner's own `store:` verification.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BODY = ROOT / "tools/project_checks/bats_body.py"


def _fake_bats(tmp_path: Path, script: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "bats"
    fake.write_text(f"#!/bin/sh\n{script}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run(tmp_path: Path, bin_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BODY), *args],
        cwd=tmp_path,
        env={"PATH": str(bin_dir)},
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_all_passing_stream_unchanged_with_a_zero_trailer(tmp_path):
    bin_dir = _fake_bats(
        tmp_path,
        "printf '1..2\\nok 1 first\\nok 2 second\\n'",
    )
    result = _run(tmp_path, bin_dir, "tests/fake/")

    assert result.returncode == 0
    assert result.stdout == ("1..2\nok 1 first\nok 2 second\n# not ok summary: 0\n")
    assert result.stderr == ""


def test_one_failure_is_named_in_the_trailer_even_if_the_head_is_truncated(tmp_path):
    bin_dir = _fake_bats(
        tmp_path,
        "printf '1..3\\nok 1 first\\nnot ok 2 second\\nok 3 third\\n'; exit 1",
    )
    result = _run(tmp_path, bin_dir, "tests/fake/")

    assert result.returncode == 1
    assert "not ok 2 second" in result.stdout
    lines = result.stdout.splitlines()
    assert lines[-2] == "# not ok summary: 1"
    assert lines[-1] == "not ok 2 second"


def test_multiple_failures_are_all_named_in_declaration_order(tmp_path):
    bin_dir = _fake_bats(
        tmp_path,
        "printf '1..4\\nok 1 a\\nnot ok 2 b\\nok 3 c\\nnot ok 4 d\\n'; exit 1",
    )
    result = _run(tmp_path, bin_dir, "tests/fake/")

    lines = result.stdout.splitlines()
    assert lines[-3:] == ["# not ok summary: 2", "not ok 2 b", "not ok 4 d"]


def test_missing_bats_on_path_exits_2_with_a_clear_message(tmp_path):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    result = _run(tmp_path, empty_bin, "tests/fake/")

    assert result.returncode == 2
    assert "no `bats` on PATH" in result.stderr


def test_help_exits_0_before_touching_bats():
    result = subprocess.run(
        [sys.executable, str(BODY), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": ""},
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
