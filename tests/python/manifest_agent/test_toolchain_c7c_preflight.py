"""C7c: a tool whose check body resolves its own engine (uv, shfmt, gitleaks,
shellcheck, bats) must have its version PREFLIGHT probe target that SAME
store-resolved engine -- not an ambient name found on `PATH`.

Before this fix, `resolve_for_preflight` only ever resolved `store:`
references named in `tool["executable"]` itself; a tool whose `executable`
stayed the repo-owned wrapper (`python3`) while its `version_argv` named a
DIFFERENT engine (`--command uv` / `--executable store:uv/bin/uv`) skipped
store resolution entirely, so the preflight silently trusted whatever
`uv`/`shfmt`/... happened to be first on `PATH` -- exactly the trust gap
3a/C2/C2b/C2c closed for every OTHER tool. These tests prove the fix with a
real PATH impostor, not a description of the mechanism.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from manifest_agent.checks import toolchain
from tools.project_checks import tool_versions


def _write_executable(path: Path, script: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
    path.chmod(0o700)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_engine_script(version_line: str) -> str:
    return f"#!{sys.executable}\nimport sys\nprint({version_line!r})\n"


def _provision_uv_store(tmp_path: Path) -> tuple[dict, Path]:
    """A fixture store with one real, hash-verified `uv` bundle."""
    store = tmp_path / "store"
    relative = "tools/uv/1.2.3/bin/uv"
    exe_sha = _write_executable(
        store / relative, _real_engine_script("uv 1.2.3 (store, real)")
    )
    lock = {
        "schema_version": 1,
        "tools": {
            "uv": {
                "kind": "binary",
                "version": "1.2.3",
                "platforms": {
                    toolchain.current_platform(): {
                        "url": "https://example.invalid/uv.tar.gz",
                        "sha256": exe_sha,
                        "exe_sha256": exe_sha,
                        "path_in_archive": "uv",
                    }
                },
            }
        },
    }
    manifest = {
        "schema_version": 1,
        "lock_digest": toolchain.lock_digest(lock),
        "tools": {
            "uv": {
                "source_sha256": exe_sha,
                "executables": {"bin/uv": {"path": relative, "sha256": exe_sha}},
            }
        },
    }
    (store / "manifest.json").write_text(json.dumps(manifest))
    return lock, store


def _impostor_path(tmp_path: Path) -> Path:
    """A directory with a bare `uv` on it that reports a DIFFERENT version --
    the attack this test proves no longer succeeds."""
    impostor_dir = tmp_path / "impostor-bin"
    _write_executable(
        impostor_dir / "uv", _real_engine_script("uv 9.9.9 (PATH impostor)")
    )
    return impostor_dir


class TestPreflightResolvesEmbeddedEngineReferences:
    def test_resolve_for_preflight_resolves_a_store_ref_named_only_in_version_argv(
        self, tmp_path: Path
    ):
        """`tool.executable` stays the repo-owned `python3` wrapper (the real
        shape of e.g. `package.coordinator`/`dependency.lock.root`); the
        engine it actually runs is named only inside `version_argv`."""
        lock, store = _provision_uv_store(tmp_path)
        impostor_dir = _impostor_path(tmp_path)
        tool = {
            "executable": "python3",
            "version_argv": [
                "python3",
                "-I",
                "tools/project_checks/tool_versions.py",
                "python-wrapper",
                "--command",
                "uv",
                "--executable",
                "store:uv/bin/uv",
            ],
        }
        env = {
            "PATH": str(impostor_dir),
            "HOME": str(tmp_path),
            "MANIFEST_TOOLCHAIN_STORE": str(store),
        }
        resolved, version_argv, run_env, blocked = toolchain.resolve_for_preflight(
            tool, env, lock, tmp_path / "candidate"
        )
        assert blocked is None
        assert resolved is not None
        assert version_argv[-1] == str(store / "tools/uv/1.2.3/bin/uv")
        # The impostor directory is gone from the child PATH entirely --
        # store bin dir first, then only os.defpath.
        assert str(impostor_dir) not in run_env["PATH"]

    def test_a_path_impostor_is_never_invoked_by_the_real_probe(self, tmp_path: Path):
        """End-to-end through the real `tool_versions.py` CLI: with a real
        PATH impostor present, the reported version is the STORE uv's, never
        the impostor's -- the actual attack this chunk closes, executed and
        observed, not just asserted about the resolver."""
        lock, store = _provision_uv_store(tmp_path)
        impostor_dir = _impostor_path(tmp_path)
        tool = {
            "executable": "python3",
            "version_argv": [
                sys.executable,
                "-I",
                "tools/project_checks/tool_versions.py",
                "python-wrapper",
                "--command",
                "uv",
                "--executable",
                "store:uv/bin/uv",
            ],
        }
        env = {
            "PATH": str(impostor_dir),
            "HOME": str(tmp_path),
            "MANIFEST_TOOLCHAIN_STORE": str(store),
        }
        _resolved, version_argv, run_env, blocked = toolchain.resolve_for_preflight(
            tool, env, lock, tmp_path / "candidate"
        )
        assert blocked is None
        repo_root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            list(version_argv),
            cwd=repo_root,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert "command:uv=1.2.3" in result.stdout
        assert "9.9.9" not in result.stdout

    def test_blocked_reason_from_the_embedded_ref_blocks_the_whole_preflight(
        self, tmp_path: Path
    ):
        """`uv` unattested for this platform: the preflight BLOCKs, it never
        falls back to searching `PATH` for a real-looking `uv`."""
        lock, _store = _provision_uv_store(tmp_path)
        # Drop the one attested platform so `uv` is genuinely unattested here.
        lock["tools"]["uv"]["platforms"] = {}
        tool = {
            "executable": "python3",
            "version_argv": [
                "python3",
                "-I",
                "tools/project_checks/tool_versions.py",
                "python-wrapper",
                "--command",
                "uv",
                "--executable",
                "store:uv/bin/uv",
            ],
        }
        env = {"PATH": "/usr/bin", "HOME": str(tmp_path)}
        resolved, _argv, _env, blocked = toolchain.resolve_for_preflight(
            tool, env, lock, tmp_path / "candidate"
        )
        assert resolved is None
        assert blocked == f"toolchain: uv unattested for {toolchain.current_platform()}"


class TestResolvedExecutableAbsolutePathHandling:
    def test_absolute_executable_matching_probe_basename_is_accepted(
        self, tmp_path: Path
    ):
        exe = tmp_path / "somewhere" / "uv"
        _write_executable(exe, _real_engine_script("uv 1.2.3"))
        resolved = tool_versions._resolved_executable("uv", str(exe))
        assert resolved == str(exe)

    def test_absolute_executable_with_wrong_basename_is_rejected(self, tmp_path: Path):
        exe = tmp_path / "somewhere" / "not-uv"
        _write_executable(exe, _real_engine_script("uv 1.2.3"))
        with pytest.raises(tool_versions.ProbeError):
            tool_versions._resolved_executable("uv", str(exe))

    def test_absolute_executable_that_does_not_exist_never_falls_back_to_path(
        self, tmp_path: Path, monkeypatch
    ):
        impostor_dir = _impostor_path(tmp_path)
        monkeypatch.setenv("PATH", str(impostor_dir))
        missing = tmp_path / "gone" / "uv"
        with pytest.raises(tool_versions.ProbeError, match="not installed"):
            tool_versions._resolved_executable("uv", str(missing))


class TestCheckBodiesImportUnderABarePathNoVenvShortcut:
    """Coordinator round 2, defect 1: a check body invoked as
    `python3 tools/project_checks/<module>.py <id> --root .` used to import
    `manifest_agent` successfully only because a dev checkout's own
    `.venv/bin/python3` happened to be first on `PATH` -- C7c's honest PATH
    (store bin dirs + `os.defpath`) exposed the hidden dependency by
    resolving `python3` to a bare interpreter instead. The fix
    (`toolchain_resolve.py` inserting the candidate's own `src/` onto
    `sys.path` before importing `manifest_agent`, same pattern
    `analysis_checks.py`/`debt_checks.py`/`dependency_checks.py` already
    used) is proven here with a real subprocess, no `PYTHONPATH`, and
    `PATH=os.defpath` -- never a crash (exit 1), even with nothing
    provisioned."""

    def _run_body(self, module: str, check_id: str) -> subprocess.CompletedProcess:

        env = {"PATH": os.defpath, "HOME": os.environ.get("HOME", "/tmp")}
        return subprocess.run(
            [
                sys.executable,
                f"tools/project_checks/{module}.py",
                check_id,
                "--root",
                ".",
            ],
            cwd=str(Path(__file__).resolve().parents[3]),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_structure_body_never_crashes_under_a_bare_path(self):
        result = self._run_body("structure", "lint.shell.scripts")
        assert "ModuleNotFoundError" not in result.stderr
        assert result.returncode in (0, 2, 3), (result.returncode, result.stderr)

    def test_hooks_body_never_crashes_under_a_bare_path(self):
        result = self._run_body("hooks", "hook.shfmt")
        assert "ModuleNotFoundError" not in result.stderr
        assert result.returncode in (0, 2, 3), (result.returncode, result.stderr)
