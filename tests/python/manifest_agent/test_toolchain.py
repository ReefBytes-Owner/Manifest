"""Parsing, allow-list, store-root safety, and registry-digest coverage for
the content-addressed toolchain store resolver.

`toolchain.resolve()`'s failure-semantics table lives in
`test_toolchain_resolve.py` -- split out to stay under the per-class
size/method ceilings.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from manifest_agent.checks import toolchain
from tests.python.manifest_agent.toolchain_fixtures import _write


class TestParseAndAllowList:
    def test_parse_store_executable_splits_bundle_and_relative(self):
        assert toolchain.parse_store_executable("store:python-env/bin/ruff") == (
            "python-env",
            "bin/ruff",
        )

    def test_parse_store_executable_rejects_plain_name(self):
        assert toolchain.parse_store_executable("ruff") is None

    @pytest.mark.parametrize("name", ["python3", "bash"])
    def test_always_present_interpreters_are_legal(self, name: str):
        assert toolchain.is_legal_plain_executable(name) is True

    def test_repo_relative_script_is_legal(self):
        assert toolchain.is_legal_plain_executable("tests/lint/check.sh") is True

    @pytest.mark.parametrize("name", ["ruff", "gitleaks", "shellcheck"])
    def test_bare_path_tool_names_are_not_on_the_allow_list(self, name: str):
        assert toolchain.is_legal_plain_executable(name) is False

    def test_absolute_path_is_not_legal(self):
        assert toolchain.is_legal_plain_executable("/usr/bin/ruff") is False


class TestStoreRoot:
    def test_env_override_wins(self, tmp_path: Path):
        env = {"MANIFEST_TOOLCHAIN_STORE": str(tmp_path / "custom")}
        assert toolchain.store_root(env) == tmp_path / "custom"

    def test_xdg_cache_home_used_when_no_override(self, tmp_path: Path):
        env = {"XDG_CACHE_HOME": str(tmp_path / "xdg")}
        assert toolchain.store_root(env) == tmp_path / "xdg" / "manifest" / "toolchain"

    def test_home_fallback_when_nothing_else_set(self, tmp_path: Path):
        env = {"HOME": str(tmp_path)}
        assert (
            toolchain.store_root(env) == tmp_path / ".cache" / "manifest" / "toolchain"
        )

    def test_never_defaults_into_a_repo_relative_path(self, tmp_path: Path):
        env = {"HOME": str(tmp_path)}
        resolved = toolchain.store_root(env)
        assert not resolved.is_relative_to(Path.cwd())

    def test_store_inside_cwd_is_rejected(self):
        env = {"MANIFEST_TOOLCHAIN_STORE": str(Path.cwd() / "toolchain-store")}
        with pytest.raises(toolchain.UnsafeStoreLocationError):
            toolchain.store_root(env)

    def test_store_inside_a_forbidden_root_is_rejected(self, tmp_path: Path):
        forbidden = tmp_path / "candidate"
        forbidden.mkdir()
        env = {"MANIFEST_TOOLCHAIN_STORE": str(forbidden / "store")}
        with pytest.raises(toolchain.UnsafeStoreLocationError):
            toolchain.store_root(env, forbidden)


class TestRewriteArgvAndEnv:
    def test_rewrite_argv_substitutes_resolved_executable(self, tmp_path: Path):
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

    def test_resolved_env_drops_user_path(self, tmp_path: Path):
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
    def test_fingerprint_changes_when_manifest_or_exe_changes(self, tmp_path: Path):
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
    def test_empty_when_no_toolchain_lock_declared(self, tmp_path: Path):
        registry_path = tmp_path / "config" / "project-checks.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text("{}")
        fields = toolchain.lock_digest_for_registry({}, registry_path)
        assert fields == {
            "toolchain_lock": "",
            "toolchain_lock_digest": "",
            "toolchain_lock_document": {},
        }

    def test_digest_reflects_lock_file_content(self, tmp_path: Path):
        (tmp_path / "config").mkdir()
        registry_path = tmp_path / "config" / "project-checks.json"
        registry_path.write_text("{}")
        lock_path = tmp_path / "config" / "toolchain.lock.json"
        lock_bytes = b'{"schema_version": 1, "tools": {"demo": {}}}'
        lock_path.write_bytes(lock_bytes)
        fields = toolchain.lock_digest_for_registry(
            {"toolchain_lock": "config/toolchain.lock.json"}, registry_path
        )
        assert fields["toolchain_lock"] == "config/toolchain.lock.json"
        assert fields["toolchain_lock_digest"] == hashlib.sha256(lock_bytes).hexdigest()
        assert fields["toolchain_lock_document"] == {
            "schema_version": 1,
            "tools": {"demo": {}},
        }

    def test_digest_empty_when_lock_file_missing(self, tmp_path: Path):
        (tmp_path / "config").mkdir()
        registry_path = tmp_path / "config" / "project-checks.json"
        registry_path.write_text("{}")
        fields = toolchain.lock_digest_for_registry(
            {"toolchain_lock": "config/does-not-exist.json"}, registry_path
        )
        assert fields["toolchain_lock_digest"] == ""
        assert fields["toolchain_lock_document"] == {}
