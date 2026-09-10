#!/usr/bin/env python3
"""Type-filtered direct-engine invocations for `hook.shellcheck` and
`hook.yamllint`.

Both hooks' registry `types_or` is frozen empty by
`config/check-preservation.json` (the historically observed
`.pre-commit-config.yaml` never overrode it, relying on shellcheck-py's and
yamllint's own wrapper-level implicit `types: [shell]` / `types: [yaml]`
default -- an implicit default this repo's offline preservation oracle
cannot see, since confirming it means reading the wrapper repos' own
`.pre-commit-hooks.yaml` over the network). Without an equivalent filter,
the shared check forwarded every "changed" path -- including non-shell,
non-YAML files such as `.gitleaks.toml` -- straight to the engine, which
then failed trying to parse them. This module supplies that filter as a
`_EXCLUDES`-style, check-scoped runtime rule (the same pattern
`tools/project_checks/hooks.py` already uses for other hooks), living in
its own module because `hooks.py` is already at the 500-line ceiling.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PASS = 0
FAIL = 2
BLOCKED = 3

_GLOBAL_EXCLUDE = re.compile(
    r"^(\.Jules/|\.git/|\.agent_outputs/|node_modules/"
    # Deliberately invalid equivalence-record fixtures (C2,
    # config/check-preservation.json "equivalence" list): their whole
    # purpose is failing the engine under direct invocation, not passing
    # the repo's own real changed-file sweep.
    r"|tests/fixtures/equivalence/)"
)
# check_id -> (path-shape filter, engine executable name, extra argv).
_TYPE_FILTERS = {
    "hook.shellcheck": (
        re.compile(r"\.(?:sh|bash)$"),
        "shellcheck",
        ("--severity=warning",),
    ),
    "hook.yamllint": (re.compile(r"\.ya?ml$"), "yamllint", ()),
}
CHECK_IDS = tuple(_TYPE_FILTERS)


class BlockedError(RuntimeError):
    """A required executable or candidate input is unavailable."""


def _context(arguments: argparse.Namespace) -> Path:
    try:
        root = arguments.root.expanduser().resolve(strict=True)
    except OSError as error:
        raise BlockedError(f"root unavailable: {error}") from error
    if not root.is_dir():
        raise BlockedError(f"root is not a directory: {root}")
    return root


def _git_paths(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            (
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ),
            check=False,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"candidate path inventory unavailable: {error}") from error
    if result.returncode:
        raise BlockedError("candidate path inventory unavailable")
    return [os.fsdecode(item) for item in result.stdout.split(b"\0") if item]


def _select(
    root: Path, check_id: str, arguments: list[str]
) -> tuple[list[Path], list[str]]:
    shape, _, _ = _TYPE_FILTERS[check_id]
    names = arguments or _git_paths(root)
    selected: list[Path] = []
    blocked: list[str] = []
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            blocked.append(f"unsafe input path: {name!r}")
            continue
        normalized = relative.as_posix()
        if _GLOBAL_EXCLUDE.search(normalized) or not shape.search(normalized):
            continue
        path = root / relative
        if not path.is_file() or path.is_symlink():
            blocked.append(f"input unavailable or unsafe: {name!r}")
            continue
        selected.append(path)
    return selected, blocked


def _run(check_id: str, root: Path, paths: list[Path]) -> int:
    if not paths:
        return PASS
    _, executable_name, extra_argv = _TYPE_FILTERS[check_id]
    executable = shutil.which(executable_name)
    if executable is None:
        raise BlockedError(f"{executable_name} is unavailable")
    try:
        result = subprocess.run(
            (executable, *extra_argv, *(str(path) for path in paths)),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"{executable_name} unavailable: {error}") from error
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode == 0:
        return PASS
    return FAIL if result.returncode == 1 else BLOCKED


def _body_argv(check_id: str) -> tuple[str, ...]:
    return ("python3", "tools/project_checks/hook_lint.py", check_id, "--root", ".")


TASK7_DISPOSITIONS = {
    check_id: (_body_argv(check_id), "changed") for check_id in CHECK_IDS
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_id", choices=CHECK_IDS)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("paths", nargs="*")
    arguments = parser.parse_intermixed_args(argv)
    try:
        root = _context(arguments)
        selected, blocked = _select(root, arguments.check_id, arguments.paths)
        status = _run(arguments.check_id, root, selected)
        for diagnostic in blocked:
            print(f"BLOCKED: {diagnostic}", file=sys.stderr)
        if status == FAIL:
            return FAIL
        return BLOCKED if blocked else status
    except BlockedError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
