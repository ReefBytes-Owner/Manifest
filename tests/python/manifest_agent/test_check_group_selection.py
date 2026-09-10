"""C6b: a profile group that resolves zero checks must never read as PASS.

Before this rule existed, `resolve_checks(registry, "full", "security")`
legitimately returned zero checks (no `security`-group id was in `full`
until this same chunk folded them in) and `_report`/`_list_report` computed
PASS from an empty `statuses`/`pending` set -- a receipt about nothing.
Both `manifest check`'s execute path (`run_profile`) and its `--list` path
(`_list_report`) gate on `group_selection.guard_nonempty_group` instead.
"""

from __future__ import annotations

import pytest

from manifest_agent.checks import run_profile
from manifest_agent.checks.cli import _list_report
from manifest_agent.checks.group_selection import EmptyGroupError
from manifest_agent.checks.models import CheckSpec
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture


def _registry_with_one_test_group_check() -> dict:
    """A minimal registry whose only check lives in group ``test`` -- asking
    for group ``lint`` reproduces the empty-selection shape without
    depending on the real registry's `security`-fold history."""
    tool = {
        "executable": "python3",
        "version_argv": ("python3", "--version"),
        "expected_version": "ok",
        "required_modules": (),
    }
    check = CheckSpec(
        id="check.only",
        category="test",
        group="test",
        argv=("python3", "-c", "pass"),
        cwd=".",
        inputs=(".",),
        dependencies=(),
        timeout_seconds=5.0,
        selection="project",
        tool="python",
        version="ok",
    )
    return {
        "schema_version": 1,
        "tools": {"python": tool},
        "checks": (check,),
        "candidate_preparations": (),
        "profiles": dict.fromkeys(
            ("quick", "full", "security", "release"), ("check.only",)
        ),
        "coverage_pending": dict.fromkeys(("quick", "full", "security", "release"), ()),
    }


@pytest.fixture
def candidate(source, tmp_path):
    return materialize(source, tmp_path)


def test_run_profile_blocks_instead_of_passing_an_empty_group(candidate):
    registry = _registry_with_one_test_group_check()

    with pytest.raises(
        EmptyGroupError, match="profile quick selects no checks in group lint"
    ):
        run_profile(registry, "quick", "lint", candidate, {})


def test_list_report_blocks_instead_of_passing_an_empty_group():
    registry = _registry_with_one_test_group_check()

    with pytest.raises(
        EmptyGroupError, match="profile quick selects no checks in group lint"
    ):
        _list_report(registry, "quick", "lint")


def test_nonempty_group_is_unaffected(candidate):
    registry = _registry_with_one_test_group_check()

    report = run_profile(registry, "quick", "test", candidate, {})

    assert report["partial"] is True
    assert [result["id"] for result in report["results"]] == ["check.only"]
