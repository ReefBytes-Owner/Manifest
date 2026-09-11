"""`manifest provision`: populate the content-addressed toolchain store.

Network is permitted here, and only here -- `manifest check` never calls into
this module. Every download goes through an injectable `Fetcher` callable so
tests drive real code paths with local fixture bytes and never touch the
network (mirrors the seam `tools/project_checks/ci_context.py` uses for
`fetch_jobs`).

Store writes are serialized with the same `fcntl.flock` primitive
`preparation.py` already uses, so two concurrent `manifest provision`
invocations cannot interleave a torn `manifest.json`.
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import tarfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import toolchain
from . import toolchain_materialize as materialize

Fetcher = Callable[[str], bytes]

_IMPLEMENTED_KINDS = frozenset({"binary", "python-env", "node-env"})
_ENV_KINDS = frozenset({"python-env", "node-env"})


@dataclass(frozen=True)
class ProvisionOutcome:
    bundle: str
    status: str  # "provisioned" | "skipped" | "blocked"
    reason: str = ""


@dataclass(frozen=True)
class ProvisionContext:
    """The arguments every provisioning path needs together."""

    store: Path
    lock: Mapping
    platform: str
    fetcher: Fetcher = None  # type: ignore[assignment]
    repo_root: Path = field(default_factory=Path.cwd)
    env: Mapping[str, str] = field(default_factory=dict)


def default_fetcher(url: str) -> bytes:  # pragma: no cover - exercised only live
    """The real network fetcher; never invoked by tests or by `manifest check`."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def _extract_one(data: bytes, path_in_archive: str, destination: Path) -> None:
    """Write `path_in_archive` from `data` to `destination`, archive or raw."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            member = archive.extractfile(path_in_archive)
            if member is None:
                raise ValueError(f"{path_in_archive!r} not found in archive")
            destination.write_bytes(member.read())
    except tarfile.ReadError:
        destination.write_bytes(data)
    destination.chmod(0o755)


def _with_store_lock(store: Path, body: Callable[[], dict]) -> dict:
    store.mkdir(parents=True, exist_ok=True)
    lock_path = store / ".provision.lock"
    with os.fdopen(
        os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a"
    ) as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        return body()


def _load_manifest(store: Path, lock: Mapping) -> dict:
    manifest = toolchain.load_store_manifest(store)
    if manifest is None or manifest.get("lock_digest") != toolchain.lock_digest(lock):
        return {
            "schema_version": 1,
            "lock_digest": toolchain.lock_digest(lock),
            "tools": {},
        }
    return manifest


def _record_bundle(
    ctx: ProvisionContext,
    bundle: str,
    relative: str,
    source_sha256: str,
    exe_sha256: str,
) -> None:
    def body() -> dict:
        manifest = _load_manifest(ctx.store, ctx.lock)
        manifest["tools"][bundle] = {
            "source_sha256": source_sha256,
            "executables": {f"bin/{bundle}": {"path": relative, "sha256": exe_sha256}},
        }
        (ctx.store / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
        return manifest

    _with_store_lock(ctx.store, body)


def _record_env_bundle(
    ctx: ProvisionContext, bundle: str, source_sha256: str, scripts: Mapping[str, str]
) -> None:
    def body() -> dict:
        manifest = _load_manifest(ctx.store, ctx.lock)
        manifest["tools"][bundle] = {
            "source_sha256": source_sha256,
            "executables": {
                f"bin/{name}": {"path": relative} for name, relative in scripts.items()
            },
        }
        (ctx.store / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
        return manifest

    _with_store_lock(ctx.store, body)


class _SourceUnavailable(RuntimeError):
    """A `file://` lock source could not be read; carries the reason text."""


def _read_source_bytes(ctx: ProvisionContext, url: str) -> bytes:
    """`file://` lockfile URLs are read relative to `ctx.repo_root` -- never
    fetched over the network; only the archive/binary `Fetcher` seam does
    that. A non-`file://` URL is a lock authoring error, not a runtime one.

    Raises `_SourceUnavailable` (never a raw `OSError`) so a missing or
    unreadable source -- e.g. a `file://` lock source that exists on the
    author's machine but was never committed -- BLOCKs `manifest provision`
    instead of crashing it with a traceback."""
    relative = url.removeprefix("file://")
    path = ctx.repo_root / relative
    try:
        return path.read_bytes()
    except OSError as error:
        raise _SourceUnavailable(f"source unavailable: {relative}: {error}") from error


def _provision_env_entry(
    ctx: ProvisionContext, bundle: str, entry: Mapping
) -> ProvisionOutcome:
    """Materialize a python-env/node-env bundle and record whatever the
    materialization actually produced. This never gates on the lock's
    `exe_sha256` matching -- exactly like `_provision_binary_entry`, the
    check is `toolchain.resolve()`'s job on every later preflight, not
    provisioning time; the store's own manifest is never the trust anchor."""
    platform_entry = _platform_entry(entry, bundle, ctx.platform)
    if isinstance(platform_entry, ProvisionOutcome):
        return platform_entry
    try:
        source_bytes = _read_source_bytes(ctx, platform_entry["url"])
    except _SourceUnavailable as error:
        return ProvisionOutcome(bundle, "blocked", f"toolchain: {bundle} {error}")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if source_sha256 != platform_entry["sha256"]:
        return ProvisionOutcome(
            bundle, "blocked", f"toolchain: {bundle} digest mismatch"
        )
    env_root = ctx.store / f"tools/{bundle}/{source_sha256[:16]}"
    names = [Path(script).name for script in platform_entry.get("console_scripts", ())]
    ctx_m = materialize.MaterializeContext(
        ctx.lock, ctx.store, ctx.platform, ctx.repo_root, ctx.env
    )
    try:
        if entry["kind"] == "python-env":
            materialize.materialize_python_env(ctx_m, env_root)
            scripts = materialize.python_env_console_scripts(env_root, names)
        else:
            materialize.materialize_node_env(ctx_m, env_root, ctx.fetcher)
            scripts = materialize.node_env_console_scripts(env_root, names)
    except materialize.MaterializationError as error:
        return ProvisionOutcome(bundle, "blocked", str(error))
    missing = sorted(set(names) - set(scripts))
    if missing:
        return ProvisionOutcome(
            bundle,
            "blocked",
            f"toolchain: {bundle} missing console script(s) {missing}",
        )
    env_relative = env_root.relative_to(ctx.store)
    store_relative_scripts = {
        name: str(env_relative / relative) for name, relative in scripts.items()
    }
    _record_env_bundle(ctx, bundle, source_sha256, store_relative_scripts)
    return ProvisionOutcome(bundle, "provisioned")


def _platform_entry(
    entry: Mapping, bundle: str, platform: str
) -> dict | ProvisionOutcome:
    platform_entry = entry.get("platforms", {}).get(platform)
    if (
        platform_entry is None
        or platform_entry.get("sha256") is None
        or platform_entry.get("exe_sha256") is None
    ):
        return ProvisionOutcome(
            bundle, "blocked", f"toolchain: {bundle} unattested for {platform}"
        )
    return platform_entry


def _provision_binary_entry(
    ctx: ProvisionContext, bundle: str, entry: Mapping
) -> ProvisionOutcome:
    platform_entry = _platform_entry(entry, bundle, ctx.platform)
    if isinstance(platform_entry, ProvisionOutcome):
        return platform_entry
    data = ctx.fetcher(platform_entry["url"])
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != platform_entry["sha256"]:
        return ProvisionOutcome(
            bundle, "blocked", f"toolchain: {bundle} digest mismatch"
        )
    relative = f"tools/{bundle}/{entry['version']}/bin/{bundle}"
    exe_path = ctx.store / relative
    _extract_one(data, platform_entry["path_in_archive"], exe_path)
    exe_sha = toolchain.sha256_file(exe_path)
    _record_bundle(ctx, bundle, relative, actual_sha, exe_sha)
    return ProvisionOutcome(bundle, "provisioned")


def import_binary(
    ctx: ProvisionContext, bundle: str, entry: Mapping, source: Path
) -> ProvisionOutcome:
    """Adopt an existing on-disk binary ONLY if its sha256 matches the lock.

    Only `binary`-kind bundles can be adopted this way: a `python-env`/
    `node-env` bundle has several console scripts, not one file to import.
    """
    if entry.get("kind") != "binary":
        return ProvisionOutcome(
            bundle,
            "blocked",
            f"toolchain: {bundle} is kind {entry.get('kind')!r}, "
            "--import only adopts binary-kind tools",
        )
    platform_entry = _platform_entry(entry, bundle, ctx.platform)
    if isinstance(platform_entry, ProvisionOutcome):
        return platform_entry
    if not source.is_file():
        return ProvisionOutcome(
            bundle, "blocked", f"toolchain: {bundle} import source missing"
        )
    actual_sha = toolchain.sha256_file(source)
    if actual_sha != platform_entry["exe_sha256"]:
        return ProvisionOutcome(
            bundle, "blocked", f"toolchain: {bundle} digest mismatch"
        )
    relative = f"tools/{bundle}/{entry['version']}/bin/{bundle}"
    exe_path = ctx.store / relative
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    exe_path.write_bytes(source.read_bytes())
    exe_path.chmod(0o755)
    _record_bundle(ctx, bundle, relative, actual_sha, actual_sha)
    return ProvisionOutcome(bundle, "provisioned")


def _relative_scripts(entry: Mapping, platform: str) -> list[str]:
    """Every console-script relative path a bundle's platform entry declares.

    `binary` kinds have exactly one, implicit at `bin/<bundle>`; `python-env`
    / `node-env` kinds list every console script the registry actually uses
    under `console_scripts` (schema `toolchain.lock.schema.json`).
    """
    platform_entry = entry.get("platforms", {}).get(platform, {})
    return list(platform_entry.get("console_scripts") or ["bin/{bundle}"])


def validate_offline(
    lock: Mapping, store: Path, platform: str
) -> tuple[bool, list[str]]:
    """Check the store against the lock without downloading anything.

    Returns `(complete, problems)`; `complete` is False if any attested tool
    for `platform` is missing, mismatched, or the store is stale.
    """
    problems: list[str] = []
    for bundle, entry in (lock.get("tools") or {}).items():
        platform_entry = entry.get("platforms", {}).get(platform, {})
        if (
            platform_entry.get("sha256") is None
            or platform_entry.get("exe_sha256") is None
        ):
            continue  # unattested is not this store's fault; not "incomplete"
        for relative in _relative_scripts(entry, platform):
            relative = relative.format(bundle=bundle)
            outcome = toolchain.resolve(
                f"store:{bundle}/{relative}", lock=lock, store=store, platform=platform
            )
            if isinstance(outcome, toolchain.BlockedReason):
                problems.append(outcome.reason)
    return not problems, problems


def provision(
    lock: Mapping,
    store: Path,
    *,
    platform: str,
    only: frozenset[str] | None = None,
    fetcher: Fetcher | None = None,
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> list[ProvisionOutcome]:
    """Provision every (or `only`-selected) lock tool for `platform`.

    Binary bundles provision before python-env/node-env ones regardless of
    the lock's key order: `uv` and `node` must already be in the store
    before a `python-env`/`node-env` materialization can resolve them.
    """
    ctx = ProvisionContext(
        store,
        lock,
        platform,
        fetcher or default_fetcher,
        repo_root or Path.cwd(),
        env or {},
    )
    items = sorted(
        (lock.get("tools") or {}).items(),
        key=lambda item: item[1].get("kind") in _ENV_KINDS,
    )
    outcomes: list[ProvisionOutcome] = []
    for bundle, entry in items:
        if only is not None and bundle not in only:
            continue
        if entry.get("kind") not in _IMPLEMENTED_KINDS:
            outcomes.append(
                ProvisionOutcome(
                    bundle,
                    "blocked",
                    f"toolchain: {bundle} kind {entry.get('kind')!r} not yet provisionable",
                )
            )
            continue
        if entry.get("kind") in _ENV_KINDS:
            outcomes.append(_provision_env_entry(ctx, bundle, entry))
        else:
            outcomes.append(_provision_binary_entry(ctx, bundle, entry))
    return outcomes
