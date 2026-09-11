"""`path_prepend` resolution -- split out of `toolchain.py` for the Code
Constitution's 500-line ceiling (C7i, phase-3-5-decisions.md Correction 7
step 1).

A tool may declare `"path_prepend": ["store:<bundle>/bin", ...]`: each entry
names a bundle whose bin directory goes FIRST on the resolved child `PATH`,
ahead of the tool's own executable's bin dir and `os.defpath`. This exists
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

from collections.abc import Mapping
from pathlib import Path


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


def resolve_dirs(
    entries, resolve_fn, parse_fn, lock: Mapping, store: Path, platform: str
):
    """Every `path_prepend` bundle, hash-verified via `resolve_fn` (the
    caller's `toolchain.resolve`, injected to avoid a circular import),
    reduced to just its bin directory -- in declaration order, de-duplicated.
    A bundle that fails to resolve BLOCKs the whole preflight, same as any
    other store reference. Returns a tuple of dirs, or the `BlockedReason`
    (the caller's dataclass, duck-typed via `.reason`) from the first
    failure."""
    dirs: dict[Path, None] = {}
    for entry in entries:
        parsed = parse_fn(entry)
        if parsed is None:
            return _blocked(f"toolchain: invalid path_prepend entry {entry!r}")
        bundle, relative = parsed
        primary = bundle_primary_relative(lock, bundle, relative)
        if primary is None:
            return _blocked(
                f"toolchain: path_prepend for {bundle} needs a specific executable"
            )
        outcome = resolve_fn(
            f"store:{bundle}/{primary}", lock=lock, store=store, platform=platform
        )
        if not hasattr(outcome, "executable"):
            return outcome
        dirs.setdefault(outcome.executable.parent, None)
    return tuple(dirs)


def _blocked(reason: str):
    from .toolchain_env import BlockedReason

    return BlockedReason(reason)
