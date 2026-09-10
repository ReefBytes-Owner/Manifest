"""Command-line entry point: check files against the constitution.

Exit contract (matches the repo's Python convention):
    0  no blocking finding
    1  at least one blocking finding
    2  usage error, or the registry could not be read

The registry failing to load is exit 2, not a silent pass: a gate that cannot
read its own rules and reports success is the false green this repo has a whole
skill about. The hook makes the opposite choice, on purpose.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
from pathlib import Path

from . import baseline as baseline_mod
from .anchor import anchor_for
from .findings import Finding, render_json, render_text
from .registry import Registry, RegistryError, load
from .source import SourceFile

PROG = "constitution_check.py"
USAGE = """\
constitution_check.py - check files against the Code Constitution

Usage: constitution_check.py [options] [FILE ...]

  --changed [REF]   check files changed against REF (default: HEAD)
  --only CHECK      run one check only (repeatable), e.g. --only C-DATA
  --format FORMAT   text (default) or json
  --strict          treat advisory findings as blocking too
  --no-baseline     report every violation, not only those above the ratchet
  --list            print the article and check registry, then exit

  --update-baseline is retired (C3 identity ratchet). Propose instead:
      manifest check debt.constitution --propose-baseline --output PATH

Exit: 0 clean, 1 blocking findings, 2 usage or registry error.
"""


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv if argv is not None else sys.argv[1:])
    if args.help:
        print(USAGE, end="")
        return 0

    try:
        registry = load()
    except RegistryError as err:
        print(f"{PROG}: {err}", file=sys.stderr)
        return 2

    if args.list:
        print(_render_registry(registry))
        return 0

    paths = _resolve_paths(args)
    if not paths:
        print(f"{PROG}: no files to check", file=sys.stderr)
        return 2

    findings = _collect(paths, registry, args.only)
    root = _repo_root(paths[0])

    reported, suppressed = _apply_baseline(findings, root, registry, args.no_baseline)
    if reported:
        output = (
            render_json(reported) if args.format == "json" else render_text(reported)
        )
        print(output, file=sys.stderr if args.format == "text" else sys.stdout)
    if suppressed and args.format == "text":
        print(
            f"{PROG}: {suppressed} pre-existing violation(s) held at the baseline "
            f"({baseline_mod.DEFAULT_PATH.name}); fixing one lowers it permanently.",
            file=sys.stderr,
        )

    return 1 if _blocking(reported, registry, args.strict) else 0


def _apply_baseline(
    findings, root, registry, disabled: bool
) -> tuple[list[Finding], int]:
    """Split findings into what the ratchet reports and what it already holds."""
    if disabled or not baseline_mod.DEFAULT_PATH.is_file():
        return findings, 0
    try:
        recorded = baseline_mod.Baseline.load(baseline_mod.DEFAULT_PATH, root)
    except (OSError, ValueError, json.JSONDecodeError) as err:
        # An unreadable baseline must not silently excuse every violation.
        print(f"{PROG}: ignoring unreadable baseline: {err}", file=sys.stderr)
        return findings, 0
    advisory = [f for f in findings if _is_advisory(f, registry)]
    gated = [f for f in findings if not _is_advisory(f, registry)]
    excess = baseline_mod.over_baseline(gated, recorded, registry)
    return advisory + excess, len(gated) - len(excess)


def _is_advisory(finding: Finding, registry: Registry) -> bool:
    check = registry.checks.get(finding.check)
    return check is None or check.advisory


def _repo_root(sample: Path) -> Path:
    for parent in sample.resolve().parents:
        if (parent / ".git").exists():
            return parent
    return Path.cwd()


def _collect(
    paths: list[Path], registry: Registry, only: list[str] | None
) -> list[Finding]:
    from .checks import run_checks  # imported here so --help never pays for it

    findings: list[Finding] = []
    for path in paths:
        try:
            src = SourceFile.load(path, registry)
        except OSError as err:
            print(f"{PROG}: cannot read {path}: {err}", file=sys.stderr)
            continue
        for finding in run_checks(src, registry, only=only):
            findings.append(
                dataclasses.replace(finding, anchor=anchor_for(src, finding.line))
            )
    return findings


def _blocking(findings: list[Finding], registry: Registry, strict: bool) -> bool:
    for finding in findings:
        check = registry.checks.get(finding.check)
        if check is None:
            continue
        if check.advisory and not strict:
            continue
        if finding.severity == "error" or (strict and finding.severity == "warn"):
            return True
    return False


# Code the repository did not write and cannot fix. Gating it would report
# thousands of violations nobody can act on, which is how a checker gets
# switched off. Matched as path components, not substrings.
VENDORED = {
    ".venv",
    "venv",
    "node_modules",
    "site-packages",
    ".git",
    "__pycache__",
    "vendor",
}


def _resolve_paths(args) -> list[Path]:
    paths = [Path(p) for p in args.files]
    if args.changed is not None:
        paths.extend(_changed_files(args.changed))
    seen, unique = set(), []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        if VENDORED.intersection(resolved.parts):
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _changed_files(ref: str) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", ref],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as err:
        print(
            f"{PROG}: cannot list files changed against {ref}: {err}", file=sys.stderr
        )
        return []
    return [Path(line) for line in result.stdout.split("\n") if line.strip()]


def _render_registry(registry: Registry) -> str:
    lines = [f"Code Constitution v{registry.version}", ""]
    for article in registry.articles:
        checks = f"  [{', '.join(article.checks)}]" if article.checks else ""
        lines.append(f"{article.id}  {article.title}{checks}")
    lines.append("")
    for check_id, check in registry.checks.items():
        posture = "advisory" if check.advisory else "blocking"
        lines.append(f"{check_id:<9} {check.article}  {posture:<8} {check.summary}")
    return "\n".join(lines)


def _parse(argv: list[str]):
    parser = argparse.ArgumentParser(prog=PROG, add_help=False)
    parser.add_argument("files", nargs="*")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--changed", nargs="?", const="HEAD", default=None)
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--no-baseline", action="store_true")
    parser.add_argument("--list", action="store_true")
    return parser.parse_args(argv)
