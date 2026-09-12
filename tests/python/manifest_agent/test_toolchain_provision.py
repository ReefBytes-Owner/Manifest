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
from pathlib import Path

from manifest_agent.checks import toolchain
from manifest_agent.checks import toolchain_provision as provision_mod


def _tar_gz_with(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name=name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _lock_for(
    bundle: str,
    sha256: str | None,
    *,
    exe_sha256: str | None = "unset",
    path_in_archive: str = "demo",
) -> dict:
    """`exe_sha256` (the lock's resolve-time trust anchor) defaults to
    `sha256` (the archive hash) unless a distinct value is given -- most
    fixtures here don't extract a differently-hashed file."""
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
                        "exe_sha256": sha256 if exe_sha256 == "unset" else exe_sha256,
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
        exe_sha = hashlib.sha256(content).hexdigest()
        lock = _lock_for("demo", sha, exe_sha256=exe_sha)
        store = tmp_path / "store"

        outcomes = provision_mod.provision(
            lock, store, platform="linux-x64", fetcher=lambda url: archive
        )
        assert outcomes == [provision_mod.ProvisionOutcome("demo", "provisioned")]

        exe_path = store / "tools/demo/1.0.0/bin/demo"
        assert exe_path.read_bytes() == content
        manifest = json.loads((store / "manifest.json").read_text())
        assert manifest["tools"]["demo"]["executables"]["bin/demo"]["sha256"] == exe_sha

        # And resolve() now succeeds against exactly this store + lock,
        # anchored on the lock's exe_sha256 -- not the store manifest's.
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

    def test_provision_unattested_when_exe_sha256_is_null(self, tmp_path):
        """The archive sha256 alone is not enough to provision: exe_sha256
        null is unattested too -- committed-but-unfilled locks (like the
        real `config/toolchain.lock.json` today) must stay BLOCKED."""
        lock = _lock_for("demo", "1" * 64, exe_sha256=None)
        outcomes = provision_mod.provision(
            lock, tmp_path / "store", platform="linux-x64", fetcher=lambda url: b""
        )
        assert outcomes == [
            provision_mod.ProvisionOutcome(
                "demo", "blocked", "toolchain: demo unattested for linux-x64"
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
                "rust-env": {
                    "kind": "rust-env",
                    "version": "x",
                    "platforms": {
                        "linux-x64": {
                            "url": "file://x",
                            "sha256": "2" * 64,
                            "exe_sha256": "2" * 64,
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

    def test_import_refuses_a_non_binary_kind_bundle(self, tmp_path):
        """`--import` only adopts `binary`-kind tools; a `python-env`/
        `node-env` bundle (multi-script, no single "the binary") must be
        refused, not silently mis-adopted as one raw file."""
        lock = _lock_for("demo", "a" * 64)
        lock["tools"]["demo"]["kind"] = "python-env"
        source = tmp_path / "external-binary"
        source.write_bytes(b"irrelevant")
        store = tmp_path / "store"
        ctx = provision_mod.ProvisionContext(store, lock, "linux-x64")
        outcome = provision_mod.import_binary(
            ctx, "demo", lock["tools"]["demo"], source
        )
        assert outcome.status == "blocked"
        assert "kind" in outcome.reason


def _tar_gz_with_many(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class TestExtraExecutables:
    """`node` gains `bin/npm` -- extracted from the SAME archive as `bin/node`
    (no second download), hashed against its OWN `exe_sha256` (`npm-cli.js`'s
    own bytes), never against the primary `node` tool's `exe_sha256`. Mirrors
    C7f's node-env `_npm-cli` extraction, but recorded at the plain `node`
    binary bundle so `store:node/bin/npm` resolves independently of the
    node-env project environment."""

    def _npm_lock(self, node_sha: str, npm_sha: str) -> dict:
        return {
            "schema_version": 1,
            "tools": {
                "node": {
                    "kind": "binary",
                    "version": "24.9.0",
                    "platforms": {
                        "linux-x64": {
                            "url": "https://example.invalid/node.tar.gz",
                            "sha256": "will-be-set",
                            "exe_sha256": node_sha,
                            "path_in_archive": "node-v24.9.0-linux-x64/bin/node",
                            "extra_executables": {
                                "npm": {
                                    "path_in_archive": "node-v24.9.0-linux-x64/lib/node_modules/npm",
                                    "executable_relative": "bin/npm-cli.js",
                                    "exe_sha256": npm_sha,
                                }
                            },
                        }
                    },
                }
            },
        }

    _NODE_CONTENT = b"#!/bin/sh\necho node\n"
    _NPM_CONTENT = b"#!/usr/bin/env node\nrequire('../lib/cli.js')(process)\n"

    def _fixture(
        self, npm_sha: str, *, npm_content: bytes | None = None
    ) -> tuple[dict, bytes]:
        """A ready-to-provision `(lock, archive)` pair: a two-member archive
        (`bin/node`, the npm subtree's `bin/npm-cli.js`) whose `sha256`
        fields are all derived from the fixture bytes -- `npm_sha` is the
        only value a caller injects, so a test can pin it wrong on purpose."""
        npm_bytes = self._NPM_CONTENT if npm_content is None else npm_content
        archive = _tar_gz_with_many(
            {
                "node-v24.9.0-linux-x64/bin/node": self._NODE_CONTENT,
                "node-v24.9.0-linux-x64/lib/node_modules/npm/bin/npm-cli.js": npm_bytes,
                "node-v24.9.0-linux-x64/lib/node_modules/npm/package.json": b"{}",
            }
        )
        lock = self._npm_lock(hashlib.sha256(self._NODE_CONTENT).hexdigest(), npm_sha)
        lock["tools"]["node"]["platforms"]["linux-x64"]["sha256"] = hashlib.sha256(
            archive
        ).hexdigest()
        return lock, archive

    def _resolve_both(self, lock: dict, store):
        node = toolchain.resolve(
            "store:node/bin/node", lock=lock, store=store, platform="linux-x64"
        )
        npm = toolchain.resolve(
            "store:node/bin/npm", lock=lock, store=store, platform="linux-x64"
        )
        return node, npm

    def test_provision_records_npm_alongside_node_hashed_independently(self, tmp_path):
        npm_sha = hashlib.sha256(self._NPM_CONTENT).hexdigest()
        lock, archive = self._fixture(npm_sha)
        store = tmp_path / "store"

        outcomes = provision_mod.provision(
            lock, store, platform="linux-x64", fetcher=lambda url: archive
        )
        assert outcomes == [provision_mod.ProvisionOutcome("node", "provisioned")]

        manifest = json.loads((store / "manifest.json").read_text())
        executables = manifest["tools"]["node"]["executables"]
        assert executables["bin/node"]["sha256"] != executables["bin/npm"]["sha256"]
        assert executables["bin/npm"]["sha256"] == npm_sha

        node_resolved, npm_resolved = self._resolve_both(lock, store)
        assert isinstance(node_resolved, toolchain.ResolvedTool)
        assert isinstance(npm_resolved, toolchain.ResolvedTool)
        # The store's own node directory is on npm's resolved PATH, ahead of
        # the `os.defpath` tail -- a nested `#!/usr/bin/env node` inside
        # npm-cli.js finds the store node, never an ambient one.
        assert node_resolved.executable.parent in npm_resolved.path_entries
        assert npm_resolved.path_entries.index(
            node_resolved.executable.parent
        ) < npm_resolved.path_entries.index(Path("/usr/bin"))

    def test_impostor_npm_cli_js_blocks_on_digest_mismatch(self, tmp_path):
        """A swapped `npm-cli.js` BLOCKs -- and, critically, does NOT affect
        `bin/node`'s own resolution, proving the two executables are
        verified against independent hashes."""
        npm_sha = hashlib.sha256(self._NPM_CONTENT).hexdigest()
        lock, archive = self._fixture(npm_sha)
        store = tmp_path / "store"
        provision_mod.provision(
            lock, store, platform="linux-x64", fetcher=lambda url: archive
        )

        impostor = b"#!/usr/bin/env node\nconsole.log('not really npm')\n"
        (store / "tools/node/24.9.0/_npm/bin/npm-cli.js").write_bytes(impostor)

        node_resolved, npm_resolved = self._resolve_both(lock, store)
        assert npm_resolved == toolchain.BlockedReason(
            "toolchain: node digest mismatch"
        )
        assert isinstance(node_resolved, toolchain.ResolvedTool)

    def test_extra_executable_digest_mismatch_at_provision_time_blocks(self, tmp_path):
        """The lock's declared `npm` hash is wrong for the archive's real
        `npm-cli.js` bytes -- provisioning itself must BLOCK, not silently
        record a hash nothing verified."""
        lock, archive = self._fixture("0" * 64)  # deliberately wrong
        store = tmp_path / "store"

        outcomes = provision_mod.provision(
            lock, store, platform="linux-x64", fetcher=lambda url: archive
        )
        assert outcomes == [
            provision_mod.ProvisionOutcome(
                "node", "blocked", "toolchain: node npm digest mismatch"
            )
        ]
        assert not (store / "manifest.json").is_file()

    def test_real_lock_node_npm_hashes_and_relative_paths(self):
        """The committed lock's `node` entries: `npm`'s `exe_sha256` differs
        from `node`'s own on every attested platform, and (being the same
        pure-JS shim) is identical across platforms."""
        lock_path = (
            Path(__file__).resolve().parents[3] / "config" / "toolchain.lock.json"
        )
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        node = lock["tools"]["node"]["platforms"]
        npm_hashes = set()
        for platform, entry in node.items():
            npm = entry["extra_executables"]["npm"]
            assert npm["exe_sha256"] != entry["exe_sha256"], platform
            npm_hashes.add(npm["exe_sha256"])
        assert len(npm_hashes) == 1  # a pure-JS shim: identical across platforms


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
        exe_sha = hashlib.sha256(content).hexdigest()
        lock = _lock_for("demo", sha, exe_sha256=exe_sha)
        store = tmp_path / "store"
        provision_mod.provision(
            lock, store, platform="linux-x64", fetcher=lambda u: archive
        )
        complete, problems = provision_mod.validate_offline(lock, store, "linux-x64")
        assert complete is True
        assert problems == []

    def test_unattested_entries_do_not_count_against_completeness(self, tmp_path):
        lock = _lock_for("demo", None, exe_sha256=None)
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
                            "exe_sha256": "a" * 64,
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
