"""`toolchain.resolve()` failure-semantics table and trust-anchoring coverage.

Split out of `test_toolchain.py` to stay under the per-class size/method
ceilings once the trust-boundary fix (lock-anchored `exe_sha256`, untrusted
store-provided paths) added new rows.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from manifest_agent.checks import toolchain, toolchain_env
from tests.python.manifest_agent.toolchain_fixtures import (
    _lock,
    _provision_store,
    _StoreOverrides,
    _write,
)


class TestResolveFailureSemantics:
    """One test per row of the 3a failure-semantics table."""

    def test_unattested_platform_blocks(self, tmp_path: Path):
        lock = _lock(exe_sha256=None)
        store = tmp_path / "store"
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(outcome, toolchain.BlockedReason)
        assert outcome.reason == "toolchain: gitleaks unattested for linux-x64"

    def test_unattested_when_only_archive_sha256_is_set(self, tmp_path: Path):
        """`exe_sha256: null` is unattested even when the archive `sha256`
        is filled in -- the resolve-time anchor is `exe_sha256` alone."""
        lock = _lock(exe_sha256=None, sha256="a" * 64)
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks",
            lock=lock,
            store=tmp_path / "store",
            platform="linux-x64",
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: gitleaks unattested for linux-x64"
        )

    def test_unattested_when_platform_missing_from_lock(self, tmp_path: Path):
        lock = _lock(exe_sha256="a" * 64, platform="linux-x64")
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

    def test_store_missing_entry_blocks(self, tmp_path: Path):
        lock = _lock(exe_sha256="a" * 64)
        store = tmp_path / "store"  # never provisioned
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: gitleaks not provisioned (run manifest provision)"
        )

    def test_digest_mismatch_blocks(self, tmp_path: Path):
        store = tmp_path / "store"
        real_sha = hashlib.sha256(b"real bytes").hexdigest()
        lock = _lock(exe_sha256=real_sha)
        _provision_store(store, lock, _StoreOverrides(exe_bytes=b"real bytes"))
        # Mutate the file after provisioning, as if a launcher were swapped in.
        (store / "tools/gitleaks/8.30.1/bin/gitleaks").write_bytes(b"swapped launcher")
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason("toolchain: gitleaks digest mismatch")

    def test_store_manifest_self_attestation_is_not_trusted(self, tmp_path: Path):
        """The store rewrites its own manifest.json to agree with a swapped
        binary; resolve() must still BLOCK because the trust anchor is the
        LOCK's exe_sha256, never the store's self-reported hash."""
        store = tmp_path / "store"
        real_sha = hashlib.sha256(b"real bytes").hexdigest()
        lock = _lock(exe_sha256=real_sha)
        swapped = b"swapped launcher, but the store manifest lies about it"
        swapped_sha = hashlib.sha256(swapped).hexdigest()
        _provision_store(
            store,
            lock,
            _StoreOverrides(exe_bytes=swapped, manifest_exe_sha256=swapped_sha),
        )
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason("toolchain: gitleaks digest mismatch")

    def test_manifest_lock_digest_mismatch_blocks_store_stale(self, tmp_path: Path):
        store = tmp_path / "store"
        lock = _lock(exe_sha256="b" * 64)
        _provision_store(store, lock, _StoreOverrides(lock_digest="0" * 64))
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: store stale (lock changed)"
        )

    def test_source_sha_mismatch_blocks_store_stale(self, tmp_path: Path):
        store = tmp_path / "store"
        lock = _lock(exe_sha256="c" * 64)
        _provision_store(store, lock, _StoreOverrides(source_sha256="d" * 64))
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: store stale (lock changed)"
        )

    def test_absolute_path_in_store_manifest_is_rejected(self, tmp_path: Path):
        """The store manifest's `path` field is store-controlled and
        untrusted: an absolute path must never be joined onto the store
        root and followed."""
        store = tmp_path / "store"
        outside = tmp_path / "outside-the-store" / "gitleaks"
        _write(outside, b"escaped")
        real_sha = hashlib.sha256(b"real bytes").hexdigest()
        lock = _lock(exe_sha256=real_sha)
        manifest = {
            "schema_version": 1,
            "lock_digest": toolchain.lock_digest(lock),
            "tools": {
                "gitleaks": {
                    "source_sha256": real_sha,
                    "executables": {
                        "bin/gitleaks": {"path": str(outside), "sha256": real_sha}
                    },
                }
            },
        }
        store.mkdir(parents=True)
        (store / "manifest.json").write_text(json.dumps(manifest))
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: gitleaks not provisioned (run manifest provision)"
        )

    def test_correct_store_resolves_and_verifies_hash(self, tmp_path: Path):
        store = tmp_path / "store"
        exe_bytes = b"#!/bin/sh\necho gitleaks 8.30.1\n"
        exe_path = store / "tools/gitleaks/8.30.1/bin/gitleaks"
        real_sha = hashlib.sha256(exe_bytes).hexdigest()
        lock = _lock(exe_sha256=real_sha)
        _provision_store(store, lock, _StoreOverrides(exe_bytes=exe_bytes))
        outcome = toolchain.resolve(
            "store:gitleaks/bin/gitleaks", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(outcome, toolchain.ResolvedTool)
        assert outcome.executable == exe_path
        assert outcome.tool_sha256 == real_sha
        assert outcome.path_entries[0] == exe_path.parent


class TestResolveTrustAnchoring:
    """Partial provisioning and the python-env interpreter-hash path."""

    def _two_bundle_lock(self, gitleaks_sha: str) -> dict:
        return {
            "schema_version": 1,
            "tools": {
                "gitleaks": {
                    "kind": "binary",
                    "version": "8.30.1",
                    "platforms": {
                        "linux-x64": {
                            "url": "https://example.invalid/gitleaks",
                            "sha256": gitleaks_sha,
                            "exe_sha256": gitleaks_sha,
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
                            "exe_sha256": "e" * 64,
                            "path_in_archive": "shfmt",
                        }
                    },
                },
            },
        }

    def _provision_gitleaks_only(self, store: Path, lock: dict, sha: str) -> None:
        _write(store / "tools/gitleaks/8.30.1/bin/gitleaks", b"provisioned")
        manifest = {
            "schema_version": 1,
            "lock_digest": toolchain.lock_digest(lock),
            "tools": {
                "gitleaks": {
                    "source_sha256": sha,
                    "executables": {
                        "bin/gitleaks": {
                            "path": "tools/gitleaks/8.30.1/bin/gitleaks",
                            "sha256": sha,
                        }
                    },
                }
                # shfmt intentionally absent: not provisioned yet.
            },
        }
        (store / "manifest.json").write_text(json.dumps(manifest))

    def test_partial_store_blocks_only_the_missing_tool(self, tmp_path: Path):
        """Partial provisioning is per-tool, not per-store."""
        store = tmp_path / "store"
        real_sha = hashlib.sha256(b"provisioned").hexdigest()
        lock = self._two_bundle_lock(real_sha)
        self._provision_gitleaks_only(store, lock, real_sha)

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

    def _fake_python_env(
        self, store: Path, *, record_bytes: bytes = b"pkg==1.0"
    ) -> tuple[Path, str]:
        """A hand-built python-env: one dist-info RECORD (the distribution-set
        anchor -- Correction 3, rule 3) plus a `bin/ruff` console script whose
        shebang points at `bin/python` inside the same env. Returns
        `(source_sha256_placeholder, distribution_set_digest)`."""
        env_root = store / "tools/python-env/deadbeef"
        record_path = env_root / "lib/python3.11/site-packages/pkg-1.0.dist-info/RECORD"
        record_path.parent.mkdir(parents=True)
        record_path.write_bytes(record_bytes)
        _write(env_root / "bin/python", b"fake-interpreter-launcher")
        _write(env_root / "bin/ruff", f"#!{env_root / 'bin/python'}\nruff\n".encode())
        digest = toolchain_env.distribution_set_digest(env_root, "python-env")
        return env_root, digest

    def _fake_python_env_lock_and_manifest(self, store: Path, exe_sha256: str) -> dict:
        lock = {
            "schema_version": 1,
            "tools": {
                "python-env": {
                    "kind": "python-env",
                    "version": "deadbeef",
                    "platforms": {
                        "linux-x64": {
                            "url": "file://config/toolchain/uv.lock",
                            "sha256": "f" * 64,
                            "exe_sha256": exe_sha256,
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
                        "bin/ruff": {"path": "tools/python-env/deadbeef/bin/ruff"},
                        "bin/python": {"path": "tools/python-env/deadbeef/bin/python"},
                    },
                }
            },
        }
        (store / "manifest.json").write_text(json.dumps(manifest))
        return lock

    def test_python_env_resolves_when_distribution_digest_matches(self, tmp_path: Path):
        """Correction 3's replacement anchor: `exe_sha256` is the digest of
        the installed distribution set (every dist-info RECORD), not of one
        console script's bytes -- those embed a store-location-dependent
        interpreter path and can never match a committed lock."""
        store = tmp_path / "store"
        _, digest = self._fake_python_env(store)
        lock = self._fake_python_env_lock_and_manifest(store, digest)

        outcome = toolchain.resolve(
            "store:python-env/bin/ruff", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(outcome, toolchain.ResolvedTool)
        assert outcome.tool_sha256 == digest

    def test_python_env_blocks_on_tampered_record_digest_mismatch(self, tmp_path: Path):
        """Editing one installed distribution's RECORD -- the same signal a
        real dependency swap or supply-chain tamper would leave -- changes
        the distribution-set digest and BLOCKs, without ever touching the
        `bin/ruff` console script itself."""
        store = tmp_path / "store"
        env_root, digest = self._fake_python_env(store)
        lock = self._fake_python_env_lock_and_manifest(store, digest)

        record_path = env_root / "lib/python3.11/site-packages/pkg-1.0.dist-info/RECORD"
        record_path.write_bytes(b"pkg==2.0-tampered")

        outcome = toolchain.resolve(
            "store:python-env/bin/ruff", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: python-env digest mismatch"
        )

    def test_python_env_blocks_when_launcher_points_outside_store(self, tmp_path: Path):
        """A console script whose shebang was swapped to run a system/dev
        interpreter (outside `store`) BLOCKs even though the distribution-set
        digest still matches -- rule 3d, the launcher-provenance check."""
        store = tmp_path / "store"
        env_root, digest = self._fake_python_env(store)
        lock = self._fake_python_env_lock_and_manifest(store, digest)

        outside = tmp_path / "outside-python"
        outside.write_bytes(b"not-the-store")
        (env_root / "bin/ruff").write_text(f"#!{outside}\nruff\n")
        # The shebang change does not touch any dist-info RECORD, so the
        # distribution-set digest is unchanged -- isolating the assertion to
        # the launcher-provenance rule, not a digest coincidence.
        assert toolchain_env.distribution_set_digest(env_root, "python-env") == digest

        outcome = toolchain.resolve(
            "store:python-env/bin/ruff", lock=lock, store=store, platform="linux-x64"
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: python-env digest mismatch"
        )

    def test_python_env_bin_python_launcher_is_exempt_from_the_store_check(
        self, tmp_path: Path
    ):
        """`bin/python` is the venv interpreter itself -- a symlink to the
        ambient/uv-managed CPython by design (Correction 3, rule 2: pinning
        the interpreter's own bytes is out of scope). Every OTHER console
        script's shebang names `bin/python`, which IS inside the store, so
        this exemption cannot smuggle an outside launcher past the check for
        anything else (proven by the sibling test above)."""
        store = tmp_path / "store"
        env_root, digest = self._fake_python_env(store)
        lock = self._fake_python_env_lock_and_manifest(store, digest)
        real_python = tmp_path / "ambient-python3.11"
        real_python.write_bytes(b"real ambient interpreter")
        (env_root / "bin/python").unlink()
        (env_root / "bin/python").symlink_to(real_python)

        outcome = toolchain.resolve(
            "store:python-env/bin/python", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(outcome, toolchain.ResolvedTool)
