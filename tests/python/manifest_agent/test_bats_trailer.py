"""Behavior tests for the bats not-ok failure trailer (Correction 15 rule 3).

`append_not_ok_trailer` is the pure function; the second test proves
`execute_check` actually wires it in for `check.id == "test.bats"` and
leaves every other check's stdout untouched.
"""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest

from manifest_agent.checks import execute_check
from manifest_agent.checks.bats_trailer import append_not_ok_trailer
from manifest_agent.checks.models import CheckSpec
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture


def test_all_passing_gets_a_zero_trailer():
    stream = "1..2\nok 1 first\nok 2 second\n"
    assert append_not_ok_trailer(stream) == (
        "1..2\nok 1 first\nok 2 second\n# not ok summary: 0\n"
    )


def test_one_failure_is_named_even_if_the_head_would_be_truncated():
    stream = "1..3\nok 1 first\nnot ok 2 second\nok 3 third\n"
    result = append_not_ok_trailer(stream)
    lines = result.splitlines()
    assert lines[-2:] == ["# not ok summary: 1", "not ok 2 second"]


def test_multiple_failures_are_all_named_in_declaration_order():
    stream = "1..4\nok 1 a\nnot ok 2 b\nok 3 c\nnot ok 4 d\n"
    result = append_not_ok_trailer(stream)
    assert result.splitlines()[-3:] == [
        "# not ok summary: 2",
        "not ok 2 b",
        "not ok 4 d",
    ]


def test_missing_trailing_newline_still_gets_its_own_trailer_line():
    assert (
        append_not_ok_trailer("not ok 1 x")
        == "not ok 1 x\n# not ok summary: 1\nnot ok 1 x\n"
    )


@pytest.fixture
def candidate(source, tmp_path):
    (source[0] / "fake-bats.py").write_text(
        "import sys\n"
        "sys.stdout.write('1..2\\nok 1 first\\nnot ok 2 second\\n')\n"
        "sys.exit(1)\n"
    )
    return materialize(source, tmp_path)


def _bats_check(argv) -> CheckSpec:
    return CheckSpec(
        id="test.bats",
        category="test",
        group="test",
        argv=argv,
        cwd=".",
        inputs=("fake-bats.py",),
        dependencies=(),
        timeout_seconds=5.0,
        selection="project",
        tool="test.bats",
        version="1.0.0",
    )


def test_execute_check_appends_the_trailer_only_for_test_bats(candidate):
    check = _bats_check((sys.executable, "fake-bats.py"))
    result = execute_check(check, candidate, {})

    assert result.status == "FAIL"
    assert "not ok 2 second" in result.diagnostics
    assert result.diagnostics.rstrip().endswith("not ok 2 second")
    assert "# not ok summary: 1" in result.diagnostics


def test_execute_check_leaves_other_checks_stdout_untouched(candidate):
    check = _bats_check((sys.executable, "fake-bats.py"))
    other = replace(check, id="test.other")
    result = execute_check(other, candidate, {})

    assert "# not ok summary" not in result.diagnostics
