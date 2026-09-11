"""C7k step 3 (phase-3-5-decisions.md Correction 9 rule 3): no test may
materialize its own tree.

Root cause this closes: a test whose candidate root is derived from
`REPO_ROOT` / `Path(__file__)` (this running checkout) rather than a
disposable `tmp_path` fixture repo writes into -- or reads a subprocess
`cwd`/`--project` against -- the tree currently under test whenever that
same test file is executed as `test.python` INSIDE a `manifest check`
candidate (a materialized copy of this repository). The candidate's
identity digest then changes mid-run and the runner reports "candidate
identity changed" (measured: `test_toolchain_c7i_functional.py`'s
`_fresh_store` and `test_check_runner_preparation_groups.py`'s
`TestRealSkillMirrorPreparation` both did this before C7k step 3).

This guard scans every test file under `tests/python/` for calls to
`materialize_candidate(...)` and fails if the FIRST positional argument's
source text is running-tree-derived.

What it accepts (deliberately a simple, documented syntactic scan, not a
full data-flow analysis -- phase-3-5-decisions.md's own instruction for
this guard):

- A bare local name (`source`, `root`, `fixture_root`, `candidate_root`,
  ...) or a starred fixture tuple (`*source`) -- these are assumed to be
  `tmp_path`-derived, which is the established pattern every real caller
  in this tree already follows (see `test_check_candidate.py`'s `source`
  fixture and its `materialize` helper).
- Any expression at all, EXCEPT one whose unparsed source text contains
  one of the banned running-tree markers below.

What it rejects: the first argument's unparsed source text contains
`REPO_ROOT`, `__file__`, or `Path.cwd()` -- the three shapes this
repository's tests have used to name the currently-executing checkout.
A test that legitimately needs the real repository's file CONTENT (e.g.
copying a real script's text into a fixture) must read that content into
a variable first and pass the tmp_path-derived destination to
`materialize_candidate`, never the running-tree path itself -- exactly
the pattern `test_toolchain_c7i_functional.py`'s `_provisioning_source_root`
and `test_check_runner_preparation_groups.py`'s `_skill_mirror_fixture_repo`
now follow.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]

_BANNED_MARKERS = ("REPO_ROOT", "__file__", "Path.cwd()")


def _materialize_candidate_first_arg_calls(tree: ast.AST) -> list[ast.expr]:
    """Every `materialize_candidate(...)` call's first positional argument
    expression node, found anywhere in `tree` -- bare-name call
    (`materialize_candidate(...)`) or attribute call
    (`module.materialize_candidate(...)`), matched by the called name's
    final component so an import alias still resolves."""
    first_args: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else (func.attr if isinstance(func, ast.Attribute) else None)
        )
        if name != "materialize_candidate" or not node.args:
            continue
        first_args.append(node.args[0])
    return first_args


def _violation(source_file: Path, arg: ast.expr) -> str | None:
    text = ast.unparse(arg)
    for marker in _BANNED_MARKERS:
        if marker in text:
            return f"{source_file}:{arg.lineno}: materialize_candidate(first arg={text!r}) names the running tree via {marker!r}"
    return None


def _scan(root: Path) -> list[str]:
    violations: list[str] = []
    for source_file in sorted(root.rglob("*.py")):
        tree = ast.parse(
            source_file.read_text(encoding="utf-8"), filename=str(source_file)
        )
        for arg in _materialize_candidate_first_arg_calls(tree):
            found = _violation(source_file, arg)
            if found is not None:
                violations.append(found)
    return violations


class TestNoTestMaterializesItsOwnTree:
    def test_no_materialize_candidate_call_roots_at_the_running_tree(self) -> None:
        violations = _scan(TESTS_ROOT)
        assert violations == [], "\n".join(violations)


class TestGuardDetectsTheShapesItClaimsTo:
    """The guard above is only useful if it actually fires -- pinned here
    against synthetic sources so a future edit to `_violation`/`_scan`
    cannot silently stop detecting the real shapes without a test noticing."""

    def _run(self, source: str) -> list[str]:
        tree = ast.parse(source, filename="<synthetic>")
        violations = []
        for arg in _materialize_candidate_first_arg_calls(tree):
            found = _violation(Path("<synthetic>"), arg)
            if found is not None:
                violations.append(found)
        return violations

    def test_bare_repo_root_name_is_rejected(self) -> None:
        assert self._run(
            "REPO_ROOT = Path(__file__).resolve().parents[3]\n"
            "materialize_candidate(REPO_ROOT, 'HEAD', destination)\n"
        )

    def test_inline_path_file_dunder_is_rejected(self) -> None:
        assert self._run(
            "materialize_candidate(Path(__file__).resolve().parents[3], 'HEAD', d)\n"
        )

    def test_path_cwd_is_rejected(self) -> None:
        assert self._run("materialize_candidate(Path.cwd(), 'HEAD', d)\n")

    def test_tmp_path_fixture_root_is_accepted(self) -> None:
        assert self._run("materialize_candidate(root, base, destination)\n") == []

    def test_starred_fixture_tuple_is_accepted(self) -> None:
        assert (
            self._run("materialize_candidate(*source, tmp_path / 'candidate')\n") == []
        )
