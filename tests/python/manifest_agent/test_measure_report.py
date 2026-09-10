"""Unit coverage for `tools/project_checks/measure_report.py` (5c).

Read-only over a real telemetry JSONL built with `telemetry.build_record` --
never a hand-rolled dict shaped to make the assertion trivially pass. The
two headline properties: a run with no provider cost figure renders as
`"unknown"`, never `0`; a lineage with 3 attempts where 1 is uncosted
renders `"unknown (2 of 3 attempts costed)"`.
"""

from __future__ import annotations

import json

from manifest_agent.checks import telemetry
from tools.project_checks import measure_report


def _write_records(path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")


def _record(**overrides) -> dict:
    inputs = telemetry.RunRecordInputs(
        profile=overrides.pop("profile", "full"),
        status=overrides.pop("status", "PASS"),
        duration_seconds=overrides.pop("duration_seconds", 10.0),
        candidate_lineage=overrides.pop("candidate_lineage", "123"),
        attempt=overrides.pop("attempt", 1),
        cost=overrides.pop("cost", telemetry.CostInfo()),
    )
    return telemetry.build_record(inputs)


def test_a_run_with_no_provider_cost_figure_renders_as_unknown_never_zero(tmp_path):
    runs_file = tmp_path / "runs.jsonl"
    _write_records(runs_file, [_record(cost=telemetry.CostInfo())])

    report = measure_report.build_report(measure_report.load_records(runs_file), None)
    rendered = measure_report.render(report)

    cost = report["cost_per_accepted_change"]["123"]
    assert isinstance(cost, str) and cost.startswith("unknown")
    assert cost != 0
    assert "unknown" in rendered
    assert "$0.00" not in rendered
    assert "0.00" not in rendered.split("cost per accepted change:")[1]


def test_cost_per_accepted_change_with_one_uncosted_attempt_of_three(tmp_path):
    runs_file = tmp_path / "runs.jsonl"
    records = [
        _record(status="FAIL", attempt=1, cost=telemetry.CostInfo("known", 1.5)),
        _record(status="FAIL", attempt=2, cost=telemetry.CostInfo()),  # uncosted
        _record(status="PASS", attempt=3, cost=telemetry.CostInfo("known", 2.25)),
    ]
    _write_records(runs_file, records)

    report = measure_report.build_report(measure_report.load_records(runs_file), None)

    assert (
        report["cost_per_accepted_change"]["123"] == "unknown (2 of 3 attempts costed)"
    )
    rendered = measure_report.render(report)
    assert "unknown (2 of 3 attempts costed)" in rendered


def test_cost_per_accepted_change_sums_known_costs_across_all_attempts(tmp_path):
    runs_file = tmp_path / "runs.jsonl"
    records = [
        _record(status="FAIL", attempt=1, cost=telemetry.CostInfo("known", 1.0)),
        _record(status="PASS", attempt=2, cost=telemetry.CostInfo("known", 2.0)),
    ]
    _write_records(runs_file, records)

    report = measure_report.build_report(measure_report.load_records(runs_file), None)

    assert report["cost_per_accepted_change"]["123"] == 3.0


def test_only_accepted_lineages_appear_in_cost_per_accepted_change(tmp_path):
    runs_file = tmp_path / "runs.jsonl"
    records = [
        _record(status="FAIL", candidate_lineage="never-accepted", attempt=1),
        _record(status="PASS", candidate_lineage="accepted", attempt=1),
    ]
    _write_records(runs_file, records)

    report = measure_report.build_report(measure_report.load_records(runs_file), None)

    assert "never-accepted" not in report["cost_per_accepted_change"]
    assert "accepted" in report["cost_per_accepted_change"]


def test_attempts_and_repair_cycles_are_counted_per_lineage(tmp_path):
    runs_file = tmp_path / "runs.jsonl"
    records = [
        _record(status="FAIL", attempt=1),
        _record(status="FAIL", attempt=2),
        _record(status="PASS", attempt=3),
    ]
    _write_records(runs_file, records)

    report = measure_report.build_report(measure_report.load_records(runs_file), None)

    assert report["attempts"]["123"] == 3
    assert report["repair_cycles"]["123"] == 2  # two FAIL->re-run transitions


def test_check_duration_percentiles_render_unknown_on_empty_corpus():
    report = measure_report.build_report([], None)

    assert report["check_duration_seconds"] == {"p50": "unknown", "p95": "unknown"}


def test_review_time_renders_unknown_when_no_fetch_was_performed(tmp_path):
    runs_file = tmp_path / "runs.jsonl"
    _write_records(runs_file, [_record()])

    report = measure_report.build_report(measure_report.load_records(runs_file), None)

    assert report["review_time_seconds"] == "unknown"


def test_review_time_uses_the_injected_gh_api_fetcher_not_the_network():
    calls = []

    def fake_gh_api(url: str) -> dict:
        calls.append(url)
        if url.endswith("/pulls/482"):
            return {"created_at": "2026-09-01T00:00:00Z"}
        return [{"state": "APPROVED", "submitted_at": "2026-09-01T01:00:00Z"}]

    fetch = measure_report.make_fetch_review_time(fake_gh_api)
    seconds = fetch("owner/repo", "482")

    assert seconds == 3600.0
    assert calls  # the fake was actually invoked, not bypassed


def test_main_cli_reads_runs_file_and_prints_a_report(tmp_path, capsys):
    runs_file = tmp_path / "runs.jsonl"
    _write_records(runs_file, [_record()])

    exit_code = measure_report.main(["--runs-file", str(runs_file)])

    assert exit_code == measure_report.PASS
    output = capsys.readouterr().out
    assert "Manifest measurement report" in output
    assert "unknown" in output


def test_a_bool_shaped_cost_amount_is_rejected_not_rendered_as_a_dollar_figure(
    tmp_path,
):
    """`isinstance(True, (int, float))` is `True` in Python, so a malformed
    or adversarial record with `amount_usd: true` must not silently render
    as `$1.00` -- a zero-for-unknown cousin defect in a chunk about honest
    numbers. `_numeric` rejects `bool` explicitly."""
    runs_file = tmp_path / "runs.jsonl"
    malformed = _record(status="PASS", attempt=1)
    malformed["cost"] = {"status": "known", "amount_usd": True}
    _write_records(runs_file, [malformed])

    report = measure_report.build_report(measure_report.load_records(runs_file), None)

    cost = report["cost_per_accepted_change"]["123"]
    assert isinstance(cost, str) and cost.startswith("unknown")


def test_a_bool_shaped_duration_is_excluded_from_the_percentile_corpus(tmp_path):
    """Same defect, the duration side: a `duration_seconds: true` record
    must not silently join the numeric corpus and skew p50/p95."""
    runs_file = tmp_path / "runs.jsonl"
    malformed = _record(status="PASS", attempt=1)
    malformed["duration_seconds"] = True
    _write_records(runs_file, [malformed])

    report = measure_report.build_report(measure_report.load_records(runs_file), None)

    assert report["check_duration_seconds"] == {"p50": "unknown", "p95": "unknown"}


def test_main_cli_json_output_never_coerces_unknown_cost_to_zero(tmp_path, capsys):
    runs_file = tmp_path / "runs.jsonl"
    _write_records(runs_file, [_record()])

    measure_report.main(["--runs-file", str(runs_file), "--json"])

    payload = json.loads(capsys.readouterr().out)
    cost = payload["cost_per_accepted_change"]["123"]
    assert isinstance(cost, str) and cost.startswith("unknown")
