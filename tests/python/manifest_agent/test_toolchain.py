"""Fixture-driven coverage for the content-addressed toolchain store resolver.

Every row of the failure-semantics table in phase-3-5-decisions.md section 3a
gets one real test asserting its exact BLOCKED reason string, built entirely
from local fixture files -- no network, no real download.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from manifest_agent.checks import toolchain


def _write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _lock(*, sha256: str | None, platform: str = "linux-x64") -> dict:
    return {
        "schema_version": 1,
        "tools": {
            "gitleaks": {
                "kind": "binary",
                "version": "8.30.1",
                "platforms": {
                    platform: {
                        "url": "https://example.invalid/gitleaks.tar.gz",
                        "sha256": sha256,
                        "path_in_archive": "gitleaks",
                    }
                },
            }
        },
    }


def _provision_store(
    store: Path,
    lock: dict,
    *,
    exe_bytes: bytes = b"#!/bin/sh\necho gitleaks\n",
    relative_exe: str = "tools/gitleaks/8.30.1/bin/gitleaks",
    lock_digest_override: str | None = None,
    source_sha256_override: str | None = None,
    exe_sha256_override: str | None = None,
) -> None:
    exe_path = store / relative_exe
    actual_sha = _write(exe_path, exe_bytes)
    manifest = {
        "schema_version": 1,
        "lock_digest": lock_digest_override or toolchain.lock_digest(lock),
        "tools": {
            "gitleaks": {
                "source_sha256": (
                    source_sha256_override
                    or lock["tools"]["gitleaks"]["platforms"]["linux-x64"]["sha256"]
                ),
                "executables": {
                    "bin/gitleaks": {
                        "path": relative_exe,
                        "sha256": exe_sha256_override or actual_sha,
                    }
                },
            }
        },
    }
    (store / "manifest.json").write_text(json.dumps(manifest))


class TestParseAndAllowList:
    def test_parse_store_executable_splits_bundle_and_relative(self):
        assert toolchain.parse_store_executable("store:python-env/bin/ruff") == (
            "python-env",
            "bin/ruff",
        )

    def test_parse_store_executable_rejects_plain_name(self):
        assert toolchain.parse_store_executable("ruff") is None

    @pytest.mark.parametrize("name", ["python3", "bash"])
    def test_always_present_interpreters_are_legal(self, name):
        assert toolchain.is_legal_plain_executable(name) is True

    def test_repo_relative_script_is_legal(self):
        assert toolchain.is_legal_plain_executable("tests/lint/check.sh") is True

    @pytest.mark.parametrize("name", ["ruff", "gitleaks", "shellcheck"])
    def test_bare_path_tool_names_are_not_on_the_allow_list(self, name):
        assert toolchain.is_legal_plain_executable(name) is False

    def test_absolute_path_is_not_legal(self):
        assert toolchain.is_legal_plain_executable("/usr/bin/ruff") is False


class TestStoreRoot:
    def test_env_override_wins(self, tmp_path):
        env = {"MANIFEST_TOOLCHAIN_STORE": str(tmp_path / "custom")}
        assert toolchain.store_root(env) == tmp_path / "custom"

    def test_xdg_cache_home_used_when_no_override(self, tmp_path):
        env = {"XDG_CACHE_HOME": str(tmp_path / "xdg")}
        assert toolchain.store_root(env) == tmp_path / "xdg" / "manifest" / "toolchain"

    def test_home_fallback_when_nothing_else_set(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        assert (
            toolchain.store_root(env) == tmp_path / ".cache" / "manifest" / "toolchain"
        )

    def test_never_defaults_into_a_repo_relative_path(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        resolved = toolchain.store_root(env)
        assert not resolved.is_relative_to(Path.cwd())


class TestResolveFailureSemantics:
    """One test per row of the 3a failure-semantics table."""

    def test_unattested_platform_blocks(self, tmp_path):
        lock = _lock(sha256=None)
        store = tmp_path / "store"
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(outcome, toolchain.BlockedReason)
        assert outcome.reason == "toolchain: gitleaks unattested for linux-x64"

    def test_unattested_when_platform_missing_from_lock(self, tmp_path):
        lock = _lock(sha256="a" * 64, platform="linux-x64")
        store = tmp_path / "store"
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks",
            lock=lock,
            store=store,
            platform="darwin-arm64",
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: gitleaks unattested for darwin-arm64"
        )

    def test_store_missing_entry_blocks(self, tmp_path):
        expected = "a" * 64
        lock = _lock(sha256=expected)
        store = tmp_path / "store"  # never provisioned
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: gitleaks not provisioned (run manifest provision)"
        )

    def test_digest_mismatch_blocks(self, tmp_path):
        store = tmp_path / "store"
        exe_path = store / "tools/gitleaks/8.30.1/bin/gitleaks"
        real_sha = _write(exe_path, b"real bytes")
        lock = _lock(sha256=real_sha)
        _provision_store(store, lock, exe_bytes=b"real bytes")
        # Mutate the file after provisioning, as if a launcher were swapped in.
        exe_path.write_bytes(b"swapped launcher")
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason("toolchain: gitleaks digest mismatch")

    def test_manifest_lock_digest_mismatch_blocks_store_stale(self, tmp_path):
        store = tmp_path / "store"
        lock = _lock(sha256="b" * 64)
        _provision_store(store, lock, lock_digest_override="0" * 64)
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: store stale (lock changed)"
        )

    def test_source_sha_mismatch_blocks_store_stale(self, tmp_path):
        store = tmp_path / "store"
        lock = _lock(sha256="c" * 64)
        _provision_store(store, lock, source_sha256_override="d" * 64)
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: store stale (lock changed)"
        )

    def test_correct_store_resolves_and_verifies_hash(self, tmp_path):
        store = tmp_path / "store"
        exe_bytes = b"#!/bin/sh\necho gitleaks 8.30.1\n"
        exe_path = store / "tools/gitleaks/8.30.1/bin/gitleaks"
        real_sha = hashlib.sha256(exe_bytes).hexdigest()
        lock = _lock(sha256=real_sha)
        _provision_store(store, lock, exe_bytes=exe_bytes)
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(outcome, toolchain.ResolvedTool)
        assert outcome.executable == exe_path
        assert outcome.tool_sha256 == real_sha
        assert outcome.path_entries[0] == exe_path.parent

    def test_partial_store_blocks_only_the_missing_tool(self, tmp_path):
        """Partial provisioning is per-tool, not per-store."""
        store = tmp_path / "store"
        exe_bytes = b"provisioned"
        real_sha = hashlib.sha256(exe_bytes).hexdigest()
        lock = {
            "schema_version": 1,
            "tools": {
                "gitleaks": {
                    "kind": "binary",
                    "version": "8.30.1",
                    "platforms": {
                        "linux-x64": {
                            "url": "https://example.invalid/gitleaks",
                            "sha256": real_sha,
                            "path_in_archive": "gitleaks",
                        }
                    },
                },
                "shfmt": {
                    "kind": "binary",
                    "version": "3.13.1",
                    "platforms": {
                        "linux-x64": {
                            "url": "https://example.invalid/shfmt",
                            "sha256": "e" * 64,
                            "path_in_archive": "shfmt",
                        }
                    },
                },
            },
        }
        exe_path = store / "tools/gitleaks/8.30.1/bin/gitleaks"
        _write(exe_path, exe_bytes)
        manifest = {
            "schema_version": 1,
            "lock_digest": toolchain.lock_digest(lock),
            "tools": {
                "gitleaks": {
                    "source_sha256": real_sha,
                    "executables": {
                        "bin/gitleaks": {
                            "path": "tools/gitleaks/8.30.1/bin/gitleaks",
                            "sha256": real_sha,
                        }
                    },
                }
                # shfmt intentionally absent: not provisioned yet.
            },
        }
        (store / "manifest.json").write_text(json.dumps(manifest))

        gitleaks_outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        shfmt_outcome = toolchain.resolve(
            "store:shfmt/bin/shfmt", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(gitleaks_outcome, toolchain.ResolvedTool)
        assert shfmt_outcome == toolchain.BlockedReason(
            "toolchain: shfmt not provisioned (run manifest provision)"
        )

    def test_interpreter_hash_is_verified_for_python_env(self, tmp_path):
        store = tmp_path / "store"
        interpreter_bytes = b"fake-interpreter"
        exe_bytes = b"#!fake-interpreter\nruff\n"
        interpreter_sha = _write(
            store / "tools/python-env/deadbeef/bin/python3", interpreter_bytes
        )
        exe_sha = _write(store / "tools/python-env/deadbeef/bin/ruff", exe_bytes)
        lock = {
            "schema_version": 1,
            "tools": {
                "python-env": {
                    "kind": "python-env",
                    "version": "deadbeef",
                    "platforms": {
                        "linux-x64": {
                            "url": "file://config/toolchain/pyproject.toml",
                            "sha256": "f" * 64,
                            "path_in_archive": ".",
                        }
                    },
                }
            },
        }
        manifest = {
            "schema_version": 1,
            "lock_digest": toolchain.lock_digest(lock),
            "tools": {
                "python-env": {
                    "source_sha256": "f" * 64,
                    "executables": {
                        "bin/ruff": {
                            "path": "tools/python-env/deadbeef/bin/ruff",
                            "sha256": exe_sha,
                            "interpreter": "tools/python-env/deadbeef/bin/python3",
                            "interpreter_sha256": interpreter_sha,
                        }
                    },
                }
            },
        }
        (store / "manifest.json").write_text(json.dumps(manifest))

        outcome = toolchain.resolve(
            "store:python-env/bin/ruff", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(outcome, toolchain.ResolvedTool)
        assert outcome.interpreter == store / "tools/python-env/deadbeef/bin/python3"

        # Now swap the interpreter behind the console script -- must BLOCK.
        (store / "tools/python-env/deadbeef/bin/python3").write_bytes(b"swapped")
        outcome_after_swap = toolchain.resolve(
            "store:python-env/bin/ruff", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome_after_swap == toolchain.BlockedReason(
            "toolchain: python-env digest mismatch"
        )


class TestRewriteArgvAndEnv:
    def test_rewrite_argv_substitutes_resolved_executable(self, tmp_path):
        resolved = toolchain.ResolvedTool(
            "gitleaks", tmp_path / "gitleaks", None, (tmp_path,), "x" * 64
        )
        argv = ("store:gitleaks/bin/gitleaks", "--version")
        assert toolchain.rewrite_argv(argv, resolved) == (
            str(tmp_path / "gitleaks"),
            "--version",
        )

    def test_rewrite_argv_is_a_noop_for_plain_argv(self):
        argv = ("ruff", "--version")
        assert toolchain.rewrite_argv(argv, None) == argv

    def test_resolved_env_drops_user_path(self, tmp_path):
        resolved = toolchain.ResolvedTool(
            "gitleaks",
            tmp_path / "gitleaks",
            None,
            (tmp_path / "bin",),
            "x" * 64,
        )
        env = {"PATH": "/usr/local/evil/bin", "HOME": "/home/x"}
        built = toolchain.resolved_env(env, resolved)
        assert built["PATH"] == str(tmp_path / "bin")
        assert built["HOME"] == "/home/x"


class TestFingerprintDetectsMidRunMutation:
    def test_fingerprint_changes_when_manifest_or_exe_changes(self, tmp_path):
        store = tmp_path / "store"
        exe_bytes = b"original"
        exe_path = store / "tools/gitleaks/8.30.1/bin/gitleaks"
        _write(exe_path, exe_bytes)
        (store / "manifest.json").write_text(json.dumps({"schema_version": 1}))
        resolved = toolchain.ResolvedTool(
            "gitleaks", exe_path, None, (exe_path.parent,), "irrelevant"
        )
        before = toolchain.fingerprint(store, {"gitleaks": resolved})
        exe_path.write_bytes(b"mutated mid-run")
        after = toolchain.fingerprint(store, {"gitleaks": resolved})
        assert before != after


class TestLockDigestForRegistry:
    def test_empty_when_no_toolchain_lock_declared(self, tmp_path):
        registry_path = tmp_path / "config" / "project-checks.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text("{}")
        fields = toolchain.lock_digest_for_registry({}, registry_path)
        assert fields == {"toolchain_lock": "", "toolchain_lock_digest": ""}

    def test_digest_reflects_lock_file_content(self, tmp_path):
        (tmp_path / "config").mkdir()
        registry_path = tmp_path / "config" / "project-checks.json"
        registry_path.write_text("{}")
        lock_path = tmp_path / "config" / "toolchain.lock.json"
        lock_bytes = b'{"schema_version": 1, "tools": {}}'
        lock_path.write_bytes(lock_bytes)
        fields = toolchain.lock_digest_for_registry(
            {"toolchain_lock": "config/toolchain.lock.json"}, registry_path
        )
        assert fields["toolchain_lock"] == "config/toolchain.lock.json"
        assert fields["toolchain_lock_digest"] == hashlib.sha256(lock_bytes).hexdigest()

    def test_digest_empty_when_lock_file_missing(self, tmp_path):
        (tmp_path / "config").mkdir()
        registry_path = tmp_path / "config" / "project-checks.json"
        registry_path.write_text("{}")
        fields = toolchain.lock_digest_for_registry(
            {"toolchain_lock": "config/does-not-exist.json"}, registry_path
        )
        assert fields["toolchain_lock_digest"] == ""
