"""Fixture tests for `tools.project_checks.gitleaks_check` (C6b).

Before this chunk, `hook.gitleaks`'s argv was ``gitleaks git --pre-commit
--redact --staged --verbose`` -- on a CI checkout (clean clone, nothing
staged) that scans the git *index*, which is empty, and PASSes having
looked at nothing. `test_staged_argv_scans_nothing_on_a_clean_checkout`
below reproduces that shape directly against the old argv to prove it,
before the remaining tests pin the fixed body's `base..HEAD` behavior.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.project_checks import gitleaks_check

_ENV = {
    "PATH": os.defpath,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        (
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(root),
            *args,
        ),
        env=_ENV,
        capture_output=True,
        check=True,
    ).stdout


# Assembled at runtime, never a single matchable literal in this file's own
# source -- this repo's own `hook.gitleaks`/`security.semgrep` scans run
# against THIS repo's history, not the throwaway fixture repo below, and a
# secret-shaped literal sitting in this test's committed source would be a
# real finding on this repo's own scan, not just the fixture's.
_AWS_KEY_ID = "AKIA" + "ABCDEFGHIJKLMNOP"


@pytest.fixture
def repo_with_committed_secret(tmp_path: Path) -> tuple[Path, str]:
    """A clean checkout (nothing staged) whose HEAD commit, past the base
    revision, introduces an AWS-access-key-shaped string -- gitleaks' own
    default ruleset flags it without needing this repo's `.gitleaks.toml`."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "README.md").write_text("base\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    base_sha = _git(root, "rev-parse", "HEAD").decode().strip()
    (root / "config.env").write_text(f"AWS_ACCESS_KEY_ID={_AWS_KEY_ID}\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "add secret")
    return root, base_sha


def test_staged_argv_scans_nothing_on_a_clean_checkout(repo_with_committed_secret):
    """Proves the pre-C6b false green directly: the OLD argv scans the
    (empty) git index on a clean checkout and exits 0 despite a committed
    secret sitting right there in `base..HEAD`."""
    root, _base_sha = repo_with_committed_secret
    executable = shutil.which("gitleaks")
    if executable is None:
        pytest.skip("gitleaks is not installed")

    result = subprocess.run(
        (executable, "git", "--pre-commit", "--redact", "--staged", "--verbose"),
        cwd=root,
        env=_ENV,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_run_fails_on_a_secret_introduced_since_base(repo_with_committed_secret):
    root, base_sha = repo_with_committed_secret
    (root / ".git" / "candidate-base-sha").write_text(base_sha + "\n")

    status = gitleaks_check.run(root)

    assert status == gitleaks_check.FAIL


def test_run_passes_when_base_equals_head(repo_with_committed_secret):
    root, _base_sha = repo_with_committed_secret
    head_sha = _git(root, "rev-parse", "HEAD").decode().strip()
    (root / ".git" / "candidate-base-sha").write_text(head_sha + "\n")

    status = gitleaks_check.run(root)

    assert status == gitleaks_check.PASS


def test_run_blocks_without_a_base_revision(repo_with_committed_secret, capsys):
    root, _base_sha = repo_with_committed_secret

    with pytest.raises(gitleaks_check.BlockedError, match="no base revision"):
        gitleaks_check.run(root)


def test_main_blocks_without_a_base_revision(repo_with_committed_secret, capsys):
    root, _base_sha = repo_with_committed_secret

    exit_code = gitleaks_check.main(["--root", str(root)])

    assert exit_code == gitleaks_check.BLOCKED
    assert "gitleaks: no base revision" in capsys.readouterr().err
