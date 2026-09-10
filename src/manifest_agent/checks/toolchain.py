"""Resolve `store:` tool references against a hash-verified toolchain store.

The store is the trust boundary the parent design (phase-3-5-decisions.md,
section 3a) introduces: a check preflight never trusts a binary found by name
on `PATH`. Instead `tools[NAME].executable` may be `"store:<tool>/<relative-
exe>"`, naming an entry in `config/toolchain.lock.json` and a bin path inside
`<store>/manifest.json` written by `manifest provision`. `resolve()` re-hashes
the executable (and, for `python-env`/`node-env`, the interpreter behind the
console script) against what `manifest provision` recorded, on every call --
so a swapped launcher with an unchanged version string is caught here, not by
the downstream version probe.

Network only happens in `manifest provision` (see `toolchain_provision.py`).
This module never downloads anything; it only reads local files.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

STORE_ENV_VAR = "MANIFEST_TOOLCHAIN_STORE"
XDG_CACHE_ENV_VAR = "XDG_CACHE_HOME"
DEFAULT_CACHE_RELATIVE = Path(".cache") / "manifest" / "toolchain"

ALWAYS_PRESENT_EXECUTABLES = frozenset({"python3", "bash"})

_STORE_EXECUTABLE = re.compile(
    r"^store:(?P<tool>[A-Za-z0-9][A-Za-z0-9._-]*)/(?P<relative>[^\0]+)$"
)


@dataclass(frozen=True)
class ResolvedTool:
    """A store-resolved, hash-verified executable ready to run."""

    bundle: str
    executable: Path
    interpreter: Path | None
    path_entries: tuple[Path, ...]
    tool_sha256: str


@dataclass(frozen=True)
class BlockedReason:
    """A distinct, human-readable reason a tool could not be resolved."""

    reason: str


def parse_store_executable(value: str) -> tuple[str, str] | None:
    """Split `"store:<bundle>/<relative>"` into `(bundle, relative)`, else None."""
    match = _STORE_EXECUTABLE.match(value)
    if match is None:
        return None
    return match.group("tool"), match.group("relative")


def is_legal_plain_executable(value: str) -> bool:
    """Whether a non-`store:` executable name is on the always-present allow-list.

    Only interpreters guaranteed present without provisioning (`python3`,
    `bash`) or a repository-relative script path may bypass the store; any
    other bare command name (resolved by searching `PATH`) is exactly the
    trust gap 3a closes and must migrate to a `store:` form instead.
    """
    if value in ALWAYS_PRESENT_EXECUTABLES:
        return True
    path = Path(value)
    return not path.is_absolute() and "/" in value and ".." not in path.parts


_MACHINE_ALIASES = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}


def current_platform() -> str:
    """The running `<os>-<arch>` triple, in the lock file's naming convention."""
    system = _platform.system().casefold()
    machine = _MACHINE_ALIASES.get(
        _platform.machine().casefold(), _platform.machine().casefold()
    )
    return f"{system}-{machine}"


def store_root(env: Mapping[str, str]) -> Path:
    """Resolve the toolchain store location, honoring the documented precedence."""
    override = env.get(STORE_ENV_VAR, "")
    if override:
        return Path(override)
    xdg_cache = env.get(XDG_CACHE_ENV_VAR, "")
    if xdg_cache:
        return Path(xdg_cache) / "manifest" / "toolchain"
    home = env.get("HOME", "") or os.path.expanduser("~")
    return Path(home) / DEFAULT_CACHE_RELATIVE


def sha256_file(path: Path) -> str:
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def lock_digest(lock: Mapping) -> str:
    """A stable content digest of an in-memory lock document."""
    serialized = json.dumps(
        lock, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def lock_digest_for_registry(document: Mapping, registry_path: Path) -> dict[str, str]:
    """Compute the (`toolchain_lock`, `toolchain_lock_digest`) registry fields.

    The digest is folded into `config_digest` for free: it becomes part of
    the normalized registry document that `runner._config_digest` hashes.
    """
    relative = document.get("toolchain_lock", "") or ""
    digest = ""
    if relative:
        candidate = registry_path.resolve().parent.parent / relative
        if candidate.is_file():
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return {"toolchain_lock": relative, "toolchain_lock_digest": digest}


def load_lock_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_store_manifest(store: Path) -> dict | None:
    manifest_path = store / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def resolve(
    store_reference: str, *, lock: Mapping, store: Path, platform: str
) -> ResolvedTool | BlockedReason:
    """Resolve a `store:<bundle>/<relative>` reference to a verified executable.

    Implements the failure-semantics table from phase-3-5-decisions.md 3a
    exactly: every BLOCKED path below returns one of its distinct reason
    strings, and nothing here can return PASS -- callers still run the
    existing version probe once resolution succeeds.
    """
    parsed = parse_store_executable(store_reference)
    if parsed is None:
        raise ValueError(f"not a store executable reference: {store_reference!r}")
    bundle, relative = parsed
    entry = (lock.get("tools") or {}).get(bundle)
    platform_entry = (entry or {}).get("platforms", {}).get(platform)
    if entry is None or platform_entry is None or platform_entry.get("sha256") is None:
        return BlockedReason(f"toolchain: {bundle} unattested for {platform}")

    manifest = load_store_manifest(store)
    if manifest is None:
        return BlockedReason(
            f"toolchain: {bundle} not provisioned (run manifest provision)"
        )
    if manifest.get("lock_digest") != lock_digest(lock):
        return BlockedReason("toolchain: store stale (lock changed)")

    bundle_manifest = (manifest.get("tools") or {}).get(bundle)
    if bundle_manifest is None:
        return BlockedReason(
            f"toolchain: {bundle} not provisioned (run manifest provision)"
        )
    if bundle_manifest.get("source_sha256") != platform_entry["sha256"]:
        return BlockedReason("toolchain: store stale (lock changed)")

    exe_info = (bundle_manifest.get("executables") or {}).get(relative)
    if exe_info is None:
        return BlockedReason(
            f"toolchain: {bundle} not provisioned (run manifest provision)"
        )
    return _verify_exe(bundle, relative, exe_info, store)


def _verify_exe(bundle: str, relative: str, exe_info: Mapping, store: Path):
    exe_path = store / exe_info.get("path", "")
    if not exe_info.get("path") or not exe_path.is_file():
        return BlockedReason(
            f"toolchain: {bundle} not provisioned (run manifest provision)"
        )
    actual = sha256_file(exe_path)
    if actual != exe_info.get("sha256"):
        return BlockedReason(f"toolchain: {bundle} digest mismatch")

    interpreter_path = None
    interpreter_relative = exe_info.get("interpreter")
    if interpreter_relative:
        interpreter_path = store / interpreter_relative
        if not interpreter_path.is_file():
            return BlockedReason(
                f"toolchain: {bundle} not provisioned (run manifest provision)"
            )
        if sha256_file(interpreter_path) != exe_info.get("interpreter_sha256"):
            return BlockedReason(f"toolchain: {bundle} digest mismatch")

    bin_dirs = (exe_path.parent,)
    if interpreter_path is not None:
        bin_dirs = (exe_path.parent, interpreter_path.parent)
    path_entries = tuple(dict.fromkeys(bin_dirs))
    path_entries += tuple(Path(part) for part in os.defpath.split(os.pathsep) if part)
    return ResolvedTool(bundle, exe_path, interpreter_path, path_entries, actual)


def rewrite_argv(
    argv: tuple[str, ...], resolved: ResolvedTool | None
) -> tuple[str, ...]:
    """Replace a `store:` argv[0] with its resolved absolute executable path."""
    if resolved is None or not argv or parse_store_executable(argv[0]) is None:
        return argv
    return (str(resolved.executable), *argv[1:])


def resolved_env(env: Mapping[str, str], resolved: ResolvedTool) -> dict[str, str]:
    """Build the child PATH from store bin dirs + `os.defpath` -- never the user PATH."""
    result = dict(env)
    result["PATH"] = os.pathsep.join(str(entry) for entry in resolved.path_entries)
    return result


def fingerprint(store: Path, resolved: Mapping[str, ResolvedTool]) -> dict[str, str]:
    """A before/after comparable snapshot of everything a run actually resolved."""
    manifest_path = store / "manifest.json"
    snapshot = {
        "__manifest__": sha256_file(manifest_path) if manifest_path.is_file() else ""
    }
    for name, tool in resolved.items():
        snapshot[name] = (
            sha256_file(tool.executable) if tool.executable.is_file() else ""
        )
    return snapshot


def fingerprint_for(
    resolved: ResolvedTool | None, env: Mapping[str, str]
) -> dict[str, str]:
    """`fingerprint()` for a single already-resolved tool, or `{}` if none was used."""
    if resolved is None:
        return {}
    return fingerprint(store_root(env), {resolved.bundle: resolved})


def resolve_for_preflight(
    tool: Mapping, env: Mapping[str, str], lock: Mapping
) -> tuple[ResolvedTool | None, tuple[str, ...], dict[str, str], str | None]:
    """Resolve a `store:` executable for the runner's preflight step.

    Returns `(resolved_tool, version_argv, env, blocked_reason)`; plain-name
    tools pass through unchanged (`resolved=None, blocked_reason=None`).
    """
    if parse_store_executable(tool["executable"]) is None:
        return None, tuple(tool["version_argv"]), env, None
    outcome = resolve(
        tool["executable"],
        lock=lock,
        store=store_root(env),
        platform=current_platform(),
    )
    if isinstance(outcome, BlockedReason):
        return None, (), env, outcome.reason
    return (
        outcome,
        rewrite_argv(tuple(tool["version_argv"]), outcome),
        resolved_env(env, outcome),
        None,
    )
