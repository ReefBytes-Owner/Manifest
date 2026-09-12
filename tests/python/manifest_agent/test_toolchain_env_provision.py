"""`python-env`/`node-env` trust-anchor coverage (C7b/C7c): the distribution-
set digest is a pure function of on-disk bytes (no network, no lock, no
store manifest -- built here from hand-written RECORD/`.package-lock.json`
fixtures), and every `resolve()` failure mode Correction 3 rule 3
enumerates is exercised offline: unattested, store stale, digest mismatch
(a tampered RECORD / tampered `.package-lock.json`), a launcher pointing
outside the store, and a missing console script.

One test (`TestRealMaterializationNeverUsesAmbientEngines`) runs the real
provisioner with `PATH=""`, network-gated on `MANIFEST_C7B_NETWORK=1`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from manifest_agent.checks import (
    toolchain,
)
from manifest_agent.checks import (
    toolchain_env as te,
)
from manifest_agent.checks import (
    toolchain_provision as tp,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
_NETWORK = os.environ.get("MANIFEST_C7B_NETWORK") == "1"
_NETWORK_SKIP = "set MANIFEST_C7B_NETWORK=1 to materialize the real envs"


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _one_env_bundle_lock(kind: str, url: str, source_sha256: str) -> dict:
    """A `linux-x64`-only lock with one env-kind bundle and no other tools --
    shared by the "blocks before touching the store" tests below."""
    return {
        "schema_version": 1,
        "tools": {
            kind: {
                "kind": kind,
                "version": "x",
                "platforms": {
                    "linux-x64": {
                        "url": url,
                        "sha256": source_sha256,
                        "exe_sha256": "a" * 64,
                        "path_in_archive": ".",
                    }
                },
            }
        },
    }


def _fake_python_env(root: Path) -> None:
    record = root / "lib/python3.11/site-packages/demo-1.0.dist-info/RECORD"
    _write(record, b"demo/__init__.py,sha256=abc,10\n../../../bin/demo,sha256=xyz,20\n")
    _write(root / "bin/python", b"fake-interpreter")
    (root / "bin/demo").write_text(f"#!{root / 'bin/python'}\ndemo\n")
    (root / "bin/demo").chmod(0o700)


def _fake_node_env(root: Path) -> None:
    lock_doc = {
        "packages": {
            "": {"name": "demo"},
            "node_modules/leftpad": {"version": "1.0.0", "integrity": "sha512-x"},
        }
    }
    _write(
        root / "node_modules" / ".package-lock.json",
        json.dumps(lock_doc).encode(),
    )
    bin_dir = root / "node_modules" / "leftpad" / "bin"
    _write(bin_dir / "leftpad", b"#!/usr/bin/env node\n")
    (root / "node_modules" / ".bin").mkdir(parents=True, exist_ok=True)
    (root / "node_modules" / ".bin" / "leftpad").symlink_to(
        os.path.relpath(bin_dir / "leftpad", root / "node_modules" / ".bin")
    )


class TestDistributionSetDigestIsPure:
    """No network, no lock, no store manifest -- a pure function of bytes."""

    def test_python_digest_is_deterministic_and_location_independent(
        self, tmp_path: Path
    ):
        root_a = tmp_path / "a"
        root_b = tmp_path / "somewhere" / "else" / "b"
        _fake_python_env(root_a)
        _fake_python_env(root_b)
        assert te.distribution_set_digest(
            root_a, "python-env"
        ) == te.distribution_set_digest(root_b, "python-env")

    def test_python_digest_changes_when_a_record_is_tampered(self, tmp_path: Path):
        root = tmp_path / "env"
        _fake_python_env(root)
        before = te.distribution_set_digest(root, "python-env")
        record = root / "lib/python3.11/site-packages/demo-1.0.dist-info/RECORD"
        record.write_bytes(record.read_bytes() + b"tampered-extra-line\n")
        after = te.distribution_set_digest(root, "python-env")
        assert before != after

    def test_python_digest_ignores_the_generated_launcher_line(self, tmp_path: Path):
        """The launcher line (`../../../bin/demo,...`) is excluded -- its
        bytes embed a store-location-dependent shebang, so including it
        would make the anchor non-reproducible across two otherwise-
        identical materializations (the real regression this test pins)."""
        root = tmp_path / "env"
        _fake_python_env(root)
        before = te.distribution_set_digest(root, "python-env")
        (root / "bin/demo").write_text("#!/some/totally/different/path\ndemo\n")
        after = te.distribution_set_digest(root, "python-env")
        assert before == after

    def test_node_digest_is_deterministic_and_changes_on_tamper(self, tmp_path: Path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        _fake_node_env(root_a)
        _fake_node_env(root_b)
        digest_a = te.distribution_set_digest(root_a, "node-env")
        assert digest_a == te.distribution_set_digest(root_b, "node-env")
        lock_path = root_a / "node_modules" / ".package-lock.json"
        doc = json.loads(lock_path.read_text())
        doc["packages"]["node_modules/leftpad"]["integrity"] = "sha512-tampered"
        lock_path.write_text(json.dumps(doc))
        assert te.distribution_set_digest(root_a, "node-env") != digest_a

    def test_unknown_kind_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="unknown env kind"):
            te.distribution_set_digest(tmp_path, "rust-env")


def _lock_and_manifest(
    store: Path, *, kind: str, exe_sha256: str | None, digest_present: bool = True
) -> dict:
    bundle = kind
    lock = {
        "schema_version": 1,
        "tools": {
            bundle: {
                "kind": kind,
                "version": "x",
                "platforms": {
                    "linux-x64": {
                        "url": f"file://config/toolchain/{'uv.lock' if kind == 'python-env' else 'package-lock.json'}",
                        "sha256": "a" * 64,
                        "exe_sha256": exe_sha256,
                        "path_in_archive": ".",
                    }
                },
            }
        },
    }
    if digest_present:
        store.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "lock_digest": toolchain.lock_digest(lock),
            "tools": {
                bundle: {
                    "source_sha256": "a" * 64,
                    "executables": {"bin/demo": {"path": f"tools/{bundle}/x/bin/demo"}}
                    if kind == "python-env"
                    else {
                        "bin/leftpad": {
                            "path": f"tools/{bundle}/x/node_modules/.bin/leftpad"
                        }
                    },
                }
            },
        }
        (store / "manifest.json").write_text(json.dumps(manifest))
    return lock


class TestEnvKindResolveFailureModes:
    def test_unattested_env_blocks_before_touching_the_store(self, tmp_path: Path):
        store = tmp_path / "store"
        lock = _lock_and_manifest(store, kind="python-env", exe_sha256=None)
        outcome = toolchain.resolve(
            "store:python-env/bin/demo", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: python-env unattested for linux-x64"
        )

    def test_stale_store_blocks_on_lock_digest_mismatch(self, tmp_path: Path):
        store = tmp_path / "store"
        env_root = store / "tools/python-env/x"
        _fake_python_env(env_root)
        digest = te.distribution_set_digest(env_root, "python-env")
        lock = _lock_and_manifest(store, kind="python-env", exe_sha256=digest)
        # Mutate the lock after the manifest was written -- lock_digest drifts.
        lock["tools"]["python-env"]["version"] = "y"
        outcome = toolchain.resolve(
            "store:python-env/bin/demo", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: store stale (lock changed)"
        )

    def test_tampered_record_blocks_as_digest_mismatch(self, tmp_path: Path):
        store = tmp_path / "store"
        env_root = store / "tools/python-env/x"
        _fake_python_env(env_root)
        digest = te.distribution_set_digest(env_root, "python-env")
        lock = _lock_and_manifest(store, kind="python-env", exe_sha256=digest)
        record = env_root / "lib/python3.11/site-packages/demo-1.0.dist-info/RECORD"
        record.write_bytes(record.read_bytes() + b"swapped-dependency\n")
        outcome = toolchain.resolve(
            "store:python-env/bin/demo", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: python-env digest mismatch"
        )

    def test_launcher_outside_store_blocks_as_digest_mismatch(self, tmp_path: Path):
        store = tmp_path / "store"
        env_root = store / "tools/python-env/x"
        _fake_python_env(env_root)
        digest = te.distribution_set_digest(env_root, "python-env")
        lock = _lock_and_manifest(store, kind="python-env", exe_sha256=digest)
        outside = tmp_path / "outside" / "python"
        _write(outside, b"not-the-store-python")
        (env_root / "bin/demo").write_text(f"#!{outside}\ndemo\n")
        assert te.distribution_set_digest(env_root, "python-env") == digest
        outcome = toolchain.resolve(
            "store:python-env/bin/demo", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: python-env digest mismatch"
        )

    def test_missing_console_script_blocks_as_not_provisioned(self, tmp_path: Path):
        store = tmp_path / "store"
        env_root = store / "tools/python-env/x"
        _fake_python_env(env_root)
        digest = te.distribution_set_digest(env_root, "python-env")
        lock = _lock_and_manifest(store, kind="python-env", exe_sha256=digest)
        (env_root / "bin/demo").unlink()
        outcome = toolchain.resolve(
            "store:python-env/bin/demo", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: python-env not provisioned (run manifest provision)"
        )

    def test_tampered_node_package_lock_blocks_as_digest_mismatch(self, tmp_path: Path):
        store = tmp_path / "store"
        env_root = store / "tools/node-env/x"
        _fake_node_env(env_root)
        digest = te.distribution_set_digest(env_root, "node-env")
        lock = _lock_and_manifest(store, kind="node-env", exe_sha256=digest)
        lock_path = env_root / "node_modules" / ".package-lock.json"
        doc = json.loads(lock_path.read_text())
        doc["packages"]["node_modules/leftpad"]["integrity"] = "sha512-swapped"
        lock_path.write_text(json.dumps(doc))
        outcome = toolchain.resolve(
            "store:node-env/bin/leftpad", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason("toolchain: node-env digest mismatch")


class TestProvisionerNeverFallsBackToAmbientEngines:
    def test_python_env_provisioning_blocks_when_uv_is_not_provisioned(
        self, tmp_path: Path
    ):
        """No `uv` bundle -> `blocked`, never an ambient `uv` fallback."""
        store = tmp_path / "store"
        pyproject = REPO_ROOT / "config" / "toolchain" / "uv.lock"
        source_sha256 = hashlib.sha256(pyproject.read_bytes()).hexdigest()
        lock = _one_env_bundle_lock(
            "python-env", "file://config/toolchain/uv.lock", source_sha256
        )
        outcomes = tp.provision(
            lock,
            store,
            platform="linux-x64",
            fetcher=lambda url: b"",
            repo_root=REPO_ROOT,
            env={"PATH": ""},
        )
        assert outcomes[0].status == "blocked"
        assert "uv" in outcomes[0].reason

    def test_unattested_env_materializes_and_reports_digest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        repo_root = tmp_path / "repo"
        source = repo_root / "config/toolchain/uv.lock"
        _write(source, b"locked")
        lock = _one_env_bundle_lock(
            "python-env",
            "file://config/toolchain/uv.lock",
            hashlib.sha256(source.read_bytes()).hexdigest(),
        )
        lock["tools"]["python-env"]["platforms"]["linux-x64"]["exe_sha256"] = None

        def materialize_env(_ctx, _bundle, _entry, env_root, _names):
            _fake_python_env(env_root)
            return {"python": str(env_root / "bin/python")}

        monkeypatch.setattr(tp, "_materialize_env", materialize_env)
        outcomes = tp.provision(
            lock,
            tmp_path / "store",
            platform="linux-x64",
            only=frozenset({"python-env"}),
            repo_root=repo_root,
        )

        assert outcomes[0].status == "provisioned"
        assert outcomes[0].digest == te.distribution_set_digest(
            tmp_path
            / "store/tools/python-env"
            / hashlib.sha256(source.read_bytes()).hexdigest()[:16],
            "python-env",
            store=tmp_path / "store",
            checkout_root=repo_root,
        )

    def test_a_missing_file_url_source_blocks_instead_of_crashing(self, tmp_path: Path):
        """C7g (ee640879): a missing `file://` lock source must BLOCK with a
        reason, never raise a raw `FileNotFoundError`."""
        store = tmp_path / "store"
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        lock = _one_env_bundle_lock(
            "node-env", "file://config/toolchain/package-lock.json", "a" * 64
        )
        outcomes = tp.provision(
            lock,
            store,
            platform="linux-x64",
            fetcher=lambda url: b"",
            repo_root=repo_root,
            env={"PATH": ""},
        )
        assert outcomes[0].status == "blocked"
        assert "config/toolchain/package-lock.json" in outcomes[0].reason
        assert not store.exists() or not any(store.rglob("*"))


@pytest.mark.skipif(not _NETWORK, reason=_NETWORK_SKIP)
class TestRealMaterializationNeverUsesAmbientEngines:
    def test_path_empty_still_materializes_via_store_uv_and_node(self, tmp_path: Path):
        """The real provisioner, with `PATH=""` in the child environment it
        builds for uv/node -- proving neither is found via an ambient PATH
        search, only through `toolchain.resolve()`'s hash-verified store
        lookup."""
        from manifest_agent.checks import toolchain_provision as provision_mod

        store = tmp_path / "store"
        real_lock = json.loads(
            (REPO_ROOT / "config" / "toolchain.lock.json").read_text()
        )
        platform = toolchain.current_platform()
        binaries = provision_mod.provision(
            real_lock,
            store,
            platform=platform,
            only=frozenset({"uv", "node"}),
            repo_root=REPO_ROOT,
            env={"PATH": ""},
        )
        assert all(o.status == "provisioned" for o in binaries), binaries
        envs = provision_mod.provision(
            real_lock,
            store,
            platform=platform,
            only=frozenset({"python-env", "node-env"}),
            repo_root=REPO_ROOT,
            env={"PATH": ""},
        )
        assert all(o.status == "provisioned" for o in envs), envs


class TestNodeEnvEnvNodeShebangUsesTheStoreInterpreter:
    """Coordinator round 2, defect 2: npm's own generated
    `node_modules/.bin/*` launchers are symlinks to a `.js`/`.mjs` file
    whose OWN shebang is `#!/usr/bin/env node` -- the OS resolves `node` via
    `env`'s PATH search at exec time, which is invisible to
    `toolchain.resolve()`'s launcher inspection. Every `node-env` console
    script must therefore carry the store's own `node` as its `interpreter`
    and put its bin dir on `PATH`, unconditionally -- never leaving `node`
    to be found (or not found) by an ambient search.
    """

    def _fake_node_env_with_store_node(self, store: Path) -> tuple[dict, Path, Path]:
        node_relative = "tools/node/24.9.0/bin/node"
        node_sha = hashlib.sha256(b"fake-node-binary").hexdigest()
        (store / node_relative).parent.mkdir(parents=True)
        (store / node_relative).write_bytes(b"fake-node-binary")
        (store / node_relative).chmod(0o700)

        env_root = store / "tools/node-env/x"
        _fake_node_env(env_root)
        digest = te.distribution_set_digest(env_root, "node-env")

        lock = {
            "schema_version": 1,
            "tools": {
                "node": {
                    "kind": "binary",
                    "version": "24.9.0",
                    "platforms": {
                        "linux-x64": {
                            "url": "https://example.invalid/node.tar.xz",
                            "sha256": node_sha,
                            "exe_sha256": node_sha,
                            "path_in_archive": "node",
                        }
                    },
                },
                "node-env": {
                    "kind": "node-env",
                    "version": "x",
                    "platforms": {
                        "linux-x64": {
                            "url": "file://config/toolchain/package-lock.json",
                            "sha256": "a" * 64,
                            "exe_sha256": digest,
                            "path_in_archive": ".",
                        }
                    },
                },
            },
        }
        manifest = {
            "schema_version": 1,
            "lock_digest": toolchain.lock_digest(lock),
            "tools": {
                "node": {
                    "source_sha256": node_sha,
                    "executables": {"bin/node": {"path": node_relative}},
                },
                "node-env": {
                    "source_sha256": "a" * 64,
                    "executables": {
                        "bin/leftpad": {
                            "path": "tools/node-env/x/node_modules/.bin/leftpad"
                        }
                    },
                },
            },
        }
        (store / "manifest.json").write_text(json.dumps(manifest))
        return lock, store / node_relative, env_root

    def test_node_env_resolution_carries_the_store_node_as_interpreter(
        self, tmp_path: Path
    ):
        store = tmp_path / "store"
        lock, node_exe, _env_root = self._fake_node_env_with_store_node(store)
        outcome = toolchain.resolve(
            "store:node-env/bin/leftpad", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(outcome, toolchain.ResolvedTool)
        assert outcome.interpreter == node_exe
        assert node_exe.parent in outcome.path_entries

    def test_node_env_blocks_when_node_itself_is_unattested(self, tmp_path: Path):
        """Never a silent fall-back to an ambient `node` -- if the store's
        own `node` bundle cannot be resolved, the whole node-env resolution
        BLOCKs too."""
        store = tmp_path / "store"
        lock, _node_exe, _env_root = self._fake_node_env_with_store_node(store)
        lock["tools"]["node"]["platforms"]["linux-x64"]["exe_sha256"] = None
        outcome = toolchain.resolve(
            "store:node-env/bin/leftpad", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(outcome, toolchain.BlockedReason)
        assert "toolchain:" in outcome.reason
