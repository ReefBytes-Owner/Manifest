"""Receipt schema v2: provenance digests must invalidate stale evidence.

phase-3-5-decisions.md 3e names five required tests; each is here. Receipts
are never a reason to skip execution (`run_profile` has no success cache in
Phase 3) -- these tests only prove that *evidence* correctly stops being
trustworthy the moment any of its provenance components changes, and that
`aggregate_results` rejects the two documented cross-receipt conditions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from manifest_agent.checks import receipt as _receipt
from manifest_agent.checks import run_profile
from manifest_agent.checks.aggregate import aggregate_results
from manifest_agent.checks.candidate import candidate_digest
from manifest_agent.checks.models import CheckSpec
from tests.python.manifest_agent.aggregate_fixtures import (
    clean_pair,
    context,
    job,
    load_two_group_registry,
    result,
)
from tests.python.manifest_agent.aggregate_fixtures import (
    receipt as receipt_fixture,
)
from tests.python.manifest_agent.check_registry_fixtures import (
    _registry_file as _registry_file,
)
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture


def _key(**overrides: str | None) -> str:
    base = {
        "profile": "full",
        "group": "lint",
        "candidate_digest": "cand",
        "config_digest": "cfg",
        "toolchain_digest": "tool",
        "environment_digest": "env",
    }
    base.update(overrides)
    return _receipt.receipt_key(_receipt.DigestInputs(**base))


def test_edit_after_success_invalidates_receipt_key(source, tmp_path):
    """A receipt produced before a candidate edit must not be reusable as
    evidence for the edited state: `candidate_digest` -- and therefore
    `receipt_key` -- changes the instant the candidate's bytes change."""
    candidate = materialize(source, tmp_path)
    before = candidate_digest(candidate.root)
    (candidate.root / "untracked.txt").write_text("edited after success\n")
    after = candidate_digest(candidate.root)

    assert before != after
    assert _key(candidate_digest=before) != _key(candidate_digest=after)


def _tool_script(version: str) -> str:
    import sys

    return (
        f"#!{sys.executable}\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        f"    print('demo {version}')\n"
        "else:\n"
        "    print('ran')\n"
    )


def _provision(store, contents: str, version: str = "1.0.0") -> dict:
    """Provision a fixture `demo` store tool with the given file contents;
    the lock and store manifest agree on whatever hash `contents` hashes to,
    so this always resolves cleanly -- only the *bytes* differ between two
    calls with the same `version` string."""
    exe_path = store / f"tools/demo/{version}/bin/demo"
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    exe_path.write_text(contents)
    exe_path.chmod(0o700)
    exe_sha = hashlib.sha256(exe_path.read_bytes()).hexdigest()
    lock = {
        "schema_version": 1,
        "tools": {
            "demo": {
                "kind": "binary",
                "version": version,
                "platforms": {
                    "the-platform": {
                        "url": "https://example.invalid/demo.tar.gz",
                        "sha256": exe_sha,
                        "exe_sha256": exe_sha,
                        "path_in_archive": "demo",
                    }
                },
            }
        },
    }
    manifest = {
        "schema_version": 1,
        "lock_digest": hashlib.sha256(
            json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "tools": {
            "demo": {
                "source_sha256": exe_sha,
                "executables": {
                    "bin/demo": {
                        "path": f"tools/demo/{version}/bin/demo",
                        "sha256": exe_sha,
                    }
                },
            }
        },
    }
    (store / "manifest.json").write_text(json.dumps(manifest))
    return lock


def _demo_registry(lock: dict) -> dict:
    tool = {
        "executable": "store:demo/bin/demo",
        "version_argv": ("store:demo/bin/demo", "--version"),
        "expected_version": "1.0.0",
        "required_modules": (),
    }
    check = CheckSpec(
        id="check.demo",
        category="test",
        group="test",
        argv=("store:demo/bin/demo", "--run"),
        cwd=".",
        inputs=("noop.py",),
        dependencies=(),
        timeout_seconds=5.0,
        selection="project",
        tool="demo",
        version="1.0.0",
    )
    return {
        "schema_version": 1,
        "toolchain_lock_document": lock,
        "tools": {"demo": tool},
        "checks": (check,),
        "candidate_preparations": (),
        "profiles": dict.fromkeys(
            ("quick", "full", "security", "release"), ("check.demo",)
        ),
        "coverage_pending": dict.fromkeys(("quick", "full", "security", "release"), ()),
    }


@pytest.fixture(autouse=True)
def _pin_platform(monkeypatch):
    from manifest_agent.checks import toolchain

    monkeypatch.setattr(toolchain, "current_platform", lambda: "the-platform")


def test_tool_swap_with_same_version_string_invalidates(source, tmp_path):
    """Headline case: two store binaries reporting the identical `1.0.0`
    version string, but different bytes, must produce different
    `toolchain_digest`/`receipt_key` values -- a version-string match alone
    is not enough to treat two runs as the same verification."""
    (source[0] / "noop.py").write_text("VALUE = 1\n")
    candidate = materialize(source, tmp_path)
    env = {"MANIFEST_TOOLCHAIN_STORE": str(tmp_path / "store"), "HOME": "/tmp"}

    store_a = tmp_path / "store"
    lock_a = _provision(store_a, _tool_script("1.0.0"))
    registry_a = _demo_registry(lock_a)
    report_a = run_profile(registry_a, "full", None, candidate, env)
    assert report_a["status"] == "PASS", report_a["results"]

    # Re-provision the *same* store location with different bytes that still
    # report "1.0.0" -- the lock's exe_sha256 is updated to match, so this
    # is a legitimate re-provision, not a swapped-launcher attack; but it is
    # a genuinely different tool identity and must be recorded as one.
    lock_b = _provision(store_a, _tool_script("1.0.0") + "\n# rebuilt\n")
    registry_b = _demo_registry(lock_b)
    report_b = run_profile(registry_b, "full", None, candidate, env)
    assert report_b["status"] == "PASS", report_b["results"]

    assert report_a["toolchain_digest"] != report_b["toolchain_digest"]
    assert report_a["receipt_key"] != report_b["receipt_key"]


def test_environment_change_invalidates_environment_digest():
    platform = "linux-x64"
    env_a = {"LANG": "C", "PATH": "/store/bin"}
    env_b = {"LANG": "en_US.UTF-8", "PATH": "/store/bin"}

    digest_a = _receipt.environment_digest(env_a, platform)
    digest_b = _receipt.environment_digest(env_b, platform)

    assert digest_a != digest_b
    assert _key(environment_digest=digest_a) != _key(environment_digest=digest_b)


def test_environment_digest_ignores_home():
    """`HOME` is deliberately excluded -- a receipt must not depend on which
    developer's home directory produced it."""
    platform = "linux-x64"
    digest_a = _receipt.environment_digest({"HOME": "/home/alice"}, platform)
    digest_b = _receipt.environment_digest({"HOME": "/home/bob"}, platform)

    assert digest_a == digest_b


@pytest.fixture
def registry(registry_file):
    return load_two_group_registry(registry_file)


@pytest.fixture
def digest(registry):
    from manifest_agent.checks.runner import _config_digest

    return _config_digest(registry)


def test_mixed_toolchain_across_groups_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    receipts[1]["toolchain_digest"] = "a-different-toolchain-digest"

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any(
        "toolchain_digest differs across producer groups" in d
        for d in report["diagnostics"]
    )


def test_expired_security_receipt_is_rejected(registry_file):
    registry = load_two_group_registry(registry_file)
    from manifest_agent.checks.runner import _config_digest

    digest = _config_digest(registry)
    expired = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    receipts = [
        receipt_fixture(
            group="lint",
            results=[result("lint.a")],
            digest=digest,
            profile="security",
            expires_at=expired,
        )
    ]
    run_context = context(producer_jobs=[job("lint")])

    report = aggregate_results(registry, "security", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("stale receipt" in d and "expired" in d for d in report["diagnostics"])


def test_unexpired_security_receipt_is_accepted(registry_file):
    registry = load_two_group_registry(registry_file)
    from manifest_agent.checks.runner import _config_digest

    digest = _config_digest(registry)
    fresh = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    receipts = [
        receipt_fixture(
            group="lint",
            results=[result("lint.a")],
            digest=digest,
            profile="security",
            expires_at=fresh,
        )
    ]
    run_context = context(producer_jobs=[job("lint")])

    report = aggregate_results(registry, "security", receipts, run_context)

    assert report["status"] == "PASS", report["diagnostics"]
