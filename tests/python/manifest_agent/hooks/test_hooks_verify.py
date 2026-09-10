"""`manifest hook verify <client>`: probe a fake client on a real PATH,
compare its protocol shape against a vendored fixture, and prove the single
most important property of C10 -- `client_version_verified: true` cannot
appear without a recorded, on-disk verification.

Every test below drives either the real subprocess entry point
(`hook_harness.invoke_verify`) against a real executable script placed on a
real PATH, or the real `verify.py` functions directly against real files on
disk. Nothing mocks `shutil.which`, `run_argv`, or the filesystem.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from manifest_agent.hooks import verify

REPO_SRC = Path(__file__).resolve().parents[4] / "src"
REPO_ROOT_MARKER = Path(__file__).resolve().parents[4]

_FAKE_CLIENT_TEMPLATE = Path(__file__).parent / "data" / "fake_client.py.tmpl"


def _write_fake_client(
    bin_dir: Path, *, version: str, shape: dict, name: str = "fake-client"
) -> Path:
    """Render the fake-client template -- a real, executable script placed on
    a real PATH; it is never invoked as anything but a subprocess."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    rendered = (
        _FAKE_CLIENT_TEMPLATE.read_text(encoding="utf-8")
        .replace("__PYTHON__", sys.executable)
        .replace("__VERSION__", version)
        .replace("__SHAPE_LITERAL__", repr(json.dumps(shape)))
    )
    script.write_text(rendered, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _write_matrix(
    path: Path, *, executable_name: str, protocol_probe: bool, fixture_dir: str
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_labels_status": "unresolved -- owner input required",
                "clients": {
                    "claude_code": {
                        "name": "claude_code",
                        "executable_candidates": [executable_name],
                        "version_argv": ["--version"],
                        "version_pattern": r"(\d+\.\d+\.\d+)",
                        "protocol_probe_argv": ["--emit-event"]
                        if protocol_probe
                        else None,
                        "verified_version": None,
                        "verified_at": None,
                        "fixture_dir": fixture_dir,
                        "model_labels": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _write_fixture(repo_root: Path, fixture_dir: str, shape: dict) -> None:
    directory = repo_root / fixture_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Sample.json").write_text(json.dumps(shape), encoding="utf-8")
    (directory / "SOURCE.md").write_text(
        "# fixture -- source: unverified\n", encoding="utf-8"
    )


SHAPE = {"session_id": "s", "tool_name": "Write", "tool_input": {"file_path": "a.txt"}}


@pytest.fixture
def verify_env(tmp_path: Path):
    """A real bin dir with a fake client on a real PATH, a real isolated
    repo_root, and a real matrix file -- everything `verify.VerifyConfig`
    needs, built fresh per test."""
    bin_dir = tmp_path / "bin"
    repo_root = tmp_path / "repo"
    matrix_path = tmp_path / "hook-clients.json"
    fixture_dir = "tests/fixtures/hooks/claude_code/unverified"
    return {
        "bin_dir": bin_dir,
        "repo_root": repo_root,
        "matrix_path": matrix_path,
        "fixture_dir": fixture_dir,
    }


def _config(env: dict, *, path_env: str | None = None) -> verify.VerifyConfig:
    return verify.VerifyConfig(
        repo_root=env["repo_root"],
        matrix_path=env["matrix_path"],
        resolver=verify.default_resolver(path_env or str(env["bin_dir"])),
        timeout_seconds=15.0,
    )


# ---------------------------------------------------------------------------
# unavailable: no executable on PATH -- BLOCKED, never a pass
# ---------------------------------------------------------------------------


def test_verify_unavailable_when_executable_missing(verify_env):
    env = verify_env
    _write_matrix(
        env["matrix_path"],
        executable_name="fake-client",
        protocol_probe=True,
        fixture_dir=env["fixture_dir"],
    )
    _write_fixture(env["repo_root"], env["fixture_dir"], SHAPE)
    # bin_dir intentionally left empty/nonexistent -- no executable placed.
    entries = verify.load_matrix(env["matrix_path"])
    result = verify.verify_client("claude_code", entries["claude_code"], _config(env))
    assert result.status == verify.STATUS_UNAVAILABLE
    assert result.executable is None
    assert result.version is None
    assert "no executable found" in result.reason


def test_verify_unavailable_when_no_protocol_probe_configured(verify_env):
    """Real clients ship `protocol_probe_argv: null` today -- verify must
    report unavailable, not silently skip the shape check."""
    env = verify_env
    _write_fake_client(env["bin_dir"], version="1.2.3", shape=SHAPE)
    _write_matrix(
        env["matrix_path"],
        executable_name="fake-client",
        protocol_probe=False,
        fixture_dir=env["fixture_dir"],
    )
    _write_fixture(env["repo_root"], env["fixture_dir"], SHAPE)
    entries = verify.load_matrix(env["matrix_path"])
    result = verify.verify_client("claude_code", entries["claude_code"], _config(env))
    assert result.status == verify.STATUS_UNAVAILABLE
    assert result.version == "1.2.3"
    assert "protocol_probe_argv" in result.reason


# ---------------------------------------------------------------------------
# verified: shape matches exactly
# ---------------------------------------------------------------------------


def test_verify_verified_when_shape_matches(verify_env):
    env = verify_env
    _write_fake_client(env["bin_dir"], version="2.5.1", shape=SHAPE)
    _write_matrix(
        env["matrix_path"],
        executable_name="fake-client",
        protocol_probe=True,
        fixture_dir=env["fixture_dir"],
    )
    _write_fixture(env["repo_root"], env["fixture_dir"], SHAPE)
    entries = verify.load_matrix(env["matrix_path"])
    result = verify.verify_client("claude_code", entries["claude_code"], _config(env))
    assert result.status == verify.STATUS_VERIFIED
    assert result.version == "2.5.1"
    assert result.differences == ()


# ---------------------------------------------------------------------------
# mismatch: precise field-level differences
# ---------------------------------------------------------------------------


def test_verify_mismatch_reports_differing_fields(verify_env):
    env = verify_env
    live_shape = {
        "session_id": "s",
        "tool_name": 7,
        "extra_field": True,
    }  # wrong type + missing + extra
    _write_fake_client(env["bin_dir"], version="3.0.0", shape=live_shape)
    _write_matrix(
        env["matrix_path"],
        executable_name="fake-client",
        protocol_probe=True,
        fixture_dir=env["fixture_dir"],
    )
    _write_fixture(env["repo_root"], env["fixture_dir"], SHAPE)
    entries = verify.load_matrix(env["matrix_path"])
    result = verify.verify_client("claude_code", entries["claude_code"], _config(env))
    assert result.status == verify.STATUS_MISMATCH
    joined = " | ".join(result.differences)
    assert "missing field 'tool_input'" in joined
    assert "'tool_name' expected type string, got number" in joined
    assert "unexpected field" in joined and "extra_field" in joined


# ---------------------------------------------------------------------------
# promotion: verified -> committed artifact -> reachable client_version_verified
# ---------------------------------------------------------------------------


def test_verify_promotion_writes_artifacts_and_updates_matrix(verify_env):
    env = verify_env
    _write_fake_client(env["bin_dir"], version="4.1.0", shape=SHAPE)
    _write_matrix(
        env["matrix_path"],
        executable_name="fake-client",
        protocol_probe=True,
        fixture_dir=env["fixture_dir"],
    )
    _write_fixture(env["repo_root"], env["fixture_dir"], SHAPE)
    config = _config(env)
    entries = verify.load_matrix(config.matrix_path)
    entry = entries["claude_code"]
    result = verify.verify_client("claude_code", entry, config)
    assert result.status == verify.STATUS_VERIFIED

    target = verify.promote(result, entry, config, now="2026-09-10T00:00:00Z")
    assert (
        target
        == env["repo_root"] / "tests" / "fixtures" / "hooks" / "claude_code" / "4.1.0"
    )
    assert (target / "Sample.json").is_file()
    source_md = (target / "SOURCE.md").read_text(encoding="utf-8")
    assert "verified" in source_md.splitlines()[0].lower()
    assert "unverified" not in source_md.splitlines()[0].lower()
    assert "claude_code" in source_md and "4.1.0" in source_md

    updated = json.loads(config.matrix_path.read_text(encoding="utf-8"))
    assert updated["clients"]["claude_code"]["verified_version"] == "4.1.0"
    assert updated["clients"]["claude_code"]["verified_at"] == "2026-09-10T00:00:00Z"

    # The critical positive: reachability now holds for exactly this pair.
    assert verify.is_promotion_recorded("claude_code", "4.1.0", config) is True


def test_promote_refuses_a_non_verified_result(verify_env):
    env = verify_env
    entries_config = _config(env)
    mismatch_result = verify.VerifyResult(
        verify.STATUS_MISMATCH, "claude_code", "/bin/fake", "1.0.0", differences=("x",)
    )
    entry = verify.ClientEntry(
        key="claude_code",
        name="claude_code",
        executable_candidates=("fake-client",),
        version_argv=("--version",),
        version_pattern=r"(\d+\.\d+\.\d+)",
        protocol_probe_argv=("--emit-event",),
        verified_version=None,
        verified_at=None,
        fixture_dir=env["fixture_dir"],
        model_labels=(),
    )
    with pytest.raises(ValueError):
        verify.promote(mismatch_result, entry, entries_config)


# ---------------------------------------------------------------------------
# THE critical negative: true is unreachable without a recorded verification
# ---------------------------------------------------------------------------


def test_client_version_verified_unreachable_without_recorded_verification(verify_env):
    env = verify_env
    config = _config(env)

    # 1. No matrix file at all.
    assert verify.is_promotion_recorded("claude_code", "1.0.0", config) is False

    # 2. Matrix exists but names no verified_version for this client.
    _write_matrix(
        env["matrix_path"],
        executable_name="fake-client",
        protocol_probe=True,
        fixture_dir=env["fixture_dir"],
    )
    assert verify.is_promotion_recorded("claude_code", "1.0.0", config) is False

    # 3. Matrix claims a verified_version, but no SOURCE.md backs it up
    #    (hand-edited matrix without ever running `verify`).
    data = json.loads(env["matrix_path"].read_text(encoding="utf-8"))
    data["clients"]["claude_code"]["verified_version"] = "9.9.9"
    data["clients"]["claude_code"]["verified_at"] = "2026-01-01T00:00:00Z"
    env["matrix_path"].write_text(json.dumps(data), encoding="utf-8")
    assert verify.is_promotion_recorded("claude_code", "9.9.9", config) is False

    # 4. A SOURCE.md exists at the claimed fixture_dir, but it is still the
    #    original "unverified" one -- the matrix claim alone is not evidence.
    _write_fixture(env["repo_root"], env["fixture_dir"], SHAPE)
    assert verify.is_promotion_recorded("claude_code", "9.9.9", config) is False

    # 5. A real promotion for a DIFFERENT version must not satisfy THIS version.
    _write_fake_client(env["bin_dir"], version="4.1.0", shape=SHAPE)
    entries = verify.load_matrix(env["matrix_path"])
    entry = entries["claude_code"]
    result = verify.verify_client("claude_code", entry, config)
    assert result.status == verify.STATUS_VERIFIED
    verify.promote(result, entry, config, now="2026-09-10T00:00:00Z")
    assert verify.is_promotion_recorded("claude_code", "9.9.9", config) is False
    assert verify.is_promotion_recorded("claude_code", "4.1.0", config) is True


def test_verified_result_for_one_client_cannot_promote_another(verify_env, tmp_path):
    env = verify_env
    _write_fake_client(env["bin_dir"], version="5.0.0", shape=SHAPE)
    _write_matrix(
        env["matrix_path"],
        executable_name="fake-client",
        protocol_probe=True,
        fixture_dir=env["fixture_dir"],
    )
    _write_fixture(env["repo_root"], env["fixture_dir"], SHAPE)
    # Add a second, independent matrix entry so promoting "codex" below has
    # somewhere real to write its own verified_version/verified_at.
    matrix_data = json.loads(env["matrix_path"].read_text(encoding="utf-8"))
    matrix_data["clients"]["codex"] = {
        "name": "codex",
        "executable_candidates": ["fake-client"],
        "version_argv": ["--version"],
        "version_pattern": r"(\d+\.\d+\.\d+)",
        "protocol_probe_argv": ["--emit-event"],
        "verified_version": None,
        "verified_at": None,
        "fixture_dir": "tests/fixtures/hooks/codex/unverified",
        "model_labels": [],
    }
    env["matrix_path"].write_text(json.dumps(matrix_data), encoding="utf-8")
    config = _config(env)
    entries = verify.load_matrix(config.matrix_path)
    entry = entries["claude_code"]
    result = verify.verify_client("claude_code", entry, config)
    assert result.status == verify.STATUS_VERIFIED
    verify.promote(result, entry, config, now="2026-09-10T00:00:00Z")

    # A different client, same version string, was never verified.
    assert verify.is_promotion_recorded("codex", "5.0.0", config) is False
    # Even a hand-crafted VerifyResult naming "codex" cannot promote it,
    # because promote() writes under entry.name -- and is_promotion_recorded
    # requires entry.name == the client key being checked.
    other_entry = verify.ClientEntry(
        key="codex",
        name="codex",
        executable_candidates=("fake-client",),
        version_argv=("--version",),
        version_pattern=r"(\d+\.\d+\.\d+)",
        protocol_probe_argv=("--emit-event",),
        verified_version=None,
        verified_at=None,
        fixture_dir="tests/fixtures/hooks/codex/unverified",
        model_labels=(),
    )
    fake_result = verify.VerifyResult(
        verify.STATUS_VERIFIED, "codex", "/bin/fake-client", "5.0.0"
    )
    verify.promote(fake_result, other_entry, config, now="2026-09-10T00:00:01Z")
    assert verify.is_promotion_recorded("codex", "5.0.0", config) is True
    assert verify.is_promotion_recorded("claude_code", "5.0.0", config) is True
    # But codex's own promotion never touched claude_code's matrix entry or
    # SOURCE.md -- they are independently recorded, not aliased.
    updated = json.loads(config.matrix_path.read_text(encoding="utf-8"))
    assert updated["clients"]["claude_code"]["verified_version"] == "5.0.0"
    codex_source = (
        env["repo_root"]
        / "tests"
        / "fixtures"
        / "hooks"
        / "codex"
        / "5.0.0"
        / "SOURCE.md"
    ).read_text()
    claude_source = (
        env["repo_root"]
        / "tests"
        / "fixtures"
        / "hooks"
        / "claude_code"
        / "5.0.0"
        / "SOURCE.md"
    ).read_text()
    assert "codex" in codex_source and "codex" not in claude_source.replace(
        "claude_code", ""
    )


# ---------------------------------------------------------------------------
# build_receipt: verified flag is reachable only through is_promotion_recorded
# ---------------------------------------------------------------------------


def test_build_receipt_never_verified_without_client_version(monkeypatch, verify_env):
    from manifest_agent.hooks.receipt import ReceiptInput, build_receipt

    info = ReceiptInput(
        client="claude_code",
        event="PostToolUse",
        profile="quick",
        status="PASS",
        digest="deadbeef",
        diagnostics="ok",
    )
    receipt = build_receipt(info)
    assert receipt["client_version_verified"] is False


def test_build_receipt_verified_only_with_recorded_promotion(monkeypatch, verify_env):
    from manifest_agent.hooks.receipt import ReceiptInput, build_receipt

    env = verify_env
    _write_fake_client(env["bin_dir"], version="6.0.0", shape=SHAPE)
    _write_matrix(
        env["matrix_path"],
        executable_name="fake-client",
        protocol_probe=True,
        fixture_dir=env["fixture_dir"],
    )
    _write_fixture(env["repo_root"], env["fixture_dir"], SHAPE)
    config = _config(env)
    entries = verify.load_matrix(config.matrix_path)
    entry = entries["claude_code"]
    result = verify.verify_client("claude_code", entry, config)
    verify.promote(result, entry, config, now="2026-09-10T00:00:00Z")

    monkeypatch.setattr("manifest_agent.hooks.verify.VerifyConfig", lambda: config)
    info = ReceiptInput(
        client="claude_code",
        event="PostToolUse",
        profile="quick",
        status="PASS",
        digest="deadbeef",
        diagnostics="ok",
    )
    receipt = build_receipt(info, client_version="6.0.0")
    assert receipt["client_version_verified"] is True
    receipt_wrong_version = build_receipt(info, client_version="1.2.3")
    assert receipt_wrong_version["client_version_verified"] is False


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
