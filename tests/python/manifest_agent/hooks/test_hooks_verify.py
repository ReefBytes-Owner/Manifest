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
from pathlib import Path

import pytest

from manifest_agent.hooks import verify

from ._verify_support import (
    SHAPE,
    _config,
    _write_fake_client,
    _write_fixture,
    _write_matrix,
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


def _second_client_entry() -> verify.ClientEntry:
    return verify.ClientEntry(
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


def _add_second_matrix_client(matrix_path: Path) -> None:
    """Add an independent `codex` entry so promoting it has somewhere real
    to write its own verified_version/verified_at."""
    matrix_data = json.loads(matrix_path.read_text(encoding="utf-8"))
    entry = _second_client_entry()
    matrix_data["clients"]["codex"] = {
        "name": entry.name,
        "executable_candidates": list(entry.executable_candidates),
        "version_argv": list(entry.version_argv),
        "version_pattern": entry.version_pattern,
        "protocol_probe_argv": list(entry.protocol_probe_argv),
        "verified_version": None,
        "verified_at": None,
        "fixture_dir": entry.fixture_dir,
        "model_labels": [],
    }
    matrix_path.write_text(json.dumps(matrix_data), encoding="utf-8")


def _source_md(repo_root: Path, client: str, version: str) -> str:
    return (
        repo_root / "tests" / "fixtures" / "hooks" / client / version / "SOURCE.md"
    ).read_text()


def test_verified_result_for_one_client_cannot_promote_another(verify_env):
    """Promotion is recorded per client key: a verified result for one client
    never marks another client (even at the same version) as verified."""
    env = verify_env
    _write_fake_client(env["bin_dir"], version="5.0.0", shape=SHAPE)
    _write_matrix(
        env["matrix_path"],
        executable_name="fake-client",
        protocol_probe=True,
        fixture_dir=env["fixture_dir"],
    )
    _write_fixture(env["repo_root"], env["fixture_dir"], SHAPE)
    _add_second_matrix_client(env["matrix_path"])
    config = _config(env)
    entry = verify.load_matrix(config.matrix_path)["claude_code"]
    result = verify.verify_client("claude_code", entry, config)
    assert result.status == verify.STATUS_VERIFIED
    verify.promote(result, entry, config, now="2026-09-10T00:00:00Z")

    # A different client, same version string, was never verified.
    assert verify.is_promotion_recorded("codex", "5.0.0", config) is False
    # Even a hand-crafted VerifyResult naming "codex" cannot promote it,
    # because promote() writes under entry.name -- and is_promotion_recorded
    # requires entry.name == the client key being checked.
    fake_result = verify.VerifyResult(
        verify.STATUS_VERIFIED, "codex", "/bin/fake-client", "5.0.0"
    )
    verify.promote(
        fake_result, _second_client_entry(), config, now="2026-09-10T00:00:01Z"
    )
    assert verify.is_promotion_recorded("codex", "5.0.0", config) is True
    assert verify.is_promotion_recorded("claude_code", "5.0.0", config) is True
    # But codex's own promotion never touched claude_code's matrix entry or
    # SOURCE.md -- they are independently recorded, not aliased.
    updated = json.loads(config.matrix_path.read_text(encoding="utf-8"))
    assert updated["clients"]["claude_code"]["verified_version"] == "5.0.0"
    codex_source = _source_md(env["repo_root"], "codex", "5.0.0")
    claude_source = _source_md(env["repo_root"], "claude_code", "5.0.0")
    assert "codex" in codex_source
    assert "codex" not in claude_source.replace("claude_code", "")


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
