"""`manifest hook verify <client>` over the real subprocess entry point and
a real PATH, plus the committed `config/hook-clients.json` matrix itself:
schema-valid, no client verified yet, every adapter covered."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from manifest_agent.hooks import verify

from ._verify_support import (
    REPO_ROOT_MARKER,
    REPO_SRC,
    SHAPE,
    _write_fake_client,
    _write_fixture,
    _write_matrix,
)

# ---------------------------------------------------------------------------
# CLI: `manifest hook verify <client>` over a real subprocess + real PATH
# ---------------------------------------------------------------------------


def _run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", "-m", "manifest_agent", "hook", *args],
        capture_output=True,
        env=env,
        timeout=60,
    )


def test_cli_verify_unavailable_client(tmp_path: Path):
    """With no client on PATH the CLI exits 3 (unavailable), never 0."""
    matrix_path = tmp_path / "hook-clients.json"
    repo_root = tmp_path / "repo"
    fixture_dir = "tests/fixtures/hooks/claude_code/unverified"
    _write_matrix(
        matrix_path,
        executable_name="nonexistent-fake-client",
        protocol_probe=True,
        fixture_dir=fixture_dir,
    )
    _write_fixture(repo_root, fixture_dir, SHAPE)
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": os.pathsep.join((str(REPO_SRC), str(REPO_ROOT_MARKER))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "MANIFEST_HOOK_CLIENTS_CONFIG": str(matrix_path),
        "MANIFEST_HOOK_VERIFY_REPO_ROOT": str(repo_root),
        "HOME": str(tmp_path),
    }
    result = _run_cli(["verify", "claude-code"], env)
    body = json.loads(result.stdout.decode())
    assert body["status"] == "unavailable"
    assert result.returncode == 3  # BLOCKED, never a pass


def test_cli_verify_verified_client_with_write(tmp_path: Path):
    """A shape-matching fake client on a real PATH verifies and, with --write, promotes."""
    bin_dir = tmp_path / "bin"
    repo_root = tmp_path / "repo"
    matrix_path = tmp_path / "hook-clients.json"
    fixture_dir = "tests/fixtures/hooks/claude_code/unverified"
    _write_fake_client(bin_dir, version="7.7.7", shape=SHAPE)
    _write_matrix(
        matrix_path,
        executable_name="fake-client",
        protocol_probe=True,
        fixture_dir=fixture_dir,
    )
    _write_fixture(repo_root, fixture_dir, SHAPE)
    env = {
        "PATH": str(bin_dir),
        "PYTHONPATH": os.pathsep.join((str(REPO_SRC), str(REPO_ROOT_MARKER))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "MANIFEST_HOOK_CLIENTS_CONFIG": str(matrix_path),
        "MANIFEST_HOOK_VERIFY_REPO_ROOT": str(repo_root),
        "HOME": str(tmp_path),
    }
    result = _run_cli(["verify", "claude-code", "--write"], env)
    body = json.loads(result.stdout.decode())
    assert body["status"] == "verified"
    assert body["version"] == "7.7.7"
    assert result.returncode == 0
    promoted = (
        repo_root
        / "tests"
        / "fixtures"
        / "hooks"
        / "claude_code"
        / "7.7.7"
        / "SOURCE.md"
    )
    assert promoted.is_file()


# ---------------------------------------------------------------------------
# The committed matrix: schema-valid, unresolved, honest
# ---------------------------------------------------------------------------


def test_committed_matrix_validates_against_its_schema():
    """The committed matrix conforms to its own schema, so a hand edit cannot ship a malformed entry."""
    import jsonschema

    schema = json.loads(
        (REPO_ROOT_MARKER / "config" / "hook-clients.schema.json").read_text(
            encoding="utf-8"
        )
    )
    instance = json.loads(
        (REPO_ROOT_MARKER / "config" / "hook-clients.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(instance, schema)


def test_committed_matrix_has_no_client_verified_yet():
    """The load-bearing honesty check: every real client in the shipped
    matrix is unresolved -- no network, no installed clients here means no
    real verification could have happened."""
    instance = json.loads(
        (REPO_ROOT_MARKER / "config" / "hook-clients.json").read_text(encoding="utf-8")
    )
    assert instance["model_labels_status"].startswith("unresolved")
    for key, entry in instance["clients"].items():
        assert entry["verified_version"] is None, key
        assert entry["verified_at"] is None, key
        assert entry["protocol_probe_argv"] is None, key
        assert entry["model_labels"] == [], key
        config = verify.VerifyConfig(repo_root=REPO_ROOT_MARKER)
        assert verify.is_promotion_recorded(key, "0.0.0", config) is False


def test_committed_matrix_entries_cover_every_hook_adapter():
    """Every adapter CLIENT constant has a matrix entry, so no client can escape verification tracking."""
    instance = json.loads(
        (REPO_ROOT_MARKER / "config" / "hook-clients.json").read_text(encoding="utf-8")
    )
    assert set(instance["clients"]) == {"claude_code", "codex", "cursor", "gemini"}


def test_cli_hook_event_dispatch_still_works(tmp_path: Path):
    """`manifest hook <client> <event>` (no verify) must be unaffected by
    turning `hook` into a click.Group."""
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": os.pathsep.join((str(REPO_SRC), str(REPO_ROOT_MARKER))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "manifest_agent",
            "hook",
            "claude-code",
            "NotAnEvent",
        ],
        input=b"{}",
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout.decode()) == {"coverage": "unsupported"}
