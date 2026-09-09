"""honors_status_contract: repo-owned exit 3 is BLOCKED, third-party stays FAIL.

Split out from test_check_runner.py (already at its own 500-line ceiling) so
this narrow exit-code-mapping concern does not grow that file further.
"""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest

from manifest_agent.checks import execute_check
from manifest_agent.checks.models import CheckSpec
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture


@pytest.fixture
def candidate(source, tmp_path):
    (source[0] / "exit-three.py").write_text("raise SystemExit(3)\n")
    (source[0] / "exit-one.py").write_text("raise SystemExit(1)\n")
    return materialize(source, tmp_path)


@pytest.fixture
def exit_three_check() -> CheckSpec:
    version = sys.version.split()[0]
    return CheckSpec(
        id="check.exit-three",
        category="test",
        group="test",
        argv=(sys.executable, "exit-three.py"),
        cwd=".",
        inputs=("exit-three.py",),
        dependencies=(),
        timeout_seconds=2.0,
        selection="project",
        tool="python",
        version=version,
    )


def test_repo_owned_body_exiting_three_is_recorded_blocked(exit_three_check, candidate):
    check = replace(exit_three_check, honors_status_contract=True)

    result = execute_check(check, candidate, {})

    assert result.status == "BLOCKED"
    assert result.returncode == 3


def test_third_party_tool_exiting_three_is_still_recorded_fail(
    exit_three_check, candidate
):
    assert exit_three_check.honors_status_contract is False

    result = execute_check(exit_three_check, candidate, {})

    assert result.status == "FAIL"
    assert result.returncode == 3


def test_contract_honoring_body_exiting_outside_0_2_3_is_blocked_not_fail(
    exit_three_check, candidate
):
    # A body that declares `honors_status_contract` but exits 1 (e.g. an
    # uncaught traceback) has violated its own contract: exit 1 carries no
    # agreed meaning under the 0/2/3 vocabulary, so it cannot be honestly
    # recorded as FAIL ("ran and found problems") -- that would fabricate a
    # finding the check never made. It must read as BLOCKED instead, with a
    # diagnostic naming the observed exit code.
    check = replace(
        exit_three_check,
        id="check.exit-one",
        argv=(sys.executable, "exit-one.py"),
        inputs=("exit-one.py",),
        honors_status_contract=True,
    )

    result = execute_check(check, candidate, {})

    assert result.status == "BLOCKED"
    assert result.returncode == 1
    assert "contract violation" in result.diagnostics
    assert "1" in result.diagnostics
