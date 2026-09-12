"""Store-provisioned, hash-anchored npm cache for `package.node-runtime`
(phase-3-5-decisions.md Correction 10, rule 2).

`package.node-runtime` installs `plugins/stitch-design/runtime/node`'s own
locked dependencies with `npm ci --offline` (`dependency_checks.py::
_node_runtime`). The runner's per-check cache environment (`toolchain_cache.
cache_environment`, Correction 4/C7d) redirects `npm_config_cache` to a
FRESH, EMPTY per-run temp directory -- correct for keeping a check body from
writing into the candidate, but it means `--offline` has nothing to read
from and the check BLOCKs with `npm error code ENOTCACHED`. The honest fix
is not to go back online, and not to point `npm_config_cache` at the
developer's real `~/.npm` (an unattested, unverifiable, host-specific
source `manifest check` must never trust) -- it is a THIRD npm cache,
populated once by `manifest provision` (network permitted there, and only
there) from exactly `plugins/stitch-design/runtime/node/package-lock.json`,
hash-anchored so a check can verify it before trusting it.

`config/toolchain/package-lock.json` (the ``node-env`` bundle's own lock)
was the source Correction 10's prose named, but `package.node-runtime`
never installs that project -- it installs the stitch-design bundle, whose
lockfile shares only 8 of 71 packages with `node-env`'s. A cache built from
the wrong lockfile would still BLOCK "no store-anchored npm cache" on every
real run, which is not an honest source; this module builds the cache from
the lockfile the check actually installs.

Materialization reuses `npm ci`'s own integrity verification (SHA-512,
package-lock.json's `integrity` fields) rather than re-implementing a
per-package hash check: `npm ci --ignore-scripts` against a disposable
scratch copy of just `package.json`/`package-lock.json`, with
`npm_config_cache` pointed at the store, downloads and verifies every
package as a side effect of populating its own cache. The resulting
attestation digest is the sorted set of npm's own cache-index entries
(`_cacache/index-v5/**`) -- a content fingerprint of what the cache holds,
independent of npm's internal directory sharding.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import toolchain

CACHE_BUNDLE = "node-cache"
PROJECT_RELATIVE = "plugins/stitch-design/runtime/node"
_INDEX_SUBDIR = Path("_cacache") / "index-v5"
_MATERIALIZE_TIMEOUT_SECONDS = 300.0


class NpmCacheError(RuntimeError):
    """`node-cache` materialization failed (`manifest provision` only)."""


def index_digest(cache_root: Path) -> str | None:
    """sha256 over the sorted, cache-relative paths of every npm cache-index
    entry under ``cache_root`` -- ``None`` if the cache has no index yet
    (an empty or never-populated cache directory)."""
    index_dir = cache_root / _INDEX_SUBDIR
    if not index_dir.is_dir():
        return None
    entries = sorted(
        str(path.relative_to(cache_root))
        for path in index_dir.rglob("*")
        if path.is_file()
    )
    if not entries:
        return None
    hasher = hashlib.sha256()
    for entry in entries:
        hasher.update(entry.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def source_sha256(repo_root: Path) -> str:
    """sha256 of the exact ``package-lock.json`` bytes `package.node-runtime`
    installs from -- the lock's ``caches.node-cache.source_sha256`` anchor,
    and what a candidate's own copy is compared against at check time."""
    path = repo_root / PROJECT_RELATIVE / "package-lock.json"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_npm_for_materialize(
    lock: Mapping, store: Path, platform: str
) -> toolchain.ResolvedTool:
    """`node` and `npm`, both store-resolved -- `npm-cli.js` runs via a
    `#!/usr/bin/env node` shebang, so `node` must resolve too even though
    only `npm`'s `ResolvedTool` is returned."""
    resolved_node = toolchain.resolve(
        "store:node/bin/node", lock=lock, store=store, platform=platform
    )
    if isinstance(resolved_node, toolchain.BlockedReason):
        raise NpmCacheError(resolved_node.reason)
    resolved_npm = toolchain.resolve(
        "store:node/bin/npm", lock=lock, store=store, platform=platform
    )
    if isinstance(resolved_npm, toolchain.BlockedReason):
        raise NpmCacheError(resolved_npm.reason)
    return resolved_npm


def _npm_ci_into_cache(
    *,
    resolved_npm: toolchain.ResolvedTool,
    package_json_bytes: bytes,
    package_lock_bytes: bytes,
    cache_dir: Path,
    env: Mapping[str, str],
) -> None:
    """`npm ci --ignore-scripts` a scratch copy of the two lockfile bytes,
    with `npm_config_cache` pointed at `cache_dir` -- npm's own download and
    SHA-512 integrity verification populates the cache as a side effect."""
    with tempfile.TemporaryDirectory(prefix="manifest-npm-cache-scratch-") as name:
        scratch = Path(name)
        (scratch / "package.json").write_bytes(package_json_bytes)
        (scratch / "package-lock.json").write_bytes(package_lock_bytes)
        run_env = dict(env)
        run_env["PATH"] = os.pathsep.join(
            str(entry) for entry in resolved_npm.path_entries
        )
        run_env["npm_config_cache"] = str(cache_dir)
        try:
            result = subprocess.run(
                [str(resolved_npm.executable), "ci", "--ignore-scripts"],
                cwd=scratch,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=_MATERIALIZE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise NpmCacheError(f"npm ci could not execute: {error}") from error
        if result.returncode != 0:
            raise NpmCacheError(
                f"npm ci exited {result.returncode}: {result.stderr[-2000:]}"
            )


def materialize(
    *,
    repo_root: Path,
    store: Path,
    lock: Mapping,
    platform: str,
    env: Mapping[str, str],
) -> tuple[Path, str]:
    """`npm ci --ignore-scripts` a scratch copy of the stitch-design node
    project's lockfile with `npm_config_cache` pointed at a fresh store
    subdirectory -- network-permitted (this function is only ever reached
    from `manifest provision`, never `manifest check`, mirroring
    `toolchain_provision.py`'s own network boundary). Returns
    `(cache_dir, digest)`; raises `NpmCacheError` on any failure -- never a
    partial or unverifiable cache.
    """
    resolved_npm = _resolve_npm_for_materialize(lock, store, platform)
    project = repo_root / PROJECT_RELATIVE
    try:
        package_json_bytes = (project / "package.json").read_bytes()
        package_lock_bytes = (project / "package-lock.json").read_bytes()
    except OSError as error:
        raise NpmCacheError(f"node-cache source unavailable: {error}") from error

    cache_dir = store / "caches" / CACHE_BUNDLE / platform
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _npm_ci_into_cache(
        resolved_npm=resolved_npm,
        package_json_bytes=package_json_bytes,
        package_lock_bytes=package_lock_bytes,
        cache_dir=cache_dir,
        env=env,
    )

    digest = index_digest(cache_dir)
    if digest is None:
        raise NpmCacheError("node-cache: npm ci produced an empty cache index")
    return cache_dir, digest


@dataclass(frozen=True, slots=True)
class ResolvedCache:
    """A verified `node-cache` directory, ready for `npm_config_cache`."""

    directory: Path
    digest: str


def resolve(
    *, repo_root: Path, store: Path, lock: Mapping, platform: str
) -> ResolvedCache | toolchain.BlockedReason:
    """Hash-verified resolution of the store's `node-cache` -- never
    `~/.npm`, never a network fetch: `manifest check` only reads what
    `manifest provision` already verified and wrote, and re-verifies both
    that the candidate's own lockfile still matches what was cached AND
    that the cache's own contents still match its attested digest."""
    entry = (lock.get("caches") or {}).get(CACHE_BUNDLE)
    platform_entry = (entry or {}).get("platforms", {}).get(platform)
    attested_digest = (platform_entry or {}).get("digest") if platform_entry else None
    if entry is None or platform_entry is None or attested_digest is None:
        return toolchain.BlockedReason(
            f"toolchain: {CACHE_BUNDLE} unattested for {platform}"
        )
    try:
        actual_source = source_sha256(repo_root)
    except OSError as error:
        return toolchain.BlockedReason(
            f"toolchain: {CACHE_BUNDLE} source unavailable: {error}"
        )
    if actual_source != entry.get("source_sha256"):
        return toolchain.BlockedReason(
            f"toolchain: {CACHE_BUNDLE} stale (package-lock.json changed)"
        )
    cache_dir = store / "caches" / CACHE_BUNDLE / platform
    if not cache_dir.is_dir():
        return toolchain.BlockedReason(
            f"toolchain: no store-anchored npm cache for {CACHE_BUNDLE} "
            "(run manifest provision)"
        )
    actual_digest = index_digest(cache_dir)
    if actual_digest is None or actual_digest != attested_digest:
        return toolchain.BlockedReason(f"toolchain: {CACHE_BUNDLE} digest mismatch")
    return ResolvedCache(cache_dir, actual_digest)


def resolve_for_check(root: Path) -> ResolvedCache | toolchain.BlockedReason:
    """`resolve()` plus the same lock/store lookup every other project-check
    body does (`analysis_checks.resolve_scanner`, `toolchain_resolve.
    resolve_tool`) -- the one entry point `dependency_checks.py` calls."""
    lock_path = root / "config" / "toolchain.lock.json"
    try:
        lock = toolchain.load_lock_file(lock_path)
    except (OSError, ValueError) as error:
        return toolchain.BlockedReason(f"toolchain lock unavailable: {error}")
    try:
        store = toolchain.store_root(dict(os.environ), root)
    except toolchain.UnsafeStoreLocationError as error:
        return toolchain.BlockedReason(str(error))
    return resolve(
        repo_root=root, store=store, lock=lock, platform=toolchain.current_platform()
    )


class _ProvisionContext(Protocol):
    """Structural stand-in for `toolchain_provision.ProvisionContext` --
    duck-typed so this module never imports that one back (it already
    imports this one), which would be a cycle. `@property` (read-only), not
    plain attributes: `ProvisionContext` is a frozen dataclass, and a
    protocol with mutable attributes is a structurally NARROWER type a
    read-only frozen field cannot satisfy."""

    @property
    def store(self) -> Path: ...
    @property
    def lock(self) -> Mapping: ...
    @property
    def platform(self) -> str: ...
    @property
    def repo_root(self) -> Path: ...
    @property
    def env(self) -> Mapping[str, str]: ...


def _load_manifest(store: Path, lock: Mapping) -> dict:
    manifest = toolchain.load_store_manifest(store)
    digest = toolchain.lock_digest(lock)
    if manifest is None or manifest.get("lock_digest") != digest:
        return {"schema_version": 1, "lock_digest": digest, "tools": {}}
    return manifest


def _record(ctx: _ProvisionContext, bundle: str, digest: str) -> None:
    """Record `bundle`'s attested digest into the store manifest, under the
    same `fcntl.flock` serialization `toolchain_provision.py` uses for
    every other bundle -- so a `node-cache` write can never interleave with
    a concurrent `manifest provision` writing `manifest.json`."""
    ctx.store.mkdir(parents=True, exist_ok=True)
    lock_path = ctx.store / ".provision.lock"
    with os.fdopen(
        os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a"
    ) as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        manifest = _load_manifest(ctx.store, ctx.lock)
        platforms = manifest.setdefault("caches", {}).setdefault(bundle, {})
        platforms.setdefault("platforms", {})[ctx.platform] = {"digest": digest}
        (ctx.store / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))


def provision(ctx: _ProvisionContext, bundle: str) -> tuple[str, str, str | None]:
    """Materialize and record a cache, returning status, reason, and digest."""
    if bundle != CACHE_BUNDLE:
        return (
            "blocked",
            f"toolchain: {bundle} has no cache materializer",
            None,
        )
    try:
        _cache_dir, digest = materialize(
            repo_root=ctx.repo_root,
            store=ctx.store,
            lock=ctx.lock,
            platform=ctx.platform,
            env=ctx.env,
        )
    except NpmCacheError as error:
        return "blocked", str(error), None
    _record(ctx, bundle, digest)
    return "provisioned", "", digest
