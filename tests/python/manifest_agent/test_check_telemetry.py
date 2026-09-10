"""Unit coverage for `manifest_agent.checks.telemetry` (phase-3-5-decisions.md
5c). Drives the real record-building and real append path against a real
tmp-dir XDG_STATE_HOME -- never a mocked cost figure, never a fixture that
merely proves a property of itself.

Headline properties pinned here:
- unknown is never zero (`model_id`, `runtime.version`, `cost` default to
  the literal string `"unknown"`, never `0`/`""`)
- a telemetry write failure (unwritable directory) never raises out of
  `record_run`/`write_record` -- the caller's exit code is never at risk
- records are append-only across repeated writes
- no secret/env-shaped value reaches the JSONL
"""

from __future__ import annotations

import json
import stat

import pytest

from manifest_agent.checks import telemetry


@pytest.fixture
def state_env(tmp_path, monkeypatch):
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    return {"XDG_STATE_HOME": str(state_home)}


def _read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_unset_cost_and_runtime_render_as_unknown_never_zero(state_env):
    inputs = telemetry.RunRecordInputs(
        profile="full", status="FAIL", duration_seconds=412.3
    )
    record = telemetry.build_record(inputs)

    assert record["model_id"] == "unknown"
    assert record["runtime"] == {"client": "unknown", "version": "unknown"}
    assert record["cost"] == {"status": "unknown"}
    assert record["model_id"] != 0
    assert record["cost"].get("amount_usd") is None


def test_known_cost_carries_a_real_provider_figure(state_env):
    inputs = telemetry.RunRecordInputs(
        profile="security",
        status="PASS",
        duration_seconds=1.0,
        cost=telemetry.CostInfo(status="known", amount_usd=0.42),
    )
    record = telemetry.build_record(inputs)

    assert record["cost"] == {"status": "known", "amount_usd": 0.42}


def test_write_record_never_raises_when_directory_is_unwritable(state_env, tmp_path):
    directory = telemetry.telemetry_dir()
    directory.mkdir(parents=True)
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)  # read+traverse only, no write
    try:
        inputs = telemetry.RunRecordInputs(
            profile="quick", status="PASS", duration_seconds=0.1
        )
        telemetry.write_record(inputs)  # must not raise
    finally:
        directory.chmod(stat.S_IRWXU)

    assert not (directory / telemetry.RECORD_FILENAME).exists()


def test_record_run_is_the_single_safety_boundary_for_the_whole_path(state_env, tmp_path):
    """Even a `resolve_lineage`-side failure (an unreadable/non-git source
    root) must not escape `record_run`."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    request = telemetry.RecordRunRequest(
        profile="quick",
        status="PASS",
        duration_seconds=0.2,
        source_root=not_a_repo,
    )

    telemetry.record_run(request)  # must not raise

    records = _read_lines(telemetry.telemetry_path())
    assert len(records) == 1
    assert records[0]["candidate_lineage"] is None


def test_records_are_append_only_across_repeated_runs(state_env):
    for index in range(3):
        telemetry.write_record(
            telemetry.RunRecordInputs(
                profile="quick", status="PASS", duration_seconds=float(index)
            )
        )

    records = _read_lines(telemetry.telemetry_path())
    assert len(records) == 3
    assert [record["duration_seconds"] for record in records] == [0.0, 1.0, 2.0]


def test_no_secret_or_env_value_reaches_the_jsonl(state_env, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_supersecrettoken1234567890")
    inputs = telemetry.RunRecordInputs(
        profile="full",
        status="BLOCKED",
        duration_seconds=1.0,
        head_sha="deadbeef api_key=ghp_supersecrettoken1234567890 trailing",
        candidate_lineage="feature/api_key=verysecretvalue123",
    )

    record = telemetry.build_record(inputs)
    serialized = json.dumps(record)

    assert "ghp_supersecrettoken1234567890" not in serialized
    assert "verysecretvalue123" not in serialized
    assert "[REDACTED]" in record["head_sha"]


def test_count_prior_attempts_increments_per_lineage(state_env):
    telemetry.write_record(
        telemetry.RunRecordInputs(
            profile="quick",
            status="FAIL",
            duration_seconds=1.0,
            candidate_lineage="123",
            attempt=1,
        )
    )

    assert telemetry.count_prior_attempts("123") == 2
    assert telemetry.count_prior_attempts("456") == 1
    assert telemetry.count_prior_attempts(None) == 1


def test_resolve_lineage_prefers_pr_number_from_github_ref(state_env):
    env = {"GITHUB_REF": "refs/pull/482/merge"}
    assert telemetry.resolve_lineage(env, None) == "482"


def test_resolve_lineage_falls_back_to_branch_ref_name(state_env):
    env = {"GITHUB_REF_NAME": "feat/my-branch"}
    assert telemetry.resolve_lineage(env, None) == "feat/my-branch"


def test_resolve_lineage_is_null_when_nothing_is_determinable(state_env, tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    assert telemetry.resolve_lineage({}, not_a_repo) is None
