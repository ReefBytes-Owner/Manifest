"""`toolchain.resolve()` failure-semantics table and trust-anchoring coverage.

Split out of `test_toolchain.py` to stay under the per-class size/method
ceilings once the trust-boundary fix (lock-anchored `exe_sha256`, untrusted
store-provided paths) added new rows.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from manifest_agent.checks import toolchain
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

    def test_interpreter_hash_is_verified_for_python_env(self, tmp_path: Path):
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
                            "exe_sha256": exe_sha,
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
