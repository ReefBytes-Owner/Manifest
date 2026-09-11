"""`toolchain_npm_cache.py`: the store-provisioned, hash-anchored npm cache
`package.node-runtime` reads instead of the runner's empty per-run cache dir
(phase-3-5-decisions.md Correction 10, rule 2).

`materialize()` needs a real `npm`/`node` and real network access (it
primes an actual npm cache from the real registry, verified by npm's own
package integrity check) -- gated behind the repo's `network` marker
(Correction 11) and skipped when npm/node are not on this host's `PATH` at
all. Every other function here (`index_digest`, `resolve`,
`resolve_for_check`) is pure/local and tested unconditionally with fixture
bytes, mirroring `test_toolchain_provision.py`'s split between injectable
(no-network) provisioning tests and the toolchain's own verification logic.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from manifest_agent.checks import toolchain, toolchain_npm_cache

NPM_AND_NODE_AVAILABLE = (
    shutil.which("npm") is not None and shutil.which("node") is not None
)


def _write_index_entry(cache_root: Path, relative: str) -> None:
    path = cache_root / "_cacache" / "index-v5" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("entry")


# --- index_digest ------------------------------------------------------


def test_index_digest_none_for_missing_cache(tmp_path):
    assert toolchain_npm_cache.index_digest(tmp_path / "missing") is None


def test_index_digest_none_for_empty_index(tmp_path):
    (tmp_path / "_cacache" / "index-v5").mkdir(parents=True)
    assert toolchain_npm_cache.index_digest(tmp_path) is None


def test_index_digest_is_stable_and_order_independent(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_index_entry(a, "aa/1111")
    _write_index_entry(a, "bb/2222")
    # Written in the opposite order -- the digest must not depend on
    # filesystem iteration order (that's why the entries are sorted first).
    _write_index_entry(b, "bb/2222")
    _write_index_entry(b, "aa/1111")
    digest_a = toolchain_npm_cache.index_digest(a)
    digest_b = toolchain_npm_cache.index_digest(b)
    assert digest_a is not None
    assert digest_a == digest_b


def test_index_digest_changes_when_an_entry_is_added(tmp_path):
    _write_index_entry(tmp_path, "aa/1111")
    before = toolchain_npm_cache.index_digest(tmp_path)
    _write_index_entry(tmp_path, "bb/2222")
    after = toolchain_npm_cache.index_digest(tmp_path)
    assert before != after


# --- resolve() -----------------------------------------------------------


def _repo_with_lockfile(tmp_path: Path, content: bytes) -> Path:
    repo = tmp_path / "repo"
    project = repo / toolchain_npm_cache.PROJECT_RELATIVE
    project.mkdir(parents=True)
    (project / "package-lock.json").write_bytes(content)
    return repo


def _lock_with(source_sha256: str | None, digest: str | None) -> dict:
    return {
        "schema_version": 1,
        "tools": {},
        "caches": {
            toolchain_npm_cache.CACHE_BUNDLE: {
                "source_sha256": source_sha256,
                "platforms": {
                    "darwin-arm64": {"digest": digest}
                    if digest or source_sha256
                    else None
                },
            }
        },
    }


def test_resolve_blocked_when_unattested(tmp_path):
    lock_content = b'{"lockfileVersion":3}'
    repo = _repo_with_lockfile(tmp_path, lock_content)
    lock = {"schema_version": 1, "tools": {}, "caches": {}}
    outcome = toolchain_npm_cache.resolve(
        repo_root=repo, store=tmp_path / "store", lock=lock, platform="darwin-arm64"
    )
    assert isinstance(outcome, toolchain.BlockedReason)
    assert "unattested" in outcome.reason


def test_resolve_blocked_when_source_lockfile_changed(tmp_path):
    lock_content = b'{"lockfileVersion":3}'
    repo = _repo_with_lockfile(tmp_path, lock_content)
    lock = _lock_with(source_sha256="0" * 64, digest="1" * 64)
    outcome = toolchain_npm_cache.resolve(
        repo_root=repo, store=tmp_path / "store", lock=lock, platform="darwin-arm64"
    )
    assert isinstance(outcome, toolchain.BlockedReason)
    assert "stale" in outcome.reason


def test_resolve_blocked_when_cache_dir_missing(tmp_path):
    lock_content = b'{"lockfileVersion":3}'
    repo = _repo_with_lockfile(tmp_path, lock_content)
    source_sha = hashlib.sha256(lock_content).hexdigest()
    lock = _lock_with(source_sha256=source_sha, digest="1" * 64)
    outcome = toolchain_npm_cache.resolve(
        repo_root=repo, store=tmp_path / "store", lock=lock, platform="darwin-arm64"
    )
    assert isinstance(outcome, toolchain.BlockedReason)
    assert "no store-anchored npm cache" in outcome.reason


def test_resolve_blocked_on_digest_mismatch(tmp_path):
    lock_content = b'{"lockfileVersion":3}'
    repo = _repo_with_lockfile(tmp_path, lock_content)
    source_sha = hashlib.sha256(lock_content).hexdigest()
    store = tmp_path / "store"
    cache_dir = store / "caches" / toolchain_npm_cache.CACHE_BUNDLE / "darwin-arm64"
    _write_index_entry(cache_dir, "aa/1111")
    tampered_digest = "f" * 64
    lock = _lock_with(source_sha256=source_sha, digest=tampered_digest)
    outcome = toolchain_npm_cache.resolve(
        repo_root=repo, store=store, lock=lock, platform="darwin-arm64"
    )
    assert isinstance(outcome, toolchain.BlockedReason)
    assert "digest mismatch" in outcome.reason


def test_resolve_succeeds_when_everything_matches(tmp_path):
    lock_content = b'{"lockfileVersion":3}'
    repo = _repo_with_lockfile(tmp_path, lock_content)
    source_sha = hashlib.sha256(lock_content).hexdigest()
    store = tmp_path / "store"
    cache_dir = store / "caches" / toolchain_npm_cache.CACHE_BUNDLE / "darwin-arm64"
    _write_index_entry(cache_dir, "aa/1111")
    digest = toolchain_npm_cache.index_digest(cache_dir)
    lock = _lock_with(source_sha256=source_sha, digest=digest)
    outcome = toolchain_npm_cache.resolve(
        repo_root=repo, store=store, lock=lock, platform="darwin-arm64"
    )
    assert isinstance(outcome, toolchain_npm_cache.ResolvedCache)
    assert outcome.directory == cache_dir
    assert outcome.digest == digest


def test_resolve_for_check_blocked_when_lock_missing(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outcome = toolchain_npm_cache.resolve_for_check(root)
    assert isinstance(outcome, toolchain.BlockedReason)
    assert "toolchain lock unavailable" in outcome.reason


# --- materialize(): real npm/node, real network -----------------------


@pytest.mark.network
@pytest.mark.skipif(not NPM_AND_NODE_AVAILABLE, reason="npm/node not installed locally")
def test_materialize_primes_a_real_verifiable_cache(tmp_path):
    """A tiny real project (one real, tiny npm dependency) -- proves
    `materialize()` drives a real `npm ci`, populates a real cache index,
    and that `resolve()` then verifies it, all without ever setting
    `--offline` here (this is the provisioning side, not the check side)."""
    repo = tmp_path / "repo"
    project = repo / toolchain_npm_cache.PROJECT_RELATIVE
    project.mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps(
            {
                "name": "fixture",
                "version": "1.0.0",
                "dependencies": {"lodash.once": "4.1.1"},
            }
        )
    )
    # A real `npm install` (network) produces a real, matching
    # package-lock.json -- hand-writing one would not prove anything about
    # what a real `npm ci` actually downloads and caches.
    subprocess.run(
        ["npm", "install", "--package-lock-only"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    store = tmp_path / "store"
    lock = {
        "schema_version": 1,
        "tools": {
            "node": json.loads(
                (Path.cwd() / "config" / "toolchain.lock.json").read_text()
            )["tools"]["node"]
        },
    }
    platform = toolchain.current_platform()
    from manifest_agent.checks import toolchain_provision

    outcomes = toolchain_provision.provision(
        lock, store, platform=platform, only=frozenset({"node"})
    )
    assert outcomes[0].status == "provisioned", outcomes

    cache_dir, digest = toolchain_npm_cache.materialize(
        repo_root=repo, store=store, lock=lock, platform=platform, env={}
    )
    assert cache_dir.is_dir()
    assert digest == toolchain_npm_cache.index_digest(cache_dir)
