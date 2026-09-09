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
