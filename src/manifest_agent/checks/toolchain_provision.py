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
from dataclasses import dataclass
from pathlib import Path

from . import toolchain

Fetcher = Callable[[str], bytes]

_IMPLEMENTED_KINDS = frozenset({"binary"})


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
) -> list[ProvisionOutcome]:
    """Provision every (or `only`-selected) lock tool for `platform`."""
    ctx = ProvisionContext(store, lock, platform, fetcher or default_fetcher)
    outcomes: list[ProvisionOutcome] = []
    for bundle, entry in (lock.get("tools") or {}).items():
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
        outcomes.append(_provision_binary_entry(ctx, bundle, entry))
    return outcomes
