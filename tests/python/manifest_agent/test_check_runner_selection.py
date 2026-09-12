"""Candidate input selection and filename-forwarding contracts."""

from __future__ import annotations

import json
import sys
from dataclasses import replace

import pytest

from manifest_agent.checks import execute_check, run_profile
from tests.python.manifest_agent.test_check_runner import _with_checks
from tests.python.manifest_agent.test_check_runner import candidate as candidate_fixture
from tests.python.manifest_agent.test_check_runner import (
    check_fixture as check_fixture_fixture,
)
from tests.python.manifest_agent.test_check_runner import source as source_fixture

candidate = candidate_fixture
check_fixture = check_fixture_fixture
source = source_fixture


def test_missing_project_input_blocks_but_empty_changed_selection_is_not_applicable(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    project = replace(registry["checks"][0], inputs=("absent.file",))
    blocked = run_profile(_with_checks(registry, project), "full", None, candidate, {})
    changed = replace(
        registry["checks"][0],
        category="lint",
        group="lint",
        selection="changed",
        inputs=("docs/*.md",),
    )
    not_applicable = run_profile(
        _with_checks(registry, changed), "full", None, candidate, {}
    )
    missing_registry = check_fixture("missing-tool")
    missing_changed = replace(
        missing_registry["checks"][0],
        category="lint",
        group="lint",
        selection="changed",
        inputs=("docs/*.md",),
    )
    missing_registry = _with_checks(missing_registry, missing_changed)
    missing_tool_not_applicable = run_profile(
        missing_registry, "full", None, candidate, {}
    )
    assert blocked["results"][0]["status"] == "BLOCKED"
    assert "missing required input" in blocked["results"][0]["diagnostics"]
    assert not_applicable["status"] == "PASS"
    assert not_applicable["results"][0]["status"] == "NOT_APPLICABLE"
    assert (
        "zero applicable changed paths" in not_applicable["results"][0]["diagnostics"]
    )
    assert not_applicable["results"][0]["selected_inputs"] == []
    assert missing_tool_not_applicable["results"][0]["status"] == "NOT_APPLICABLE"


def test_missing_project_input_blocks_before_filtered_project_is_not_applicable(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    check = replace(
        registry["checks"][0],
        inputs=("absent.file",),
        include_regex=r"^never/",
    )

    report = run_profile(_with_checks(registry, check), "full", None, candidate, {})

    assert report["status"] == "BLOCKED"
    assert report["results"][0]["status"] == "BLOCKED"
    assert "missing required input" in report["results"][0]["diagnostics"]


def test_execute_check_does_not_launch_for_missing_project_input(
    check_fixture, candidate, tmp_path
):
    registry = check_fixture("mark")
    marker = tmp_path / "checker-ran"
    check = replace(
        registry["checks"][0],
        inputs=("absent.file",),
        include_regex=r"^never/",
    )

    result = execute_check(check, candidate, {"CHECK_MARKER": str(marker)})

    assert result.status == "BLOCKED"
    assert "missing required input" in result.diagnostics
    assert not marker.exists()


def test_execute_check_does_not_launch_for_zero_applicable_project_paths(
    check_fixture, candidate, tmp_path
):
    registry = check_fixture("mark")
    marker = tmp_path / "checker-ran"
    check = replace(registry["checks"][0], include_regex=r"^never/")

    result = execute_check(check, candidate, {"CHECK_MARKER": str(marker)})

    assert result.status == "NOT_APPLICABLE"
    assert "zero applicable project paths" in result.diagnostics
    assert not marker.exists()


def test_changed_selection_appends_only_matching_candidate_paths(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    changed = replace(
        registry["checks"][0],
        id="check.show-args",
        category="lint",
        group="lint",
        argv=(sys.executable, "runner.py"),
        cwd="pkg",
        inputs=("./pkg",),
        selection="changed",
        pass_filenames=True,
    )
    report = run_profile(_with_checks(registry, changed), "quick", None, candidate, {})
    assert report["status"] == "PASS"
    selected = report["results"][0]["selected_inputs"]
    assert selected and all(path.startswith("pkg/") for path in selected)
    assert set(report["results"][0]["diagnostics"].split()) == {
        path.removeprefix("pkg/") for path in selected
    }


def test_filtered_project_selection_appends_only_selected_candidate_paths(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    check = replace(
        registry["checks"][0],
        id="check.show-project-args",
        argv=(sys.executable, "show-args.py"),
        inputs=("pkg",),
        include_regex=r"^pkg/input\.py$",
        pass_filenames=True,
    )

    report = run_profile(_with_checks(registry, check), "full", None, candidate, {})

    assert report["status"] == "PASS"
    assert report["results"][0]["selected_inputs"] == ["pkg/input.py"]
    assert report["results"][0]["diagnostics"].strip() == "pkg/input.py"


def test_filtered_project_without_filename_contract_retains_original_argv(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    check = replace(
        registry["checks"][0],
        id="check.show-project-args",
        argv=(sys.executable, "show-json-args.py", "fixed"),
        inputs=("pkg",),
        include_regex=r"^pkg/input\.py$",
        pass_filenames=False,
    )

    result = execute_check(check, candidate, {})

    assert result.status == "PASS"
    assert result.selected_inputs == ("pkg/input.py",)
    assert json.loads(result.diagnostics) == ["fixed"]


def test_forwarded_changed_filenames_preserve_argv_boundaries_and_safe_dash(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    check = replace(
        registry["checks"][0],
        id="check.show-special-args",
        category="lint",
        group="lint",
        argv=(sys.executable, "show-json-args.py", "fixed"),
        inputs=(".",),
        include_regex=r"^(?:-leading\.py|space name\.py|line\nbreak)$",
        selection="changed",
        pass_filenames=True,
    )

    result = execute_check(check, candidate, {})

    assert result.status == "PASS"
    assert json.loads(result.diagnostics) == [
        "fixed",
        "./-leading.py",
        "line\nbreak",
        "space name.py",
    ]


@pytest.mark.parametrize(
    ("selection", "selector"),
    [("project", "."), ("project", "./pkg"), ("changed", ".")],
)
def test_root_and_dot_directory_selectors_match_project_and_changed_inputs(
    check_fixture, candidate, selection, selector
):
    registry = check_fixture("exit-zero")
    check = replace(
        registry["checks"][0],
        category="lint" if selection == "changed" else "test",
        group="lint" if selection == "changed" else "test",
        inputs=(selector,),
        selection=selection,
    )
    result = execute_check(check, candidate, {})
    assert result.status == "PASS"
    assert result.selected_inputs
    if selector == "./pkg":
        assert all(
            path == "pkg" or path.startswith("pkg/") for path in result.selected_inputs
        )
