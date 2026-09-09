"""Invocation-count proof that the shared check entry never re-enters pre-commit.

Task 9 forbids calling `pre-commit` from inside a hook that pre-commit itself
runs — recursion would either hang the hook or silently no-op depending on
pre-commit's own re-entrancy guard, either way defeating the gate.

`TestDynamicRecursionInvocationCount` is the load-bearing proof: it runs the
REAL `manifest check` CLI against the REAL registry
(`config/project-checks.json`, `--group structure`, all-Python checks so no
external tool install is needed) with a PATH-shimmed `pre-commit` that
records every invocation to a file, then asserts the recorded invocation
count is exactly zero. A purely static/regex scan over source text (as the
rest of this module also does, for defense in depth) cannot see a
dynamically constructed argv such as `["python", "-m", "pre_commit"]` or
`["uv", "run", "pre-commit"]` — only actually exercising the real command
resolution proves those don't exist either.

The static tests below are the fast, no-execution complement: they count
every place a shared-check command COULD invoke a real executable and assert
none of them literally spells `pre-commit`:

1. Every `argv` in the registry (`config/project-checks.json`) — these are
   the exact command vectors `manifest check` executes.
2. Every hardcoded command table in the check-body modules
   (`tools/project_checks/*.py`) that construct an argv at runtime.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from manifest_agent.cli import cli

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


_SHIM_SCRIPT = """#!/bin/sh
echo "invoked: $*" >> "{recorder}"
exit 0
"""


def _install_pre_commit_shim(bin_dir: Path, recorder: Path) -> None:
    shim = bin_dir / "pre-commit"
    shim.write_text(_SHIM_SCRIPT.format(recorder=recorder), encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class TestDynamicRecursionInvocationCount:
    def test_manifest_check_never_shells_out_to_pre_commit_shim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bin_dir = tmp_path / "shim-bin"
        bin_dir.mkdir()
        recorder = tmp_path / "pre-commit-invocations.log"
        _install_pre_commit_shim(bin_dir, recorder)

        # Prepend the shim dir so any PATH-based resolution of `pre-commit`
        # hits the recorder first, ahead of a real pre-commit if one happens
        # to be installed. process.py forwards PATH straight through to
        # every check subprocess (see cli.py's ENVIRONMENT_KEYS), so this is
        # exactly the PATH each real check body actually resolves against.
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.chdir(ROOT)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "check",
                "full",
                "--project-config",
                str(REGISTRY_PATH),
                "--group",
                "structure",
                "--base",
                "HEAD",
                "--json",
            ],
        )

        # Sanity check this actually ran real check subprocesses (not a
        # vacuously-passing no-op): the report must carry >0 executed
        # results, whatever their PASS/FAIL/BLOCKED status.
        payload = json.loads(result.output)
        assert payload["results"], (
            f"expected the real `structure` group to execute checks; got no "
            f"results (report status {payload.get('status')!r}) -- a run "
            f"that never executes anything cannot prove non-recursion"
        )

        assert not recorder.exists(), (
            f"the shared check entry invoked `pre-commit` "
            f"{recorder.read_text(encoding='utf-8').count(chr(10))} time(s) "
            f"while running group 'structure' -- this would recurse when "
            f"called from inside a pre-commit hook"
        )
