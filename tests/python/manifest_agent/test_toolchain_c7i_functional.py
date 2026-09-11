"""C7i step 4 (phase-3-5-decisions.md Correction 7 rule 4): functional
proof, through the real preflight resolver against a freshly provisioned
store, that the test group's registry wiring actually works -- not just
that it parses.

These tests use `toolchain.resolve_for_preflight` (the exact function
`runner._preflight_tool` calls) against a REAL store provisioned by
`manifest provision` from the repository's own committed lock, then
subprocess.run the returned argv/env directly -- the same "resolve, then
really execute what came back" idiom `test_toolchain_c7c_preflight.py`
already established for this codebase. Network-gated
(`MANIFEST_C7I_NETWORK=1`... no: provisioning `project-env`/`config-env`
needs no network on a host with `uv`/`node` already store-provisioned, so
these are gated on a real store being available at all, same pattern as
`test_toolchain_c7h_project_env.py`'s `MANIFEST_C7B_NETWORK` tests but using
`manifest_agent.checks.toolchain_provision.provision` directly so CI/local
runs without extra env wiring).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from manifest_agent.checks import toolchain
from manifest_agent.checks import toolchain_provision as provision_mod
from manifest_agent.checks import toolchain_pythonpath as pythonpath_mod

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = REPO_ROOT / "config" / "project-checks.json"
LOCK_PATH = REPO_ROOT / "config" / "toolchain.lock.json"


def _fresh_store(tmp_path: Path) -> tuple[dict, Path, str]:
    """A real store, freshly provisioned from the repo's own committed lock
    -- `project-env`/`node-env`/`node` bundles only (what this file's checks
    need), never the whole nine-bundle set, to keep this fast."""
    store = tmp_path / "store"
    lock = json.loads(LOCK_PATH.read_text())
    platform = toolchain.current_platform()
    outcomes = provision_mod.provision(
        lock,
        store,
        platform=platform,
        only=frozenset({"uv", "node", "node-env", "project-env"}),
        repo_root=REPO_ROOT,
        env=dict(os.environ),
    )
    unprovisioned = [o for o in outcomes if o.status != "provisioned"]
    if unprovisioned:
        pytest.skip(f"store bundles unavailable on this host: {unprovisioned}")
    return lock, store, platform


def _registry_tool(check_id: str) -> dict:
    document = json.loads(REGISTRY_PATH.read_text())
    return document["tools"][check_id]


def _resolve(tool: dict, env: dict, lock: dict, candidate_root: Path):
    return toolchain.resolve_for_preflight(tool, env, lock, candidate_root)


class TestProjectEnvImportsTheCandidatesOwnSrc:
    """Correction 7 rule 4(a): `test.python` runs and imports the
    CANDIDATE's own `src/`, proven with a synthetic candidate carrying a
    marker no real copy of `manifest_agent` has -- through the real
    `store:project-env/bin/python -m pytest` argv the registry declares,
    not a hand-built subprocess call."""

    def test_pytest_collects_and_passes_against_a_synthetic_candidates_src(
        self, tmp_path: Path
    ):
        lock, store, _platform = _fresh_store(tmp_path)
        candidate = tmp_path / "candidate"
        (candidate / "src" / "manifest_agent").mkdir(parents=True)
        (candidate / "src" / "manifest_agent" / "__init__.py").write_text(
            'MARKER = "candidate-local-c7i-42"\n'
        )
        tests_dir = candidate / "tests" / "python"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_marker.py").write_text(
            "import manifest_agent\n"
            "\n"
            "def test_marker_is_the_candidates_own_copy():\n"
            '    assert manifest_agent.MARKER == "candidate-local-c7i-42"\n'
        )
        (candidate / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\n"
            'pythonpath = [".", "src", "tests/python"]\n'
            'testpaths = ["tests/python"]\n'
        )

        tool = _registry_tool("test.python")
        env = {
            "PATH": "/usr/bin",
            "HOME": str(tmp_path),
            "MANIFEST_TOOLCHAIN_STORE": str(store),
        }
        resolved, _version_argv, _run_env, blocked = _resolve(
            tool, env, lock, candidate
        )
        assert blocked is None, blocked
        assert resolved is not None

        argv = toolchain.rewrite_argv(
            (
                "store:project-env/bin/python",
                "-m",
                "pytest",
                "tests/python/",
                "-v",
            ),
            resolved,
        )
        run_env = toolchain.resolved_env(env, resolved)
        result = subprocess.run(
            list(argv),
            cwd=candidate,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 passed" in result.stdout


class TestImpostorNeverReachedByTheTestGroup:
    """Correction 7 rule 4(b): an impostor `pytest`/`python3` sitting first
    on the CALLER's `PATH` is never the one that actually runs -- proven by
    placing a real impostor on the caller PATH before resolution and
    checking the store's own binary answered instead."""

    def test_test_python_never_runs_a_path_impostor_pytest(self, tmp_path: Path):
        lock, store, _platform = _fresh_store(tmp_path)
        impostor_dir = tmp_path / "impostor-bin"
        impostor_dir.mkdir()
        impostor = impostor_dir / "python"
        impostor.write_text(f"#!{sys.executable}\nprint('IMPOSTOR RAN')\n")
        impostor.chmod(0o700)

        tool = _registry_tool("test.python")
        env = {
            "PATH": str(impostor_dir),
            "HOME": str(tmp_path),
            "MANIFEST_TOOLCHAIN_STORE": str(store),
        }
        _resolved, version_argv, run_env, blocked = _resolve(
            tool, env, lock, tmp_path / "candidate"
        )
        assert blocked is None, blocked
        assert str(impostor_dir) not in run_env["PATH"]
        result = subprocess.run(
            list(version_argv),
            cwd=REPO_ROOT,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "IMPOSTOR RAN" not in result.stdout


class TestProjectEnvStaleLockBlocksThroughTheRealPreflight:
    """Correction 7 rule 4(c): a `project-env` whose declared lock source
    hash no longer matches what the store was provisioned with (the shape
    of "root uv.lock changed since this store was built") BLOCKs "store
    stale" -- exercised through `resolve_for_preflight` with `test.python`'s
    actual real tool record, not a synthetic single-purpose lock."""

    def test_editing_the_declared_source_hash_blocks_test_pythons_real_tool(
        self, tmp_path: Path
    ):
        lock, store, _platform = _fresh_store(tmp_path)
        bumped_lock = json.loads(json.dumps(lock))  # deep copy
        platform_key = toolchain.current_platform()
        bumped_lock["tools"]["project-env"]["platforms"][platform_key]["sha256"] = (
            "f" * 64
        )
        tool = _registry_tool("test.python")
        env = {
            "PATH": "/usr/bin",
            "HOME": str(tmp_path),
            "MANIFEST_TOOLCHAIN_STORE": str(store),
        }
        resolved, _argv, _env, blocked = _resolve(
            tool, env, bumped_lock, tmp_path / "candidate"
        )
        assert resolved is None
        assert blocked == "toolchain: store stale (lock changed)"


class TestBatsBodyImportsYamlThroughTheStoreEnv:
    """Correction 7 rule 4(d): a body a `test.bats` script would run
    (`python3 -c 'import yaml'`) succeeds via the store's `project-env`,
    reached through `path_prepend`, never a bare/absent ambient
    interpreter."""

    def test_python3_dash_c_import_yaml_succeeds_via_path_prepend(self, tmp_path: Path):
        lock, store, _platform = _fresh_store(tmp_path)
        tool = _registry_tool("test.bats")
        env = {
            "PATH": "/usr/bin",
            "HOME": str(tmp_path),
            "MANIFEST_TOOLCHAIN_STORE": str(store),
        }
        resolved, _version_argv, run_env, blocked = _resolve(
            tool, env, lock, tmp_path / "candidate"
        )
        assert blocked is None, blocked
        assert resolved is not None

        result = subprocess.run(
            ["bash", "-c", "python3 -c 'import yaml; print(yaml.__name__)'"],
            env=run_env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr


class TestPathDependencyResolvesToTheCandidatesOwnCopy:
    """C7k step 2 (Correction 9, rule 2): `manifest-model-policy` -- the
    root project's only local path dependency, installed EDITABLE into
    `project-env` and pointing at the PROVISIONING checkout by default --
    resolves to the CANDIDATE's own copy through `store:project-env/bin/
    python`, proven with a marker no real copy of the module carries, via
    the real `toolchain_pythonpath` roots + `test.python`'s registry argv."""

    _DEP_RELATIVE = Path("configs/claude/scripts/manifest_model_policy")

    def test_marker_edit_in_candidates_manifest_model_policy_is_what_imports(
        self, tmp_path: Path
    ):
        lock, store, _platform = _fresh_store(tmp_path)
        candidate = tmp_path / "candidate"
        candidate_dep_dir = candidate / self._DEP_RELATIVE
        candidate_dep_dir.mkdir(parents=True)
        (candidate_dep_dir / "pyproject.toml").write_text(
            (REPO_ROOT / self._DEP_RELATIVE / "pyproject.toml").read_text()
        )
        (candidate_dep_dir / "__init__.py").write_text(
            'MARKER = "candidate-local-c7k-marker"\n'
        )

        roots = pythonpath_mod.candidate_path_dependency_roots(REPO_ROOT, candidate)
        assert not isinstance(roots, pythonpath_mod.BlockedPythonPath), roots
        assert roots == (candidate / "configs" / "claude" / "scripts",)

        tool = _registry_tool("test.python")
        env = {
            "PATH": "/usr/bin",
            "HOME": str(tmp_path),
            "MANIFEST_TOOLCHAIN_STORE": str(store),
        }
        resolved, _version_argv, _env, blocked = _resolve(tool, env, lock, candidate)
        assert blocked is None, blocked
        assert resolved is not None

        run_env = toolchain.resolved_env(env, resolved)
        run_env = pythonpath_mod.with_candidate_pythonpath(run_env, candidate, roots)
        argv = toolchain.rewrite_argv(
            (
                "store:project-env/bin/python",
                "-c",
                "import manifest_model_policy as m;print(m.__file__);print(m.MARKER)",
            ),
            resolved,
        )
        result = subprocess.run(
            list(argv),
            cwd=candidate,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        printed_file, printed_marker = result.stdout.splitlines()[:2]
        imported_path = Path(printed_file).resolve()

        # (a) the candidate's own copy answered.
        assert imported_path.is_relative_to(candidate.resolve())
        assert printed_marker == "candidate-local-c7k-marker"
        # (b) the provisioning checkout's copy was never reached.
        assert not imported_path.is_relative_to(REPO_ROOT.resolve())
