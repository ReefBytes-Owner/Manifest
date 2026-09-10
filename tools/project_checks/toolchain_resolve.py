#!/usr/bin/env python3
"""Shared hash-verified `store:` tool resolution for project-check bodies.

Lifted from ``analysis_checks.resolve_scanner`` -- the first check body to
use this pattern -- so every body that used to trust an ambient ``PATH``
binary (shellcheck, yamllint, shfmt, bats, markdownlint-cli2, uv, node/npm)
resolves through the same hash-verified toolchain store instead. See
``phase-3-5-decisions.md`` "Corrections 2026-09-10" > "Correction 2 --
Reverse the C2 deferral: migrate PATH-resolved checks to the store now".

Every function here BLOCKs (raises `ToolchainBlocked`) rather than falling
back to `PATH`: there is no fallback path, by design. A `store:` reference
that cannot be verified -- because the lock has no attested hash yet, the
store has not been provisioned, or a digest mismatches -- must render as
BLOCKED, never as a PASS that verified nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The candidate this body runs against is a materialized COPY of the repo
# (candidate.py) that includes src/ -- inserting it here, before the import,
# is what makes `toolchain_resolve` importable under a bare system `python3`
# with no `manifest_agent` package installed. This used to work only by
# accident: a dev checkout's own `.venv/bin/python3` (with manifest_agent
# already installed) happened to resolve first on `PATH`. C7c's honest PATH
# (store bin dirs + os.defpath) exposed the hidden dependency by resolving
# `python3` to the bare system interpreter instead -- the trust anchor for
# WHICH engine runs stays the runner's preflight (toolchain.resolve()); this
# is body-side import convenience only, same pattern already used by
# analysis_checks.py/debt_checks.py/dependency_checks.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from manifest_agent.checks import toolchain  # noqa: E402

DEFAULT_LOCK_RELATIVE = Path("config") / "toolchain.lock.json"


class ToolchainBlocked(RuntimeError):
    """A `store:` tool reference could not be resolved and hash-verified."""


def resolve_tool(store_ref: str, root: Path) -> tuple[Path, str]:
    """Hash-verified resolution of a `store:<bundle>/<relative>` reference.

    Returns `(executable_path, path_env)`. Never touches the caller's own
    `PATH`: `path_env` is built exclusively from the resolved tool's own
    store bin directories plus `os.defpath` (`toolchain.ResolvedTool.
    path_entries`), so a subprocess run with it cannot find anything the
    lock did not attest to.
    """
    lock_path = root / DEFAULT_LOCK_RELATIVE
    try:
        lock = toolchain.load_lock_file(lock_path)
    except (OSError, ValueError) as error:
        raise ToolchainBlocked(f"toolchain lock unavailable: {error}") from error
    try:
        store = toolchain.store_root(dict(os.environ), root)
    except toolchain.UnsafeStoreLocationError as error:
        raise ToolchainBlocked(str(error)) from error
    outcome = toolchain.resolve(
        store_ref, lock=lock, store=store, platform=toolchain.current_platform()
    )
    if isinstance(outcome, toolchain.BlockedReason):
        raise ToolchainBlocked(outcome.reason)
    path_env = os.pathsep.join(str(entry) for entry in outcome.path_entries)
    return outcome.executable, path_env


def resolve_env(store_ref: str, root: Path, base_env: dict[str, str]) -> dict[str, str]:
    """`resolve_tool` plus a child environment: `base_env` with `PATH`
    replaced by the resolved tool's own store bin directories.

    Non-`PATH` entries of `base_env` are preserved (e.g. `HOME`, `XDG_*`
    variables a tool such as `uv` needs for its own cache) -- only the
    executable-search path is ever store-controlled.
    """
    executable, path_env = resolve_tool(store_ref, root)
    env = dict(base_env)
    env["PATH"] = path_env
    return str(executable), env


def run_via_store(
    store_ref: str,
    root: Path,
    argv_tail: tuple[str, ...],
    *,
    timeout: float,
    base_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Resolve `store_ref` and run it with `argv_tail` appended, `cwd=root`.

    Raises `ToolchainBlocked` (resolution) or lets `OSError`/
    `subprocess.TimeoutExpired` (execution) propagate -- callers already
    catch both around their other subprocess calls, so this adds no new
    exception shape for them to handle.
    """
    executable, env = resolve_env(
        store_ref, root, base_env or {"LC_ALL": "C", "LANG": "C"}
    )
    return subprocess.run(
        (executable, *argv_tail),
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
