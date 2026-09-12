"""C7h (Correction 7): `project-env` / `config-env` -- the test group's
PROJECT environment, materialized from the repo's own lockfiles.

`project-env` (root `uv.lock`) carries the project's DEPENDENCIES only
(`--no-install-project`): `manifest_agent` itself is never baked into the
env, so a check that runs `store:project-env/bin/python -m pytest` with
`PYTHONPATH=<candidate>/src` on the child env always tests whatever the
CANDIDATE currently contains, never a stale copy frozen at provision
time. `config-env` (`configs/claude/uv.lock`) is installed for real --
its `[project.scripts] manifest` entry point is the artifact
`test.smoke.lite` needs at `bin/manifest`.

Real, network-gated materialization tests (`MANIFEST_C7B_NETWORK=1`,
reusing the flag `TestRealMaterializationNeverUsesAmbientEngines` already
established for this exact class of test) mirror this repo's own real
lockfiles; everywhere else, offline fixture-based tests exercise the
resolve()-level failure modes Correction 7 rule 4 requires.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from manifest_agent.checks import toolchain
from manifest_agent.checks import toolchain_provision as provision_mod

REPO_ROOT = Path(__file__).resolve().parents[3]
_NETWORK = os.environ.get("MANIFEST_C7B_NETWORK") == "1"
_NETWORK_SKIP = "set MANIFEST_C7B_NETWORK=1 to materialize the real project envs"


@pytest.mark.skipif(not _NETWORK, reason=_NETWORK_SKIP)
class TestProjectEnvRealMaterialization:
    """The real provisioner against this repo's own committed lock."""

    def _provisioned_store(self, tmp_path: Path) -> tuple[Path, dict, str]:
        store = tmp_path / "store"
        lock = json.loads((REPO_ROOT / "config" / "toolchain.lock.json").read_text())
        platform = toolchain.current_platform()
        outcomes = provision_mod.provision(
            lock,
            store,
            platform=platform,
            only=frozenset({"uv", "project-env", "config-env"}),
            repo_root=REPO_ROOT,
            env=dict(os.environ),
        )
        assert all(o.status == "provisioned" for o in outcomes), outcomes
        return store, lock, platform

    def test_project_env_python_has_no_manifest_agent_baked_in(
        self, tmp_path: Path
    ) -> None:
        """`--no-install-project` really took effect: importing
        `manifest_agent` under the store's own interpreter, with an EMPTY
        `PYTHONPATH`, fails -- there is no baked-in copy to find."""
        import subprocess

        store, lock, platform = self._provisioned_store(tmp_path)
        resolved = toolchain.resolve(
            "store:project-env/bin/python", lock=lock, store=store, platform=platform
        )
        assert isinstance(resolved, toolchain.ResolvedTool)
        result = subprocess.run(
            [str(resolved.executable), "-c", "import manifest_agent"],
            env={"PATH": "", "PYTHONPATH": ""},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
        assert "manifest_agent" in result.stderr

    def test_a_candidate_local_edit_is_what_gets_tested(self, tmp_path: Path) -> None:
        """The mechanism `test.python`/`test.hooks` rely on: pointing
        `PYTHONPATH` at a candidate's `src/` makes the store's
        `project-env` interpreter import THAT copy of `manifest_agent`,
        proven here with a fake candidate carrying a marker no real copy
        of the package has."""
        import subprocess

        store, lock, platform = self._provisioned_store(tmp_path)
        resolved = toolchain.resolve(
            "store:project-env/bin/python", lock=lock, store=store, platform=platform
        )
        assert isinstance(resolved, toolchain.ResolvedTool)

        candidate = tmp_path / "candidate"
        package_dir = candidate / "src" / "manifest_agent"
        package_dir.mkdir(parents=True)
        marker = "candidate-local-edit-42"
        (package_dir / "__init__.py").write_text(f'MARKER = "{marker}"\n')

        result = subprocess.run(
            [
                str(resolved.executable),
                "-c",
                "import manifest_agent; print(manifest_agent.MARKER)",
            ],
            env={"PATH": "", "PYTHONPATH": str(package_dir.parent)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == marker

    def test_config_env_manifest_script_runs_for_real(self, tmp_path: Path) -> None:
        """`config-env` IS installed for real: `bin/manifest --help`
        actually runs, proving the entry point `test.smoke.lite` needs
        exists and works, not merely that a file is present."""
        import subprocess

        store, lock, platform = self._provisioned_store(tmp_path)
        resolved = toolchain.resolve(
            "store:config-env/bin/manifest", lock=lock, store=store, platform=platform
        )
        assert isinstance(resolved, toolchain.ResolvedTool)
        result = subprocess.run(
            [str(resolved.executable), "--help"],
            env={"PATH": os.pathsep.join(str(p) for p in resolved.path_entries)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "manifest" in result.stdout.lower()


class TestProjectEnvStaleLockBlocks:
    """Offline: Correction 7 rule 4's staleness invariant, at the
    `resolve()` layer these bundles share with every other `python-env`."""

    def _lock(self, sha256: str) -> dict:
        return {
            "schema_version": 1,
            "tools": {
                "project-env": {
                    "kind": "python-env",
                    "version": "pyproject.toml",
                    "platforms": {
                        "linux-x64": {
                            "url": "file://uv.lock",
                            "sha256": sha256,
                            "exe_sha256": "a" * 64,
                            "path_in_archive": ".",
                            "console_scripts": ["bin/python"],
                        }
                    },
                }
            },
        }

    def test_a_lock_sha256_the_store_never_synced_against_blocks_as_stale(
        self, tmp_path: Path
    ) -> None:
        """Simulates a bumped root `uv.lock` (the lock's declared `sha256`
        changed) that the store has not been re-provisioned against yet:
        `resolve()` must BLOCK "store stale", never silently resolve the
        now-outdated env as if it still matched."""
        store = tmp_path / "store"
        provisioned_lock = self._lock("a" * 64)
        manifest = {
            "schema_version": 1,
            "lock_digest": toolchain.lock_digest(provisioned_lock),
            "tools": {
                "project-env": {
                    "source_sha256": "a" * 64,
                    "executables": {
                        "bin/python": {"path": "tools/project-env/x/bin/python"}
                    },
                }
            },
        }
        store.mkdir(parents=True)
        (store / "manifest.json").write_text(json.dumps(manifest))

        bumped_lock = self._lock("b" * 64)  # the lockfile moved on
        outcome = toolchain.resolve(
            "store:project-env/bin/python",
            lock=bumped_lock,
            store=store,
            platform="linux-x64",
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: store stale (lock changed)"
        )

    def test_unprovisioned_project_env_blocks_as_not_provisioned(
        self, tmp_path: Path
    ) -> None:
        store = tmp_path / "store"
        lock = self._lock("a" * 64)
        outcome = toolchain.resolve(
            "store:project-env/bin/python", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: project-env not provisioned (run manifest provision)"
        )


class TestLongShebangTrampolineLauncher:
    """C7h finding: `config-env`'s `bin/manifest` -- and any other
    `python-env` console script -- gets a `#!/bin/sh` polyglot trampoline
    launcher instead of a plain `#!<python>` shebang whenever the real
    interpreter's absolute path is too long for the OS shebang-line limit
    (uv/pip's standard long-shebang workaround). A store living under a
    deeply nested temp dir (this repo's own `pytest tmp_path`, CI runners,
    some `XDG_*` layouts) hits this reliably. Before this fix,
    `launcher_target` read the trampoline's `#!/bin/sh` line, could not
    recognize it, and `launcher_inside_store` -- comparing `/bin/sh`
    against the store -- reported "outside the store", failing CLOSED as
    "digest mismatch" for a launcher that was actually fine."""

    def _trampoline(self, python_path: str) -> bytes:
        return (
            "#!/bin/sh\n"
            f"'''exec' '{python_path}' \"$0\" \"$@\"\n"
            "' '''\n"
            "# -*- coding: utf-8 -*-\n"
            "import sys\n"
        ).encode()

    def test_trampoline_launcher_resolves_to_the_real_interpreter_path(
        self, tmp_path: Path
    ) -> None:
        from manifest_agent.checks import toolchain_env as te

        store = tmp_path / "store"
        interpreter = store / "tools/config-env/x/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"fake-python")
        script = store / "tools/config-env/x/bin/manifest"
        script.write_bytes(self._trampoline(str(interpreter)))
        script.chmod(0o755)

        target = te.launcher_target(script)
        assert target is not None
        assert target.path == interpreter
        assert target.env_interpreter_name is None
        assert te.launcher_inside_store(target, store) is True

    def test_trampoline_pointing_outside_the_store_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """The fix must not become a blanket exemption: a trampoline whose
        embedded interpreter path is OUTSIDE the store is exactly the
        swap this check exists to catch, and must still be rejected."""
        from manifest_agent.checks import toolchain_env as te

        store = tmp_path / "store"
        store.mkdir()
        script = tmp_path / "manifest"
        script.write_bytes(self._trampoline("/usr/bin/python3"))
        script.chmod(0o755)

        target = te.launcher_target(script)
        assert target is not None
        assert target.path == Path("/usr/bin/python3")
        assert te.launcher_inside_store(target, store) is False

    def test_a_plain_bin_sh_script_that_is_not_the_trampoline_shape_is_untouched(
        self, tmp_path: Path
    ) -> None:
        """A genuine `#!/bin/sh` script (not the specific polyglot
        trampoline pattern) must still resolve to `/bin/sh` itself, not be
        mistaken for a launcher naming some other interpreter."""
        from manifest_agent.checks import toolchain_env as te

        script = tmp_path / "plain.sh"
        script.write_text("#!/bin/sh\necho hello\n")
        script.chmod(0o755)

        target = te.launcher_target(script)
        assert target is not None
        assert target.path == Path("/bin/sh")
        assert target.env_interpreter_name is None
