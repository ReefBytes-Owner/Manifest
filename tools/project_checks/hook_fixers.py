#!/usr/bin/env python3
"""Pinned pre-commit-hooks v6.0.0 fixer execution.

Split out of `hooks.py` (C2c, phase-3-5-decisions.md "Corrections
2026-09-10" > "Correction 2 -- Reverse the C2 deferral") for two reasons:
`hooks.py` was already at the 500-line constitution ceiling, and this is a
distinct responsibility -- resolving `trailing-whitespace-fixer`,
`end-of-file-fixer` and `mixed-line-ending` -- that now shares the same
hash-verified toolchain store resolution as every other engine invocation
in this package, instead of trusting `shutil.which` (PATH). `hooks.py`'s
`_pinned_fixer` is a thin wrapper around `run()` below.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from tools.project_checks import toolchain_resolve
except ModuleNotFoundError:  # direct script execution from this directory
    import toolchain_resolve

PASS = 0
FAIL = 2
BLOCKED = 3

_FIXER_ROWS = (
    "hook.trailing-whitespace|trailing-whitespace-fixer|trailing_whitespace_fixer;"
    "hook.end-of-file-fixer|end-of-file-fixer|end_of_file_fixer;"
    "hook.mixed-line-ending|mixed-line-ending|mixed_line_ending|--fix=lf"
)
_FIXER_EXECUTABLES = {
    fields[0]: (fields[1], f"pre_commit_hooks.{fields[2]}:main", tuple(fields[3:]))
    for fields in (row.split("|") for row in _FIXER_ROWS.split(";"))
}
_PRE_COMMIT_HOOKS_VERSION = "6.0.0"
CHECK_IDS = tuple(_FIXER_EXECUTABLES)


class BlockedError(RuntimeError):
    """A required executable or candidate input is unavailable."""


def run(check_id: str, root: Path, paths: list[tuple[str, Path]]) -> int:
    if not paths:
        return PASS
    name, entry_point, options = _FIXER_EXECUTABLES[check_id]
    environment_python, origin = _resolve_fixer(root, name, entry_point)
    with tempfile.TemporaryDirectory(prefix="manifest-hook-check-") as temporary:
        copy_root = Path(temporary)
        copied, blocked = _copy_inputs(copy_root, paths)
        for diagnostic in blocked:
            print(f"BLOCKED: {diagnostic}", file=sys.stderr)
        if not copied:
            return BLOCKED
        before = _snapshot_tree(copy_root)
        result = _run_process(
            (
                str(environment_python),
                "-I",
                "-c",
                "import runpy,sys; p=sys.argv[1]; sys.argv=[p,*sys.argv[2:]];"
                "runpy.run_path(p,run_name='__main__')",
                str(origin),
                *options,
                "--",
                *(relative for relative, _ in copied),
            ),
            copy_root,
            300,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        after = _snapshot_tree(copy_root)
        changed = sorted(set(before) | set(after), key=os.fsencode)
        changed = [path for path in changed if before.get(path) != after.get(path)]
        if changed:
            for relative in changed:
                print(
                    f"FAIL: {relative!r} requires {check_id} formatting",
                    file=sys.stderr,
                )
            return FAIL
        if result.returncode:
            raise BlockedError(
                f"pinned fixer failed without a formatting result (exit {result.returncode})"
            )
    return BLOCKED if blocked else PASS


def _resolve_fixer(root: Path, name: str, entry_point: str) -> tuple[Path, Path]:
    """Hash-verified resolution of a pre-commit-hooks console script from
    `store:python-env/bin/<name>` -- never `shutil.which` (PATH). Once
    resolved, the same provenance checks as before (pinned distribution
    version, exact entry-point target, source file confined to the
    provisioned environment) still apply against the resolved executable."""
    try:
        executable, _ = toolchain_resolve.resolve_tool(
            f"store:python-env/bin/{name}", root
        )
    except toolchain_resolve.ToolchainBlocked as error:
        raise BlockedError(str(error)) from error
    environment_python = executable.parent / "python"
    if not environment_python.is_file():
        raise BlockedError("pre-commit-hooks environment interpreter unavailable")
    metadata = _run_process(
        (
            str(environment_python),
            "-I",
            "-c",
            "import importlib.metadata as m,sys; d=m.distribution('pre-commit-hooks');"
            "e=[x.value for x in d.entry_points if x.group=='console_scripts' "
            "and x.name==sys.argv[1]];"
            "p=d.locate_file(sys.argv[2].replace('.','/')+'.py');"
            "print(d.version);print(e[0] if len(e)==1 else '');print(p)",
            name,
            entry_point.partition(":")[0],
        ),
        executable.parent,
        30,
    )
    lines = metadata.stdout.splitlines()
    if (
        metadata.returncode
        or lines[:2] != [_PRE_COMMIT_HOOKS_VERSION, entry_point]
        or len(lines) != 3
    ):
        raise BlockedError(
            f"required pre-commit-hooks {_PRE_COMMIT_HOOKS_VERSION} entry point "
            f"{name}={entry_point} is not provisioned"
        )
    try:
        origin = Path(lines[2]).resolve(strict=True)
    except OSError as error:
        raise BlockedError(f"pinned fixer source unavailable: {error}") from error
    environment = executable.parent.parent.resolve(strict=True)
    if (
        not origin.is_relative_to(environment)
        or not origin.is_file()
        or origin.is_symlink()
    ):
        raise BlockedError("pinned fixer source escapes its provisioned environment")
    return environment_python, origin


def _copy_inputs(
    copy_root: Path, paths: list[tuple[str, Path]]
) -> tuple[list[tuple[str, Path]], list[str]]:
    copied = []
    blocked = []
    for relative, source in paths:
        destination = copy_root / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        except OSError as error:
            blocked.append(f"input copy unavailable: {relative!r}: {error}")
            continue
        copied.append((relative, source))
    return copied, blocked


def _snapshot_tree(root: Path) -> dict[str, tuple[str, bytes | str, int]]:
    snapshot: dict[str, tuple[str, bytes | str, int]] = {}
    try:
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            snapshot[relative] = _snapshot_entry(path)
    except OSError as error:
        raise BlockedError(f"disposable comparison unavailable: {error}") from error
    return snapshot


def _snapshot_entry(path: Path) -> tuple[str, bytes | str, int]:
    if path.is_symlink():
        return "symlink", os.readlink(path), path.lstat().st_mode
    if path.is_file():
        return "file", path.read_bytes(), path.stat().st_mode
    if path.is_dir():
        return "directory", b"", path.stat().st_mode
    return "other", b"", path.lstat().st_mode


def _run_process(
    command: tuple[str, ...], cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"command unavailable: {error}") from error
