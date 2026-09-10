"""`manifest check` writes one append-only telemetry record per run (5c).

Reuses `test_check_cli.py`'s real-Git fixture harness (`runner`,
`configured_project`, `_invoke`) rather than re-deriving it, and drives the
actual CLI entry point end to end -- no mocked report, no faked cost figure.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from manifest_agent.checks import telemetry
from tests.python.manifest_agent.test_check_cli import (
    ConfiguredProject,
    _check,
    _invoke,
    configured_project,
    runner,
)

configured_project = configured_project
runner = runner


@pytest.fixture(autouse=True)
def isolated_state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_home = tmp_path / "xdg-state-cli"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    return state_home


def _records() -> list[dict]:
    path = telemetry.telemetry_path()
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_run_writes_exactly_one_telemetry_record(
    runner: CliRunner, configured_project: ConfiguredProject
):
    result = _invoke(runner, configured_project, "--base", configured_project.base)

    assert result.exit_code == 0
    records = _records()
    assert len(records) == 1
    assert records[0]["profile"] == "quick"
    assert records[0]["status"] == "PASS"
    assert records[0]["cost"] == {"status": "unknown"}
    assert records[0]["model_id"] == "unknown"


def test_repeated_runs_append_without_rewriting_prior_records(
    runner: CliRunner, configured_project: ConfiguredProject
):
    _invoke(runner, configured_project, "--base", configured_project.base)
    _invoke(runner, configured_project, "--base", configured_project.base)
    _invoke(runner, configured_project, "--base", configured_project.base)

    records = _records()
    assert len(records) == 3
    assert [record["attempt"] for record in records] == [1, 2, 3]


def test_listing_never_writes_a_telemetry_record(
    runner: CliRunner, configured_project: ConfiguredProject
):
    _invoke(runner, configured_project, "--list", "--json")

    assert not telemetry.telemetry_path().is_file()


def test_telemetry_write_failure_does_not_change_the_exit_code_or_report(
    runner: CliRunner, configured_project: ConfiguredProject, isolated_state_home: Path
):
    telemetry_dir = isolated_state_home / "manifest" / "telemetry"
    telemetry_dir.mkdir(parents=True)
    telemetry_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # unwritable
    try:
        result = _invoke(
            runner, configured_project, "--base", configured_project.base, "--json"
        )
    finally:
        telemetry_dir.chmod(stat.S_IRWXU)

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["status"] == "PASS"
    assert not (telemetry_dir / telemetry.RECORD_FILENAME).exists()


def test_a_failing_run_still_writes_status_fail_not_a_gate_change(
    runner: CliRunner, configured_project: ConfiguredProject
):
    configured_project.write_registry([_check("check.fail", "fail.py", "test")])

    result = _invoke(
        runner, configured_project, "--base", configured_project.base, "--json"
    )

    assert result.exit_code == 2
    records = _records()
    assert records[-1]["status"] == "FAIL"
