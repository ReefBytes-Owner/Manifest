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


@dataclass(frozen=True)
class BinaryArtifacts:
    """The primary `bin/<bundle>` executable plus any `extra_executables`
    extracted alongside it (e.g. `node`'s `bin/npm`) -- each entry carries
    its OWN `sha256`; `toolchain.resolve()` looks up the one matching the
    relative path it was asked to verify, never the primary tool's."""

    source_sha256: str
    primary_relative: str
    primary_sha256: str
    extra: Mapping[str, tuple[str, str]] = field(default_factory=dict)


def _record_bundle(
    ctx: ProvisionContext, bundle: str, artifacts: BinaryArtifacts
) -> None:
    def body() -> dict:
        manifest = _load_manifest(ctx.store, ctx.lock)
        executables = {
            f"bin/{bundle}": {
                "path": artifacts.primary_relative,
                "sha256": artifacts.primary_sha256,
            }
        }
        for name, (extra_relative, extra_sha256) in artifacts.extra.items():
            # `npm-cli.js` (and any future extra executable) runs via a
            # `#!/usr/bin/env node`-style shebang -- it needs the primary
            # `bin/<bundle>` executable (node) on its resolved PATH, exactly
            # like a python-env console script needs its venv interpreter.
            executables[f"bin/{name}"] = {
                "path": extra_relative,
                "sha256": extra_sha256,
                "interpreter": artifacts.primary_relative,
                "interpreter_sha256": artifacts.primary_sha256,
            }
        manifest["tools"][bundle] = {
            "source_sha256": artifacts.source_sha256,
            "executables": executables,
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


def _materialize_env(
    ctx: ProvisionContext, bundle: str, entry: Mapping, env_root: Path, names: list[str]
) -> dict[str, str]:
    """Dispatch to the right materializer by bundle name/kind, and return
    its console scripts. `project-env`/`config-env` are the two fixed
    Correction 7 step 1 additions, each with its own real project dir;
    every other `python-env`/`node-env` bundle keeps the original
    kind-only dispatch."""
    ctx_m = materialize.MaterializeContext(
        ctx.lock, ctx.store, ctx.platform, ctx.repo_root, ctx.env
    )
    if bundle == "project-env":
        # The ROOT project's dependency set ONLY -- never installs
        # `manifest_agent` itself (Correction 7 step 1).
        materialize.materialize_project_env(ctx_m, env_root)
        return materialize.python_env_console_scripts(env_root, names)
    if bundle == "config-env":
        # `configs/claude`'s OWN project, installed for real so its
        # `[project.scripts] manifest` entry point exists at `bin/manifest`
        # -- unlike project-env, this env IS meant to carry an installed
        # project (Correction 7 step 1).
        materialize.materialize_python_env(
            ctx_m, env_root, project_relative="configs/claude"
        )
        return materialize.python_env_console_scripts(env_root, names)
    if entry["kind"] == "python-env":
        materialize.materialize_python_env(ctx_m, env_root)
        return materialize.python_env_console_scripts(env_root, names)
    materialize.materialize_node_env(ctx_m, env_root, ctx.fetcher)
    return materialize.node_env_console_scripts(env_root, names)


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
    try:
        scripts = _materialize_env(ctx, bundle, entry, env_root, names)
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


def _extract_extra_executables(
    ctx: ProvisionContext,
    bundle: str,
    entry: Mapping,
    platform_entry: Mapping,
    data: bytes,
) -> dict[str, tuple[str, str]] | ProvisionOutcome:
    """Extract each `extra_executables` entry from the SAME already-hash-
    verified archive bytes as the primary executable -- never a second
    download -- and re-hash the extracted file against its OWN `exe_sha256`
    (never the primary tool's). Returns `{name: (relative, exe_sha256)}`, or
    a `ProvisionOutcome("blocked", ...)` on a digest mismatch."""
    extra: dict[str, tuple[str, str]] = {}
    for name, spec in (platform_entry.get("extra_executables") or {}).items():
        subtree_root = ctx.store / f"tools/{bundle}/{entry['version']}/_{name}"
        target = subtree_root / spec["executable_relative"]
        if not target.is_file():
            materialize.extract_subtree(data, spec["path_in_archive"], subtree_root)
        if not target.is_file():
            return ProvisionOutcome(
                bundle,
                "blocked",
                f"toolchain: {bundle} extra executable {name!r} not found in archive",
            )
        actual = toolchain.sha256_file(target)
        if actual != spec.get("exe_sha256"):
            return ProvisionOutcome(
                bundle, "blocked", f"toolchain: {bundle} {name} digest mismatch"
            )
        extra[name] = (str(target.relative_to(ctx.store)), actual)
    return extra


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
    extra = _extract_extra_executables(ctx, bundle, entry, platform_entry, data)
    if isinstance(extra, ProvisionOutcome):
        return extra
    _record_bundle(ctx, bundle, BinaryArtifacts(actual_sha, relative, exe_sha, extra))
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
    _record_bundle(ctx, bundle, BinaryArtifacts(actual_sha, relative, actual_sha))
    return ProvisionOutcome(bundle, "provisioned")


def _relative_scripts(entry: Mapping, platform: str) -> list[str]:
    """Every console-script relative path a bundle's platform entry declares.

    `binary` kinds have exactly one implicit `bin/<bundle>`, plus one per
    declared `extra_executables` entry (e.g. `node`'s `bin/npm`); `python-env`
    / `node-env` kinds list every console script the registry actually uses
    under `console_scripts` (schema `toolchain.lock.schema.json`).
    """
    platform_entry = entry.get("platforms", {}).get(platform, {})
    scripts = list(platform_entry.get("console_scripts") or ["bin/{bundle}"])
    scripts.extend(
        f"bin/{name}" for name in (platform_entry.get("extra_executables") or {})
    )
    return scripts


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
