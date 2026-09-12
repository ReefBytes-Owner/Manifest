"""Store/candidate integrity bookkeeping for a check's before/after
comparison. Split out of `toolchain.py` (C7i) to keep it under the Code
Constitution's 500-line ceiling -- purely mechanical, no new behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def fingerprint(
    store: Path, resolved: Mapping[str, object], sha256_file
) -> dict[str, str]:
    """A before/after comparable snapshot of everything a run actually
    resolved. `sha256_file` is the caller's `toolchain.sha256_file`,
    injected to avoid a circular import."""
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
    resolved, env: Mapping[str, str], store_root, sha256_file
) -> dict[str, str]:
    """`fingerprint()` for a single already-resolved tool, or `{}` if none
    was used. `store_root`/`sha256_file` are the caller's `toolchain`
    functions, injected to avoid a circular import."""
    if resolved is None:
        return {}
    return fingerprint(store_root(env), {resolved.bundle: resolved}, sha256_file)


def integrity_reason(
    store_before: Mapping[str, str],
    store_after: Mapping[str, str],
    changed: bool,
    identity_error: str,
) -> str:
    """Empty unless the store or the candidate changed during a check's run."""
    if store_before != store_after:
        return "toolchain: store changed during run"
    return "candidate identity changed" if changed else identity_error
