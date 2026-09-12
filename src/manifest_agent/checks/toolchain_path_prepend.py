"""`path_prepend` resolution -- split out of `toolchain.py` for the Code
Constitution's 500-line ceiling (C7i, phase-3-5-decisions.md Correction 7
step 1).

A tool may declare `"path_prepend": ["store:<bundle>/bin", ...]`: each entry
names a bundle whose bin directory goes FIRST on the resolved child `PATH`,
ahead of the tool's own executable's bin dir and the OS baseline PATH
(Correction 17). This exists
for check bodies that shell out to a nested interpreter themselves (bats
scripts running `python3 -c '...'`) -- `toolchain.rewrite_argv` only ever
rewrites argv tokens the runner itself launches, never a token a nested
shell resolves on its own, so the only honest channel for that nested
resolution is the PATH the parent process hands it. Every entry is
hash-verified exactly like any other store reference: an unattested or
unprovisioned bundle BLOCKs the whole preflight, the same failure mode as
any other `store:` ref.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Context:
    """The caller's (`toolchain.py`) own functions/classes this module needs
    -- bundled as one object so no function here both avoids a circular
    import AND exceeds the Code Constitution's 5-parameter ceiling."""

    resolve_fn: Callable[..., Any]
    parse_fn: Callable[[str], tuple[str, str] | None]
    rewrite_argv_fn: Callable[..., Any]
    with_default_path_fn: Callable[..., Any]
    resolved_tool_cls: type
    blocked_reason_cls: type


def bundle_primary_relative(lock: Mapping, bundle: str, relative: str) -> str | None:
    """The specific executable a `path_prepend` bin-dir reference (e.g.
    `"store:project-env/bin"`) must fully resolve and hash-verify to prove
    the bundle itself is attested and provisioned -- `path_prepend` names a
    directory, but `resolve()` verifies one file, so this picks the one file
    every bundle kind always has: `bin/python` for a `python-env`, `bin/
    <bundle>` for a `binary`. `node-env` has no single canonical script, so
    a `path_prepend` entry for it must instead name a real console script
    directly (`relative != "bin"`)."""
    kind = ((lock.get("tools") or {}).get(bundle) or {}).get("kind")
    if kind == "python-env":
        return "bin/python"
    if kind == "node-env":
        return None if relative == "bin" else relative
    return f"bin/{bundle}"


def resolve_dirs(entries, ctx: Context, lock: Mapping, store: Path, platform: str):
    """Every `path_prepend` bundle, hash-verified via `ctx.resolve_fn` (the
    caller's `toolchain.resolve`), reduced to just its bin directory -- in
    declaration order, de-duplicated. A bundle that fails to resolve BLOCKs
    the whole preflight, same as any other store reference. Returns a tuple
    of dirs, or the `BlockedReason` from the first failure."""
    dirs: dict[Path, None] = {}
    for entry in entries:
        parsed = ctx.parse_fn(entry)
        if parsed is None:
            return _blocked(f"toolchain: invalid path_prepend entry {entry!r}")
        bundle, relative = parsed
        primary = bundle_primary_relative(lock, bundle, relative)
        if primary is None:
            return _blocked(
                f"toolchain: path_prepend for {bundle} needs a specific executable"
            )
        outcome = ctx.resolve_fn(
            f"store:{bundle}/{primary}", lock=lock, store=store, platform=platform
        )
        if isinstance(outcome, ctx.blocked_reason_cls):
            return outcome
        dirs.setdefault(outcome.executable.parent, None)
    return tuple(dirs)


def _blocked(reason: str):
    from .toolchain_env import BlockedReason

    return BlockedReason(reason)


def store_refs(tool: Mapping, parse_fn) -> tuple[str, ...]:
    """Every distinct literal `store:` token in a tool's `executable` or
    `version_argv`, in first-seen order.

    A tool whose body resolves a store engine itself (e.g. `hook.shfmt`
    running `store:shfmt/bin/shfmt` from inside `hooks.py`) names that same
    reference in `version_argv` (via `tool_versions.py --executable`) even
    though `executable` itself stays the repo-owned wrapper (`python3`) --
    that is how the preflight version probe is kept honest about which
    binary the check body will actually run. `parse_fn` is the caller's
    `toolchain.parse_store_executable`, injected to avoid a circular import.
    """
    seen: dict[str, None] = {}
    executable = tool.get("executable", "")
    if parse_fn(executable) is not None:
        seen[executable] = None
    for token in tool.get("version_argv", ()):
        if parse_fn(token) is not None:
            seen.setdefault(token, None)
    return tuple(seen)


def merged_resolution(primary_ref: str, resolved_by_ref, resolved_tool_cls):
    """One `ResolvedTool` standing in for every ref a preflight touched.

    Its `executable`/`interpreter`/`tool_sha256` describe `primary_ref`
    (the check's own `executable`, or the first ref when the check invokes
    its engine entirely from inside the body); `path_entries` is the union
    of every resolved ref's bin dirs, store entries first -- so a version
    probe for a *different* store ref (e.g. `store:uv/bin/uv`) still finds
    it on `PATH` without ever falling back to the ambient search.
    `resolved_tool_cls` is the caller's `toolchain.ResolvedTool`, injected to
    avoid a circular import.
    """
    primary = resolved_by_ref.get(primary_ref) or next(iter(resolved_by_ref.values()))
    if len(resolved_by_ref) == 1:
        return primary
    merged_entries: dict[Path, None] = {}
    for tool in resolved_by_ref.values():
        for entry in tool.path_entries:
            merged_entries.setdefault(entry, None)
    return resolved_tool_cls(
        primary.bundle,
        primary.executable,
        primary.interpreter,
        tuple(merged_entries),
        primary.tool_sha256,
    )


def with_prepend(merged, prepend_dirs: tuple[Path, ...], resolved_tool_cls):
    """`merged` with `prepend_dirs` spliced FIRST on `path_entries`, ahead of
    the executable's own bin dir and the OS baseline PATH (Correction 17) --
    de-duplicated."""
    if not prepend_dirs:
        return merged
    entries = tuple(dict.fromkeys((*prepend_dirs, *merged.path_entries)))
    return resolved_tool_cls(
        merged.bundle,
        merged.executable,
        merged.interpreter,
        entries,
        merged.tool_sha256,
    )


def resolve_engine_refs(tool: Mapping, lock: Mapping, store: Path, platform: str, ctx):
    """`tool["executable"]`/`version_argv`'s own store refs, merged into one
    `ResolvedTool` plus the rewritten `version_argv` -- or the tool's bare
    plain-name executable, unresolved, when it names no store ref at all (a
    `path_prepend`-only tool, e.g. a repo-relative script). `ctx` bundles the
    caller's `toolchain` functions/classes this needs (`resolve_fn`,
    `parse_fn`, `rewrite_argv_fn`, `with_default_path_fn`, `resolved_tool_cls`,
    `blocked_reason_cls`) -- injected as one object to avoid both a circular
    import and a too-many-parameters finding."""
    resolved_by_ref: dict[str, object] = {}
    for ref in store_refs(tool, ctx.parse_fn):
        outcome = ctx.resolve_fn(ref, lock=lock, store=store, platform=platform)
        if isinstance(outcome, ctx.blocked_reason_cls):
            return outcome
        resolved_by_ref[ref] = outcome
    if resolved_by_ref:
        merged = merged_resolution(
            tool["executable"], resolved_by_ref, ctx.resolved_tool_cls
        )
        version_argv = ctx.rewrite_argv_fn(tuple(tool["version_argv"]), resolved_by_ref)
        return merged, version_argv
    bare = ctx.resolved_tool_cls(
        "", Path(tool["executable"]), None, ctx.with_default_path_fn(()), ""
    )
    return bare, tuple(tool["version_argv"])
