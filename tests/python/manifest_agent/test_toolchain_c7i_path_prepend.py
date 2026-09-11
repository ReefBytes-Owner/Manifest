"""C7i step 1: the `path_prepend` mechanism (phase-3-5-decisions.md
Correction 7 step 1).

A tool may declare `"path_prepend": ["store:<bundle>/bin", ...]`: each entry
names a bundle whose bin directory goes FIRST on the resolved child `PATH`,
ahead of the tool's own executable's bin dir and `os.defpath`, hash-verified
exactly like any other store reference. This exists for check bodies that
shell out to a nested interpreter themselves (a bats script running `python3
-c '...'`) -- `toolchain.rewrite_argv` only ever rewrites argv tokens the
runner itself launches, never a token a nested shell resolves on its own, so
`PATH` is the only honest channel for that nested resolution.

These tests prove the property with a real subprocess and a real PATH
impostor, not a description of the resolver.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from manifest_agent.checks import toolchain, toolchain_env


def _write_executable(path: Path, script: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
    path.chmod(0o700)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provisioned_project_env_store(
    tmp_path: Path, *, platform: str = "linux-x64"
) -> tuple[dict, Path]:
    """A one-bundle (`project-env`, `python-env` kind) fixture store: real
    `bin/python`(3) symlinks to `sys.executable` -- the same shape a real
    `uv`/venv bin dir has -- and no installed distributions. The
    `distribution_set_digest` of an empty site-packages is still a stable,
    computable value; `bin/python` itself is exempt from the launcher-
    inside-store check (Correction 3 rule 2), and a symlink (unlike a
    shebang-wrapper script) actually execs as the real interpreter when a
    nested shell invokes it, which the impostor test below depends on."""
    store = tmp_path / "store"
    relative = "tools/project-env/x/bin/python"
    exe_path = store / relative
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    exe_path.symlink_to(sys.executable)
    # A real `uv`/venv bin dir always carries `python3` (and `python3.x`)
    # alongside `python` -- only `bin/python` is hash-verified by `resolve()`
    # (the lock's one console script), but a nested shell that runs literal
    # `python3` must find the STORE's copy here, not fall through to
    # `os.defpath`.
    (exe_path.parent / "python3").symlink_to(sys.executable)
    env_root = exe_path.parent.parent
    exe_sha256 = toolchain_env.distribution_set_digest(env_root, "python-env")
    source_sha256 = "s" * 64
    lock = {
        "schema_version": 1,
        "tools": {
            "project-env": {
                "kind": "python-env",
                "version": "pyproject.toml",
                "platforms": {
                    platform: {
                        "url": "file://uv.lock",
                        "sha256": source_sha256,
                        "exe_sha256": exe_sha256,
                        "path_in_archive": ".",
                        "console_scripts": ["bin/python"],
                    }
                },
            }
        },
    }
    manifest = {
        "schema_version": 1,
        "lock_digest": toolchain.lock_digest(lock),
        "tools": {
            "project-env": {
                "source_sha256": source_sha256,
                "executables": {"bin/python": {"path": relative}},
            }
        },
    }
    (store / "manifest.json").write_text(json.dumps(manifest))
    return lock, store


def _impostor_path(tmp_path: Path) -> Path:
    impostor_dir = tmp_path / "impostor-bin"
    _write_executable(
        impostor_dir / "python3",
        f"#!{sys.executable}\nimport sys\nprint('IMPOSTOR')\n",
    )
    return impostor_dir


class TestUnattestedBundleBlocks:
    def test_a_path_prepend_bundle_absent_from_the_lock_blocks_the_whole_preflight(
        self, tmp_path: Path
    ):
        lock = {"schema_version": 1, "tools": {}}
        tool = {
            "executable": "bash",
            "version_argv": ["bash", "--version"],
            "path_prepend": ["store:project-env/bin"],
        }
        env = {"PATH": "/usr/bin", "HOME": str(tmp_path)}
        resolved, _argv, _env, blocked = toolchain.resolve_for_preflight(
            tool, env, lock, tmp_path / "candidate"
        )
        assert resolved is None
        assert blocked == (
            f"toolchain: project-env unattested for {toolchain.current_platform()}"
        )

    def test_an_unprovisioned_but_attested_bundle_blocks_as_not_provisioned(
        self, tmp_path: Path
    ):
        lock, _store = _provisioned_project_env_store(
            tmp_path, platform=toolchain.current_platform()
        )
        empty_store = tmp_path / "empty-store"
        tool = {
            "executable": "bash",
            "version_argv": ["bash", "--version"],
            "path_prepend": ["store:project-env/bin"],
        }
        env = {
            "PATH": "/usr/bin",
            "HOME": str(tmp_path),
            "MANIFEST_TOOLCHAIN_STORE": str(empty_store),
        }
        resolved, _argv, _env, blocked = toolchain.resolve_for_preflight(
            tool, env, lock, tmp_path / "candidate"
        )
        assert resolved is None
        assert (
            blocked == "toolchain: project-env not provisioned (run manifest provision)"
        )


class TestPathPrependReachesANestedShell:
    """The real attack: a check body itself shells out to `python3` (a bats
    test script, for example) -- `path_prepend` must make that nested
    resolution land on the store's interpreter, never an ambient impostor."""

    def _resolve(self, tmp_path: Path, caller_path: str):
        lock, store = _provisioned_project_env_store(
            tmp_path, platform=toolchain.current_platform()
        )
        tool = {
            "executable": "bash",
            "version_argv": ["bash", "--version"],
            "path_prepend": ["store:project-env/bin"],
        }
        env = {
            "PATH": caller_path,
            "HOME": str(tmp_path),
            "MANIFEST_TOOLCHAIN_STORE": str(store),
        }
        return toolchain.resolve_for_preflight(tool, env, lock, tmp_path / "candidate")

    def test_impostor_python3_on_the_caller_path_is_never_invoked_by_a_nested_bash_body(
        self, tmp_path: Path
    ):
        impostor_dir = _impostor_path(tmp_path)
        resolved, _argv, run_env, blocked = self._resolve(tmp_path, str(impostor_dir))
        assert blocked is None
        assert resolved is not None
        # The impostor directory must not survive resolution at all.
        assert str(impostor_dir) not in run_env["PATH"]

        bash = shutil.which("bash") or "/bin/bash"
        result = subprocess.run(
            [bash, "-c", "python3 -c 'import sys;print(sys.executable)'"],
            env=run_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert "IMPOSTOR" not in result.stdout
        assert str(tmp_path / "store") in result.stdout

    def test_project_envs_bin_dir_is_first_on_path_ahead_of_the_tools_own_bin_dir(
        self, tmp_path: Path
    ):
        lock, store = _provisioned_project_env_store(
            tmp_path, platform=toolchain.current_platform()
        )
        tool = {
            "executable": "bash",
            "version_argv": ["bash", "--version"],
            "path_prepend": ["store:project-env/bin"],
        }
        env = {"PATH": "/usr/bin", "HOME": str(tmp_path)}
        env["MANIFEST_TOOLCHAIN_STORE"] = str(store)
        _resolved, _argv, run_env, blocked = toolchain.resolve_for_preflight(
            tool, env, lock, tmp_path / "candidate"
        )
        assert blocked is None
        entries = run_env["PATH"].split(":")
        assert entries[0] == str(store / "tools/project-env/x/bin")

    def test_path_empty_in_the_caller_still_yields_the_store_dirs(self, tmp_path: Path):
        resolved, _argv, run_env, blocked = self._resolve(tmp_path, "")
        assert blocked is None
        assert resolved is not None
        assert run_env["PATH"] != ""
        assert str(tmp_path / "store") in run_env["PATH"]
