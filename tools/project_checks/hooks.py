#!/usr/bin/env python3
"""Check-only replacements for mapped hooks that otherwise rewrite files."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from tools.project_checks import generated as generated_checks
    from tools.project_checks import hook_fixers, toolchain_resolve
except ModuleNotFoundError:  # direct script execution from this directory
    import generated as generated_checks
    import hook_fixers
    import toolchain_resolve

PASS = 0
FAIL = 2
BLOCKED = 3

_GLOBAL_EXCLUDE = re.compile(r"^(\.Jules/|\.git/|\.agent_outputs/|node_modules/)")
_EXCLUDES = {
    "hook.trailing-whitespace": re.compile(
        r"^(\.Jules/|docs/templates/|plugins/manifest-code-quality/skills/"
        r"smoke-manage/vendor/(LICENSE\.PyYAML|yaml/)|plugins/manifest-i-have-adhd/"
        r"(skills/|guidance/|devin/|LICENSE\.upstream))|\.patch$"
    ),
    "hook.end-of-file-fixer": re.compile(
        r"^(\.Jules/|docs/templates/|configs/cursor/rules/|plugins/"
        r"manifest-code-quality/skills/smoke-manage/vendor/(LICENSE\.PyYAML|yaml/)|"
        r"plugins/manifest-i-have-adhd/(skills/|guidance/|devin/|LICENSE\.upstream))"
    ),
    "hook.mixed-line-ending": re.compile(
        r"^(plugins/manifest-code-quality/skills/smoke-manage/vendor/"
        r"(LICENSE\.PyYAML|yaml/)|plugins/manifest-i-have-adhd/"
        r"(skills/|guidance/|devin/|LICENSE\.upstream))"
    ),
    "hook.shfmt": re.compile(r"(^\.Jules/|\.bats$)"),
    "hook.validate-yaml-configs": re.compile(r"$^"),
    "hook.check-stale-repo-paths": re.compile(
        r"^(\.pre-commit-config\.yaml|\.Jules/|configs/|tests/|\.claude/|specs/|"
        r"docs/superpowers/plans/|docs/superpowers/specs/|docs/templates/)"
    ),
}


class BlockedError(RuntimeError):
    """A required executable or candidate input is unavailable."""


def _context(arguments: argparse.Namespace) -> Path:
    try:
        root = arguments.root.expanduser().resolve(strict=True)
    except OSError as error:
        raise BlockedError(f"root unavailable: {error}") from error
    if not root.is_dir():
        raise BlockedError(f"root is not a directory: {root}")
    if arguments.output_dir is not None:
        output = arguments.output_dir.expanduser().resolve(strict=False)
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise BlockedError("output directory must be disjoint from root")
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


def _paths(
    root: Path, check_id: str, arguments: list[str]
) -> tuple[list[tuple[str, Path]], list[str]]:
    names = arguments or _git_paths(root)
    selected = []
    blocked = []
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            blocked.append(f"unsafe input path: {name!r}")
            continue
        normalized = relative.as_posix()
        if _GLOBAL_EXCLUDE.search(normalized) or _EXCLUDES[check_id].search(normalized):
            continue
        path = root / relative
        if not path.is_file() or path.is_symlink():
            blocked.append(f"input unavailable or unsafe: {name!r}")
            continue
        selected.append((normalized, path))
    return selected, blocked


def _pinned_fixer(root: Path, check_id: str, paths: list[tuple[str, Path]]) -> int:
    try:
        return hook_fixers.run(check_id, root, paths)
    except hook_fixers.BlockedError as error:
        raise BlockedError(str(error)) from error


_SHFMT_ERRORS = (OSError, subprocess.TimeoutExpired, toolchain_resolve.ToolchainBlocked)


def _shfmt(root: Path, paths: list[tuple[str, Path]]) -> int:
    argv_tail = (
        "-d",
        "-i",
        "4",
        "-ci",
        "-sr",
        "-ln",
        "bash",
        *(str(path) for _, path in paths),
    )
    try:
        result = toolchain_resolve.run_via_store(
            "store:shfmt/bin/shfmt", root, argv_tail, timeout=300
        )
    except _SHFMT_ERRORS as error:
        raise BlockedError(f"shfmt unavailable: {error}") from error
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return PASS if result.returncode == 0 else FAIL


def _credentials(root: Path) -> int:
    pattern = re.compile(rb"sk-[A-Za-z0-9]{32,}")
    failed = False
    blocked = False
    walk_errors: list[OSError] = []
    for folder, directories, files in os.walk(
        root, followlinks=False, onerror=walk_errors.append
    ):
        directories[:] = [name for name in directories if name != ".git"]
        for filename in files:
            path = Path(folder) / filename
            if path.suffix not in {".sh", ".md", ".yaml", ".json"}:
                continue
            if path.is_symlink():
                # Match recursive grep: encounter but do not dereference leaf links.
                continue
            try:
                found = pattern.search(path.read_bytes())
            except OSError as error:
                print(f"BLOCKED: credential input unreadable: {error}", file=sys.stderr)
                blocked = True
                continue
            if found:
                print(
                    f"FAIL: credential pattern in {path.relative_to(root)!s}",
                    file=sys.stderr,
                )
                failed = True
    for error in walk_errors:
        print(f"BLOCKED: credential input unavailable: {error}", file=sys.stderr)
        blocked = True
    return FAIL if failed else (BLOCKED if blocked else PASS)


def _yaml_configs(paths: list[tuple[str, Path]]) -> int:
    try:
        import yaml
    except ImportError as error:
        raise BlockedError("PyYAML is unavailable") from error
    failed = False
    blocked = False
    for name, path in paths:
        try:
            with path.open(encoding="utf-8") as stream:
                yaml.safe_load(stream)
        except OSError as error:
            print(f"BLOCKED: {name!r}: {error}", file=sys.stderr)
            blocked = True
        except (UnicodeError, yaml.YAMLError) as error:
            print(f"FAIL: {name!r}: {error}", file=sys.stderr)
            failed = True
    return FAIL if failed else (BLOCKED if blocked else PASS)


def _stale_paths(paths: list[tuple[str, Path]]) -> int:
    stale = re.compile(r"\.claude/(scripts|config|commands|prompts|skills|\.plans)/")
    allowed = (
        re.compile(r"~/\.claude/"),
        re.compile(r"configs/claude/"),
        re.compile(r"\$HOME/\.claude/"),
        re.compile(r"\$\{HOME\}/\.claude/"),
    )
    failed = False
    blocked = False
    for name, path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            print(f"BLOCKED: Markdown input unreadable: {error}", file=sys.stderr)
            blocked = True
            continue
        for number, line in enumerate(lines, 1):
            if stale.search(line) and not any(
                pattern.search(line) for pattern in allowed
            ):
                print(f"FAIL: stale repo path in {name!r}:{number}", file=sys.stderr)
                failed = True
    return FAIL if failed else (BLOCKED if blocked else PASS)


def _cursor(root: Path) -> int:
    try:
        return generated_checks._run(root, "generated.cursor")
    except generated_checks.BlockedError as error:
        raise BlockedError(str(error)) from error


CHECK_IDS = tuple(
    f"hook.{name}"
    for name in re.findall(
        r"\S+",
        "trailing-whitespace end-of-file-fixer mixed-line-ending shfmt check-credentials validate-yaml-configs check-cursor-rules-drift check-stale-repo-paths",
    )
)

_UNRESOLVED_HOOKS = {
    "hook.golangci-lint": "pinned golangci-lint hook v2.12.2 is not provisioned",
    "hook.terraform_fmt": "pinned pre-commit-terraform v1.108.0 hook is not provisioned",
    "hook.terraform_validate": "pinned pre-commit-terraform v1.108.0 hook is not provisioned",
    "hook.terraform_tflint": "pinned pre-commit-terraform v1.108.0 hook is not provisioned",
    "hook.terraform_trivy": "pinned pre-commit-terraform v1.108.0 hook is not provisioned",
}

_PY = "store:python-env/bin/"
_DIRECT_ROWS = (
    f"hook.check-yaml|{_PY}check-yaml|--unsafe;hook.check-json|{_PY}check-json;"
    f"hook.check-added-large-files|{_PY}check-added-large-files|--maxkb=500;"
    f"hook.check-case-conflict|{_PY}check-case-conflict;"
    f"hook.check-merge-conflict|{_PY}check-merge-conflict;"
    f"hook.check-executables-have-shebangs|{_PY}check-executables-have-shebangs;"
    f"hook.check-shebang-scripts-are-executable|{_PY}check-shebang-scripts-are-executable;"
    f"hook.detect-private-key|{_PY}detect-private-key;hook.check-ast|{_PY}check-ast;"
    f"hook.debug-statements|{_PY}debug-statement-hook;"
    "hook.markdownlint-cli2|store:node-env/bin/markdownlint-cli2|--config|"
    ".markdownlint.jsonc;"
    f"hook.ruff|{_PY}ruff|check;hook.ruff-format|{_PY}ruff|format|--check;"
    "hook.eslint|store:node-env/bin/eslint;"
    "hook.constitution-check|python3|configs/claude/scripts/constitution_check.py;"
    "hook.validate-bootstrap|bash|-n;hook.check-bats-assertions|tests/lint/check_bats_assertions.sh;"
    "hook.check-array-expansion|tests/lint/check_array_expansion.sh;"
    "hook.cargo-fmt-check|cargo|fmt|--all|--|--check;"
    "hook.cargo-clippy|cargo|clippy|--all-targets|--|-D|warnings;hook.pyright|pyright"
)
_DIRECT_HOOK_ARGV = {
    fields[0]: tuple(fields[1:])
    for fields in (row.split("|") for row in _DIRECT_ROWS.split(";"))
}


def _body_argv(check_id: str) -> tuple[str, ...]:
    return ("python3", "tools/project_checks/hooks.py", check_id, "--root", ".")


_PROJECT_HOOKS = re.compile(
    r"hook\.(?:check-credentials|check-cursor-rules-drift|cargo-fmt-check|"
    r"cargo-clippy|pyright)$"
)
TASK7_DISPOSITIONS = {
    check_id: (
        _body_argv(check_id),
        "project" if _PROJECT_HOOKS.fullmatch(check_id) else "changed",
    )
    for check_id in CHECK_IDS
}
TASK7_DISPOSITIONS.update(
    {
        check_id: (argv, "project" if _PROJECT_HOOKS.fullmatch(check_id) else "changed")
        for check_id, argv in _DIRECT_HOOK_ARGV.items()
    }
)
TASK7_DISPOSITIONS.update(
    {check_id: (_body_argv(check_id), "changed") for check_id in _UNRESOLVED_HOOKS}
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_id", choices=(*CHECK_IDS, *_UNRESOLVED_HOOKS))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("paths", nargs="*")
    arguments = parser.parse_intermixed_args(argv)
    try:
        root = _context(arguments)
        if arguments.check_id in _UNRESOLVED_HOOKS:
            raise BlockedError(_UNRESOLVED_HOOKS[arguments.check_id])
        if arguments.check_id == "hook.check-credentials":
            return _credentials(root)
        if arguments.check_id == "hook.check-cursor-rules-drift":
            return _cursor(root)
        paths, blocked = _paths(root, arguments.check_id, arguments.paths)
        if arguments.check_id == "hook.shfmt":
            status = _shfmt(root, paths)
        elif arguments.check_id == "hook.validate-yaml-configs":
            status = _yaml_configs(paths)
        elif arguments.check_id == "hook.check-stale-repo-paths":
            status = _stale_paths(paths)
        else:
            status = _pinned_fixer(root, arguments.check_id, paths)
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
