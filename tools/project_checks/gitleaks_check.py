#!/usr/bin/env python3
"""Check body for ``hook.gitleaks``: scans the candidate's base..HEAD range.

Before this chunk (C6b) the registry ran gitleaks directly with
``--pre-commit --staged``, which reads the git *index*, not a commit range.
On a CI checkout (a clean clone, nothing staged) that scans zero commits and
reports PASS having looked at nothing -- a secret scanner that never runs is
worse than no scanner, because nobody investigates a pass. This body reads
the candidate's base revision (the same ``.git/candidate-base-sha`` sidecar
``debt_checks.py``/``analysis_checks.py`` already use) and scans exactly
``<base>..HEAD``; when no base revision is available the run is BLOCKED, not
PASS -- see docs/SHARED_CHECKS.md.

Exit: 0 PASS, 2 FAIL, 3 BLOCKED (this repo's ``honors_status_contract``).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PASS, FAIL, BLOCKED = 0, 2, 3


class BlockedError(RuntimeError):
    """A required executable or base revision is unavailable."""


def _context(root_argument: Path) -> Path:
    try:
        root = root_argument.expanduser().resolve(strict=True)
    except OSError as error:
        raise BlockedError(f"root unavailable: {error}") from error
    if not root.is_dir():
        raise BlockedError(f"root is not a directory: {root}")
    return root


def _base_sha(root: Path) -> str:
    sidecar = root / ".git" / "candidate-base-sha"
    try:
        base_sha = sidecar.read_text(encoding="utf-8").strip()
    except OSError:
        base_sha = ""
    if not base_sha:
        raise BlockedError("gitleaks: no base revision")
    return base_sha


def run(root: Path) -> int:
    executable = shutil.which("gitleaks")
    if executable is None:
        raise BlockedError("gitleaks is unavailable")
    base_sha = _base_sha(root)
    try:
        result = subprocess.run(
            (
                executable,
                "git",
                "--log-opts",
                f"{base_sha}..HEAD",
                "--redact",
                "--verbose",
            ),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"gitleaks unavailable: {error}") from error
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode == 0:
        return PASS
    if result.returncode == 1:
        return FAIL
    raise BlockedError(f"gitleaks exited {result.returncode} unexpectedly")


TASK7_DISPOSITIONS = {
    "hook.gitleaks": (
        ("python3", "tools/project_checks/gitleaks_check.py", "--root", "."),
        "project",
    ),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        root = _context(arguments.root)
        return run(root)
    except BlockedError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
