"""End-to-end smoke tests for tools/project_checks/debt_checks.py: the
project-check body wiring around ``manifest_agent.checks.debt`` -- real git,
real subprocess, the status contract (0/2/3), and the propose-baseline
containment guarantee from the CLI surface a reviewer actually runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools/project_checks/debt_checks.py"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _init_repo(root: Path) -> str:
    """A minimal real git repo carrying just enough of the real tree
    (the bundle-link scanner + an empty plugins/) for debt_checks.py's
    scanner subprocesses to run against, without paying for a full clone.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "tools").mkdir()
    shutil.copy(REPO_ROOT / "tools/check_bundle_link_references.py", root / "tools")
    shutil.copy(REPO_ROOT / "tools/bundle_link_baseline.py", root / "tools")
    (root / "plugins").mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "debt-baseline.json").write_text(
        json.dumps({"version": 2, "entries": []})
    )
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "base")
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _run(root: Path, check_id: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), check_id, "--root", str(root), *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_base_unavailable_is_blocked(tmp_path: Path):
    root = tmp_path / "repo"
    _init_repo(root)
    result = _run(root, "debt.constitution", "--base-sha", "0" * 40)
    assert result.returncode == 3
    assert "BLOCKED" in result.stderr


def test_clean_repo_with_no_new_debt_passes(tmp_path: Path):
    root = tmp_path / "repo"
    base_sha = _init_repo(root)
    result = _run(root, "debt.bundle-links", "--base-sha", base_sha)
    assert result.returncode == 0, result.stderr


def test_propose_baseline_refuses_to_write_inside_the_repo(tmp_path: Path):
    root = tmp_path / "repo"
    base_sha = _init_repo(root)
    inside = root / "config" / "debt-baseline.json"
    result = _run(
        root,
        "debt.bundle-links",
        "--base-sha",
        base_sha,
        "--propose-baseline",
        "--output",
        str(inside),
    )
    assert result.returncode == 3
    assert "outside the source tree" in result.stderr
    # and the committed file is provably untouched
    assert json.loads(inside.read_text()) == {"version": 2, "entries": []}


def test_propose_baseline_writes_a_reviewable_file_outside_the_repo(tmp_path: Path):
    root = tmp_path / "repo"
    base_sha = _init_repo(root)
    outside = tmp_path / "review" / "proposal.json"
    result = _run(
        root,
        "debt.bundle-links",
        "--base-sha",
        base_sha,
        "--propose-baseline",
        "--output",
        str(outside),
    )
    assert result.returncode == 0, result.stderr
    assert outside.is_file()
    payload = json.loads(outside.read_text())
    assert payload["version"] == 2
