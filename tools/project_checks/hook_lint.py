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
import re
import subprocess
import sys
from pathlib import Path

try:
    from tools.project_checks import toolchain_resolve
except ModuleNotFoundError:  # direct script execution from this directory
    import toolchain_resolve

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
# check_id -> hash-verified toolchain store reference (phase-3-5-decisions.md
# "Corrections 2026-09-10" > "Correction 2"): the engine is resolved from the
# store, never from PATH -- there is no fallback.
_STORE_REFS = {
    "hook.shellcheck": "store:shellcheck/bin/shellcheck",
    "hook.yamllint": "store:python-env/bin/yamllint",
}
# Extension-only misses two real shapes: shell scripts without a .sh/.bash
# suffix (e.g. *.sh.tmpl -- still a #!/bin/bash script pre-commit's real
# shellcheck-py hook would lint) and yamllint's own no-extension config
# filenames. Checked only after the extension pattern misses, and only for
# real, non-symlink files (shebang reads touch the file; basenames do not).
_SHEBANG_SHELLS = frozenset({"bash", "sh", "dash", "ksh", "zsh", "ash"})
_YAML_BASENAMES = frozenset({".yamllint", ".yamllintrc"})


def _shebang_interpreter(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            first_line = stream.readline(256).decode("utf-8", errors="ignore")
    except OSError:
        return ""
    if not first_line.startswith("#!"):
        return ""
    words = first_line[2:].strip().split()
    if not words:
        return ""
    interpreter = Path(words[0]).name
    if interpreter != "env":
        return interpreter
    rest = words[1:]
    if rest[:1] == ["-S"]:
        rest = rest[1:]
    return Path(rest[0]).name if rest else ""


def _matches_shape_fallback(check_id: str, path: Path, normalized: str) -> bool:
    """Called only when the extension pattern already missed."""
    if check_id == "hook.shellcheck":
        return _shebang_interpreter(path) in _SHEBANG_SHELLS
    return Path(normalized).name in _YAML_BASENAMES


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


def _select(
    root: Path, check_id: str, arguments: list[str]
) -> tuple[list[Path], list[str]]:
    """Select this check's real, on-shape inputs from an explicit path list.

    Both registered checks are "changed" selection: the runner always
    forwards an explicit, already-changed-file-filtered path list (never an
    empty one -- zero changed inputs is NOT_APPLICABLE upstream, before this
    body ever runs). No `_git_paths(root)` repo-sweep fallback: an empty
    `arguments` here means zero inputs, not "list the whole repository".
    """
    shape, _, _ = _TYPE_FILTERS[check_id]
    selected: list[Path] = []
    blocked: list[str] = []
    for name in arguments:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            blocked.append(f"unsafe input path: {name!r}")
            continue
        normalized = relative.as_posix()
        if _GLOBAL_EXCLUDE.search(normalized):
            continue
        path = root / relative
        is_real = path.is_file() and not path.is_symlink()
        if shape.search(normalized):
            matched = True
        elif is_real:
            matched = _matches_shape_fallback(check_id, path, normalized)
        else:
            matched = False
        if not matched:
            continue
        if not is_real:
            blocked.append(f"input unavailable or unsafe: {name!r}")
            continue
        selected.append(path)
    return selected, blocked


def _run(check_id: str, root: Path, paths: list[Path]) -> int:
    if not paths:
        return PASS
    _, executable_name, extra_argv = _TYPE_FILTERS[check_id]
    try:
        executable, path_env = toolchain_resolve.resolve_tool(
            _STORE_REFS[check_id], root
        )
    except toolchain_resolve.ToolchainBlocked as error:
        raise BlockedError(str(error)) from error
    env = {"PATH": path_env, "LC_ALL": "C", "LANG": "C"}
    try:
        result = subprocess.run(
            (str(executable), *extra_argv, *(str(path) for path in paths)),
            cwd=root,
            env=env,
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
