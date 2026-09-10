"""`manifest provision` with an injectable fetcher -- no network, ever.

Every test drives `provision()`/`import_binary()`/`validate_offline()` with
bytes it built locally (a tiny tar.gz fixture, or a plain file for
`--import`), asserting the real code path end to end.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

from manifest_agent.checks import toolchain
from manifest_agent.checks import toolchain_provision as provision_mod


def _tar_gz_with(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name=name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _lock_for(bundle: str, sha256: str, *, path_in_archive: str = "demo") -> dict:
    return {
        "schema_version": 1,
        "tools": {
            bundle: {
                "kind": "binary",
                "version": "1.0.0",
                "platforms": {
                    "linux-x64": {
                        "url": f"https://example.invalid/{bundle}.tar.gz",
                        "sha256": sha256,
                        "path_in_archive": path_in_archive,
                    }
                },
            }
        },
    }


class TestProvisionBinary:
    def test_provision_downloads_hashes_and_records_manifest(self, tmp_path):
        content = b"#!/bin/sh\necho demo\n"
        archive = _tar_gz_with("demo", content)
        sha = hashlib.sha256(archive).hexdigest()
        lock = _lock_for("demo", sha)
        store = tmp_path / "store"

        outcomes = provision_mod.provision(
            lock, store, platform="linux-x64", fetcher=lambda url: archive
        )
        assert outcomes == [provision_mod.ProvisionOutcome("demo", "provisioned")]

        exe_path = store / "tools/demo/1.0.0/bin/demo"
        assert exe_path.read_bytes() == content
        manifest = json.loads((store / "manifest.json").read_text())
        assert manifest["tools"]["demo"]["executables"]["bin/demo"]["sha256"] == (
            hashlib.sha256(content).hexdigest()
        )

        # And resolve() now succeeds against exactly this store + lock.
        resolved = toolchain.resolve(
            "store:demo/bin/demo", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(resolved, toolchain.ResolvedTool)

    def test_provision_refuses_on_download_hash_mismatch(self, tmp_path):
        archive = _tar_gz_with("demo", b"real")
        lock = _lock_for("demo", "0" * 64)  # deliberately wrong
        store = tmp_path / "store"
        outcomes = provision_mod.provision(
            lock, store, platform="linux-x64", fetcher=lambda url: archive
        )
        assert outcomes == [
            provision_mod.ProvisionOutcome(
                "demo", "blocked", "toolchain: demo digest mismatch"
            )
        ]
        assert not (store / "tools").exists()

    def test_provision_unattested_platform_refuses(self, tmp_path):
        lock = _lock_for("demo", "1" * 64)
        store = tmp_path / "store"
        outcomes = provision_mod.provision(
            lock, store, platform="darwin-arm64", fetcher=lambda url: b""
        )
        assert outcomes == [
            provision_mod.ProvisionOutcome(
                "demo", "blocked", "toolchain: demo unattested for darwin-arm64"
            )
        ]

    def test_only_filters_to_named_bundles(self, tmp_path):
        content = b"demo"
        archive = _tar_gz_with("demo", content)
        sha = hashlib.sha256(archive).hexdigest()
        lock = _lock_for("demo", sha)
        lock["tools"]["other"] = lock["tools"]["demo"]
        store = tmp_path / "store"
        outcomes = provision_mod.provision(
            lock,
            store,
            platform="linux-x64",
            only=frozenset({"demo"}),
            fetcher=lambda u: archive,
        )
        assert [outcome.bundle for outcome in outcomes] == ["demo"]

    def test_unimplemented_kind_is_reported_not_faked(self, tmp_path):
        lock = {
            "schema_version": 1,
            "tools": {
                "python-env": {
                    "kind": "python-env",
                    "version": "x",
                    "platforms": {
                        "linux-x64": {
                            "url": "file://x",
                            "sha256": "2" * 64,
                            "path_in_archive": ".",
                        }
                    },
                }
            },
        }
        outcomes = provision_mod.provision(
            lock, tmp_path / "store", platform="linux-x64", fetcher=lambda u: b""
        )
        assert outcomes[0].status == "blocked"
        assert "not yet provisionable" in outcomes[0].reason


class TestImportBinary:
    def test_import_adopts_when_hash_matches(self, tmp_path):
        content = b"real launcher bytes"
        sha = hashlib.sha256(content).hexdigest()
        lock = _lock_for("demo", sha)
        source = tmp_path / "external-binary"
        source.write_bytes(content)
        store = tmp_path / "store"
        ctx = provision_mod.ProvisionContext(store, lock, "linux-x64")
        outcome = provision_mod.import_binary(
            ctx, "demo", lock["tools"]["demo"], source
        )
        assert outcome == provision_mod.ProvisionOutcome("demo", "provisioned")
        resolved = toolchain.resolve(
            "store:demo/bin/demo", lock=lock, store=store, platform="linux-x64"
        )
        assert isinstance(resolved, toolchain.ResolvedTool)

    def test_import_refuses_when_hash_does_not_match(self, tmp_path):
        source = tmp_path / "external-binary"
        source.write_bytes(b"not the pinned bytes")
        lock = _lock_for("demo", "f" * 64)
        store = tmp_path / "store"
        ctx = provision_mod.ProvisionContext(store, lock, "linux-x64")
        outcome = provision_mod.import_binary(
            ctx, "demo", lock["tools"]["demo"], source
        )
        assert outcome == provision_mod.ProvisionOutcome(
            "demo", "blocked", "toolchain: demo digest mismatch"
        )
        assert not (store / "tools").exists()

    def test_import_refuses_when_source_missing(self, tmp_path):
        lock = _lock_for("demo", "a" * 64)
        store = tmp_path / "store"
        ctx = provision_mod.ProvisionContext(store, lock, "linux-x64")
        outcome = provision_mod.import_binary(
            ctx, "demo", lock["tools"]["demo"], tmp_path / "does-not-exist"
        )
        assert outcome.status == "blocked"
        assert "import source missing" in outcome.reason


class TestValidateOffline:
    def test_empty_store_against_attested_lock_is_incomplete(self, tmp_path):
        lock = _lock_for("demo", "b" * 64)
        complete, problems = provision_mod.validate_offline(
            lock, tmp_path / "store", "linux-x64"
        )
        assert complete is False
        assert problems

    def test_fully_provisioned_store_is_complete(self, tmp_path):
        content = b"demo"
        archive = _tar_gz_with("demo", content)
        sha = hashlib.sha256(archive).hexdigest()
        lock = _lock_for("demo", sha)
        store = tmp_path / "store"
        provision_mod.provision(
            lock, store, platform="linux-x64", fetcher=lambda u: archive
        )
        complete, problems = provision_mod.validate_offline(lock, store, "linux-x64")
        assert complete is True
        assert problems == []

    def test_unattested_entries_do_not_count_against_completeness(self, tmp_path):
        lock = _lock_for("demo", None)
        complete, problems = provision_mod.validate_offline(
            lock, tmp_path / "store", "linux-x64"
        )
        assert complete is True
        assert problems == []

    def test_multi_console_script_bundle_checks_every_script(self, tmp_path):
        """A `python-env`/`node-env` bundle lists several console scripts
        under one lock entry; `--offline` must check each one, not assume a
        single `bin/<bundle>` executable the way `binary` kind bundles do."""
        lock = {
            "schema_version": 1,
            "tools": {
                "python-env": {
                    "kind": "python-env",
                    "version": "deadbeef",
                    "platforms": {
                        "linux-x64": {
                            "url": "file://config/toolchain/pyproject.toml",
                            "sha256": "a" * 64,
                            "path_in_archive": ".",
                            "console_scripts": ["bin/ruff", "bin/yamllint"],
                        }
                    },
                }
            },
        }
        store = tmp_path / "store"
        complete, problems = provision_mod.validate_offline(lock, store, "linux-x64")
        assert complete is False
        assert len(problems) == 2
        assert all(
            problem == "toolchain: python-env not provisioned (run manifest provision)"
            for problem in problems
        )
