"""Non-mutating structural checks extracted from the observed CI bodies."""

from __future__ import annotations

import argparse
import os
import re
import shutil
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

# Hash-verified toolchain store references for the engines this module used
# to resolve from ambient PATH (phase-3-5-decisions.md "Corrections
# 2026-09-10" > "Correction 2"). No fallback: an unattested/unprovisioned
# entry BLOCKs, it never falls back to PATH.
_SHELLCHECK_REF = "store:shellcheck/bin/shellcheck"
_YAMLLINT_REF = "store:python-env/bin/yamllint"
_BATS_REF = "store:node-env/bin/bats"

SYMLINKS = {
    "configs/claude/skills": "../../.apm/skills",
    "configs/cursor/scripts": "../claude/scripts",
    "configs/cursor/config": "../claude/config",
    "configs/cursor/prompts": "../claude/prompts",
    "configs/cursor/.plans": "../claude/.plans",
    "configs/gemini/scripts": "../claude/scripts",
    "configs/gemini/config": "../claude/config",
    "configs/gemini/prompts": "../claude/prompts",
    "configs/gemini/.plans": "../claude/.plans",
    "configs/codex/AGENTS.md": "../../AGENTS.md",
    "configs/codex/scripts": "../claude/scripts",
    "configs/codex/config": "../claude/config",
    "configs/codex/prompts": "../claude/prompts",
    "configs/codex/.plans": "../claude/.plans",
    "configs/antigravity/config": "../claude/config",
    "configs/antigravity/skills": "../claude/skills",
    "configs/antigravity/.plans": "../claude/.plans",
}


class BlockedError(RuntimeError):
    """A required local prerequisite is unavailable."""


def _context(arguments: argparse.Namespace) -> tuple[Path, Path | None]:
    try:
        root = arguments.root.expanduser().resolve(strict=True)
    except OSError as error:
        raise BlockedError(f"root unavailable: {error}") from error
    if not root.is_dir():
        raise BlockedError(f"root is not a directory: {root}")
    if arguments.output_dir is None:
        return root, None
    output = arguments.output_dir.expanduser().resolve(strict=False)
    if output == root or output.is_relative_to(root) or root.is_relative_to(output):
        raise BlockedError("output directory must be disjoint from root")
    return root, output


def _symlinks(root: Path) -> int:
    failed = False
    for name, expected in SYMLINKS.items():
        path = root / name
        if not path.is_symlink():
            print(f"FAIL: {name!r} is not a symlink", file=sys.stderr)
            failed = True
            continue
        actual = os.readlink(path)
        if actual != expected:
            print(
                f"FAIL: {name!r} -> {actual!r} (expected {expected!r})",
                file=sys.stderr,
            )
            failed = True
    return FAIL if failed else PASS


def _yaml(root: Path) -> int:
    try:
        import yaml
    except ImportError as error:
        raise BlockedError("PyYAML is unavailable") from error
    files = sorted((root / "configs/claude/config").glob("*.yml"))
    if not files:
        raise BlockedError("no configs/claude/config/*.yml inputs")
    failed = False
    blocked = False
    for path in files:
        if path.is_symlink():
            print(f"BLOCKED: YAML input is symlinked: {path}", file=sys.stderr)
            blocked = True
            continue
        try:
            with path.open(encoding="utf-8") as stream:
                yaml.safe_load(stream)
        except OSError as error:
            print(f"BLOCKED: {path.relative_to(root)!s}: {error}", file=sys.stderr)
            blocked = True
        except (UnicodeError, yaml.YAMLError) as error:
            print(f"FAIL: {path.relative_to(root)!s}: {error}", file=sys.stderr)
            failed = True
    return FAIL if failed else (BLOCKED if blocked else PASS)


def _git_names(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            (
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(root),
                "ls-files",
                "-z",
            ),
            check=False,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"Git index unavailable: {error}") from error
    if result.returncode != 0:
        raise BlockedError("Git index unavailable")
    return [os.fsdecode(name) for name in result.stdout.split(b"\0") if name]


def _case_collision(root: Path) -> int:
    seen: dict[str, str] = {}
    collisions: list[tuple[str, str]] = []
    for name in _git_names(root):
        folded = name.casefold()
        if folded in seen and seen[folded] != name:
            collisions.append((seen[folded], name))
        else:
            seen[folded] = name
    for first, second in collisions:
        print(f"FAIL: case-colliding paths: {first!r}, {second!r}", file=sys.stderr)
    return FAIL if collisions else PASS


def _inventory(root: Path) -> int:
    policy = root / "configs/claude/config/skill_policies.yml"
    try:
        text = policy.read_text(encoding="utf-8")
    except OSError as error:
        raise BlockedError(f"skill policy unavailable: {error}") from error
    match = re.search(r"^expected_total:[ \t]*([0-9]+).*", text, re.MULTILINE)
    expected = int(match.group(1)) if match else None
    if expected is None:
        print("FAIL: skill_policies.yml declares no expected_total", file=sys.stderr)
        return FAIL
    skill_root = root / "configs/claude/skills"
    skills = []
    if skill_root.is_dir():
        for folder, _, names in os.walk(skill_root, followlinks=True):
            if "SKILL.md" in names:
                skills.append(Path(folder) / "SKILL.md")
    scripts = sorted((root / "configs/claude/scripts").rglob("*.sh"))
    failed = False
    if len(skills) != expected:
        print(
            f"FAIL: expected exactly {expected} skills, found {len(skills)}",
            file=sys.stderr,
        )
        failed = True
    if not scripts:
        print("FAIL: expected at least 1 shell script", file=sys.stderr)
        failed = True
    return FAIL if failed else PASS


def _shell_syntax(root: Path) -> int:
    bash = shutil.which("bash")
    if bash is None:
        raise BlockedError("bash is unavailable")
    script_paths = sorted((root / "configs/claude/scripts").glob("*.sh"))
    library_paths = sorted((root / "bootstrap/lib").glob("*.sh"))
    bootstrap = root / "bootstrap.sh"
    blocked = []
    if not script_paths:
        blocked.append("configs/claude/scripts/*.sh")
    if not library_paths:
        blocked.append("bootstrap/lib/*.sh")
    if not bootstrap.is_file():
        blocked.append("bootstrap.sh")
    paths = [*script_paths, bootstrap, *library_paths]
    failed = False
    for path in paths:
        if not path.is_file():
            if path in script_paths or path in library_paths:
                blocked.append(f"non-file shell glob match: {path.relative_to(root)!s}")
            continue
        if path.is_symlink():
            blocked.append(f"symlinked shell input: {path.relative_to(root)!s}")
            continue
        try:
            result = subprocess.run(
                (bash, "-n", str(path)), check=False, capture_output=True
            )
        except OSError as error:
            blocked.append(f"{path.relative_to(root)!s}: {error}")
            continue
        if result.returncode:
            failed = True
            print(
                f"FAIL: {path.relative_to(root)!s}: "
                f"{result.stderr.decode(errors='replace').strip()}",
                file=sys.stderr,
            )
    for name in blocked:
        print(f"BLOCKED: required shell input set unavailable: {name}", file=sys.stderr)
    if failed:
        return FAIL
    if blocked:
        return BLOCKED
    return FAIL if failed else PASS


def _skill_paths(root: Path) -> int:
    skill_root = root / ".apm/skills"
    if not skill_root.is_dir():
        raise BlockedError(".apm/skills is unavailable")
    failed = False
    blocked = False
    for path in skill_root.rglob("SKILL.md"):
        if path.is_symlink():
            print(f"BLOCKED: skill input is symlinked: {path}", file=sys.stderr)
            blocked = True
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            print(f"BLOCKED: skill input unreadable: {error}", file=sys.stderr)
            blocked = True
            continue
        for number, line in enumerate(lines, 1):
            if ".claude/skills/" in line:
                print(
                    f"FAIL: {path.relative_to(root)!s}:{number}: absolute skills path",
                    file=sys.stderr,
                )
                failed = True
    return FAIL if failed else (BLOCKED if blocked else PASS)


def _glob_inputs(root: Path, pattern: str) -> tuple[list[Path], list[str]]:
    paths = sorted(root.glob(pattern))
    blocked = []
    if not paths:
        blocked.append(f"required input set is empty: {pattern}")
    files = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            blocked.append(
                f"required input is not a regular file: {path.relative_to(root)}"
            )
        else:
            files.append(path)
    return files, blocked


def _lint_process(
    root: Path, command: tuple[str, ...], env: dict[str, str] | None = None
) -> int:
    try:
        result = subprocess.run(
            command,
            cwd=root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"BLOCKED: linter unavailable: {error}", file=sys.stderr)
        return BLOCKED
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode == 0:
        return PASS
    return FAIL if result.returncode == 1 else BLOCKED


def _resolved(store_ref: str, root: Path) -> tuple[str, dict[str, str]]:
    try:
        executable, path_env = toolchain_resolve.resolve_tool(store_ref, root)
    except toolchain_resolve.ToolchainBlocked as error:
        raise BlockedError(str(error)) from error
    return str(executable), {"PATH": path_env, "LC_ALL": "C", "LANG": "C"}


def _shellcheck_project(root: Path, bootstrap: bool) -> int:
    executable, env = _resolved(_SHELLCHECK_REF, root)
    patterns = (
        ("bootstrap/lib/*.sh",) if bootstrap else ("configs/claude/scripts/*.sh",)
    )
    groups = []
    blocked = []
    if bootstrap:
        script = root / "bootstrap.sh"
        if script.is_symlink() or not script.is_file():
            blocked.append("required input is unavailable: bootstrap.sh")
        else:
            groups.append([script])
    for pattern in patterns:
        files, unavailable = _glob_inputs(root, pattern)
        blocked.extend(unavailable)
        if files:
            groups.append(files)
    failed = False
    for files in groups:
        status = _lint_process(
            root,
            (executable, "-S", "warning", *(str(path) for path in files)),
            env,
        )
        failed = failed or status == FAIL
        if status == BLOCKED:
            blocked.append("shellcheck could not execute")
    for diagnostic in blocked:
        print(f"BLOCKED: {diagnostic}", file=sys.stderr)
    return FAIL if failed else (BLOCKED if blocked else PASS)


def _yamllint_project(root: Path) -> int:
    executable, env = _resolved(_YAMLLINT_REF, root)
    files, blocked = _glob_inputs(root, "configs/claude/config/*.yml")
    status = (
        PASS
        if not files
        else _lint_process(root, (executable, *(str(path) for path in files)), env)
    )
    for diagnostic in blocked:
        print(f"BLOCKED: {diagnostic}", file=sys.stderr)
    return (
        FAIL if status == FAIL else (BLOCKED if blocked or status == BLOCKED else PASS)
    )


def _bundle_partition(root: Path) -> int:
    executable, env = _resolved(_BATS_REF, root)
    target = root / "tests/bats/bundle_partition.bats"
    if target.is_symlink() or not target.is_file():
        raise BlockedError(
            "required input is unavailable: tests/bats/bundle_partition.bats"
        )
    return _lint_process(root, (executable, str(target)), env)


CHECKS = {
    "structure.symlinks": _symlinks,
    "syntax.yaml.config": _yaml,
    "structure.case-collision": _case_collision,
    "structure.inventory": _inventory,
    "syntax.shell.project": _shell_syntax,
    "structure.skill-paths": _skill_paths,
    "lint.shell.scripts": lambda root: _shellcheck_project(root, False),
    "lint.shell.bootstrap": lambda root: _shellcheck_project(root, True),
    "lint.yaml.config": _yamllint_project,
    "test.bundle-partition": _bundle_partition,
}

_BLOCKED_CHECKS = {
    "lint.markdown.keydocs": (
        "markdownlint-cli2 executable matching action pin "
        "21c1be1b93ad9ed58fa840aacc3f279cde2a72ff is not provisioned"
    ),
}

_PROJECT_ARGV = {
    "lint.shell.arrays": ("tests/lint/check_array_expansion.sh",),
    "lint.bats.assertions": ("tests/lint/check_bats_assertions.sh",),
    "package.self-contained": ("tests/lint/check_fresh_checkout.sh",),
    "structure.bundle-references": ("python3", "tools/check_bundle_link_references.py"),
    "structure.runtime-paths": (
        "python3",
        "tools/check_plugin_runtime_paths.py",
        "--json",
    ),
    "structure.agent-frontmatter": ("python3", "tools/check_agent_frontmatter.py"),
    "structure.skill-references": (
        "python3",
        "configs/claude/scripts/skill_reference_check.py",
        "--registry",
        "configs/claude/config/skill_policies.yml",
        "--baseline",
        "configs/claude/config/skill_reference_baseline.json",
    ),
    "test.bats": ("store:node-env/bin/bats", "tests/bats/"),
    "test.python": (
        "store:python-env/bin/pytest",
        "tests/python/",
        "-v",
        "-m",
        "not native",
    ),
    "test.hooks": (
        "store:python-env/bin/pytest",
        ".apm/skills/ai-hooks-integration/tests/",
        "-v",
    ),
    "test.smoke.lite": (
        "configs/claude/.venv/bin/manifest",
        "smoke",
        "run",
        "--app",
        "manifest",
        "--tier",
        "Lite",
        "--catalog-dir",
        "smoke-catalog",
    ),
}


def _body_argv(check_id: str) -> tuple[str, ...]:
    return (
        "python3",
        "tools/project_checks/structure.py",
        check_id,
        "--root",
        ".",
    )


TASK7_DISPOSITIONS = {
    check_id: (_body_argv(check_id), "project") for check_id in CHECKS
}
TASK7_DISPOSITIONS.update(
    {check_id: (argv, "project") for check_id, argv in _PROJECT_ARGV.items()}
)
TASK7_DISPOSITIONS.update(
    {check_id: (_body_argv(check_id), "project") for check_id in _BLOCKED_CHECKS}
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_id", choices=(*CHECKS, *_BLOCKED_CHECKS))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    arguments = parser.parse_args(argv)
    try:
        root, _ = _context(arguments)
        if arguments.check_id in _BLOCKED_CHECKS:
            raise BlockedError(_BLOCKED_CHECKS[arguments.check_id])
        return CHECKS[arguments.check_id](root)
    except BlockedError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
