"""Invocation-count proof that the shared check entry never re-enters pre-commit.

Task 9 forbids calling `pre-commit` from inside a hook that pre-commit itself
runs — recursion would either hang the hook or silently no-op depending on
pre-commit's own re-entrancy guard, either way defeating the gate. This test
proves the invariant statically (no subprocess is executed) by counting every
place a shared-check command could invoke a real executable and asserting
none of them is `pre-commit`:

1. Every `argv` in the registry (`config/project-checks.json`) — these are
   the exact command vectors `manifest check` executes.
2. Every hardcoded command table in the check-body modules
   (`tools/project_checks/*.py`) that construct an argv at runtime.

A count of zero is the recursion guarantee; a nonzero count means a future
edit introduced a call back into pre-commit and must fail this test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = ROOT / "config/project-checks.json"
PROJECT_CHECKS_DIR = ROOT / "tools/project_checks"
CHECKS_PACKAGE_DIR = ROOT / "src/manifest_agent/checks"

# Any of these forms would invoke the `pre-commit` executable itself.
_PRE_COMMIT_ARGV0 = re.compile(r"^\s*pre-commit\s*$")
_PRE_COMMIT_INVOCATION = re.compile(
    r"""
    (?: subprocess\.(?:run|Popen|call|check_call|check_output) \s* \( \s* \[ \s* ["']pre-commit["']
      | \[ \s* ["']pre-commit["'] \s* , .*?\]   # a literal argv list starting with pre-commit
      | shutil\.which\( \s* ["']pre-commit["']
    )
    """,
    re.VERBOSE | re.DOTALL,
)


def _registry_argvs() -> list[list[str]]:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return [check["argv"] for check in registry["checks"]]


def test_no_registered_check_invokes_pre_commit() -> None:
    offenders = [
        argv
        for argv in _registry_argvs()
        if argv and _PRE_COMMIT_ARGV0.match(str(argv[0]))
    ]
    assert offenders == [], (
        f"{len(offenders)} registered check(s) invoke `pre-commit` as their "
        f"command — this would recurse when the shared entry is called from "
        f"inside a pre-commit hook: {offenders}"
    )


def test_no_check_body_module_shells_out_to_pre_commit() -> None:
    sources = sorted(PROJECT_CHECKS_DIR.glob("*.py")) + sorted(
        CHECKS_PACKAGE_DIR.glob("*.py")
    )
    assert sources, "expected check-body source files to scan"
    offenders: dict[str, int] = {}
    for source in sources:
        text = source.read_text(encoding="utf-8")
        count = len(_PRE_COMMIT_INVOCATION.findall(text))
        if count:
            offenders[str(source.relative_to(ROOT))] = count
    assert offenders == {}, (
        f"check-body module(s) construct a `pre-commit` invocation, which "
        f"would recurse when called from inside a pre-commit hook: {offenders}"
    )


def test_recursion_invocation_count_is_zero() -> None:
    # Single combined count, matching the brief's "invocation-count" framing:
    # zero pre-commit invocations across the entire shared-check surface.
    registry_hits = sum(
        1
        for argv in _registry_argvs()
        if argv and _PRE_COMMIT_ARGV0.match(str(argv[0]))
    )
    sources = sorted(PROJECT_CHECKS_DIR.glob("*.py")) + sorted(
        CHECKS_PACKAGE_DIR.glob("*.py")
    )
    source_hits = sum(
        len(_PRE_COMMIT_INVOCATION.findall(source.read_text(encoding="utf-8")))
        for source in sources
    )
    assert registry_hits + source_hits == 0
