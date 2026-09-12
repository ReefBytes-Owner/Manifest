"""Unit coverage for `manifest_agent.checks.telemetry` (phase-3-5-decisions.md
5c). Drives the real record-building and real append path against a real
tmp-dir XDG_STATE_HOME -- never a mocked cost figure, never a fixture that
merely proves a property of itself.

Headline properties pinned here:
- unknown is never zero (`model_id`, `runtime.version`, `cost` default to
  the literal string `"unknown"`, never `0`/`""`)
- a telemetry write failure (unwritable directory) never raises out of
  `record_run` -- the caller's exit code is never at risk
- records are append-only across repeated writes
- no secret/env-shaped value reaches the JSONL
- concurrent writers on the same lineage never compute the same attempt
  number (count-then-append is one atomic operation under one lock hold)
"""

from __future__ import annotations

import json
import multiprocessing
import stat

import pytest

from manifest_agent.checks import telemetry


def _record_run_worker(state_home: str, lineage: str) -> None:
    """Module-level so `multiprocessing.Process` (spawn start method) can
    pickle it. Runs in a real separate process -- not a thread, not a
    simulated sequence of calls -- against the real `record_run` lock path."""
    from manifest_agent.checks import telemetry as _telemetry

    env = {"XDG_STATE_HOME": state_home, "GITHUB_REF_NAME": lineage}
    request = _telemetry.RecordRunRequest(
        profile="quick", status="PASS", duration_seconds=0.1, source_root=None
    )
    _telemetry.record_run(request, env=env)


@pytest.fixture
def state_env(tmp_path, monkeypatch):
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    for name in ("GITHUB_REF", "GITHUB_REF_NAME", "GITHUB_HEAD_REF"):
        monkeypatch.delenv(name, raising=False)
    return {"XDG_STATE_HOME": str(state_home)}


def _read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def unwritable_telemetry_dir(state_env):
    """Yields the telemetry dir made read+traverse-only (no write), restored
    to normal permissions on teardown regardless of what the test does."""
    directory = telemetry.telemetry_dir()
    directory.mkdir(parents=True)
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        yield directory
    finally:
        directory.chmod(stat.S_IRWXU)


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


def test_record_run_never_raises_when_directory_is_unwritable(unwritable_telemetry_dir):
    request = telemetry.RecordRunRequest(
        profile="quick", status="PASS", duration_seconds=0.1, source_root=None
    )
    telemetry.record_run(request)  # must not raise

    assert not (unwritable_telemetry_dir / telemetry.RECORD_FILENAME).exists()


def test_a_write_failure_is_reported_on_stderr_never_stdout(
    unwritable_telemetry_dir, capsys
):
    """A permanently unwritable state dir must not yield an empty corpus
    with no hint. stderr only -- never stdout, which is `manifest check`'s
    and `manifest hook`'s single-JSON-document protocol channel -- and the
    exit code (`record_run` still returns normally) is untouched."""
    request = telemetry.RecordRunRequest(
        profile="quick", status="PASS", duration_seconds=0.1, source_root=None
    )
    telemetry.record_run(request)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "manifest telemetry" in captured.err


def test_record_run_is_the_single_safety_boundary_for_the_whole_path(
    state_env, tmp_path
):
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
        telemetry.record_run(
            telemetry.RecordRunRequest(
                profile="quick",
                status="PASS",
                duration_seconds=float(index),
                source_root=None,
            )
        )

    records = _read_lines(telemetry.telemetry_path())
    assert len(records) == 3
    assert [record["duration_seconds"] for record in records] == [0.0, 1.0, 2.0]


def test_no_secret_reaches_the_jsonl(state_env):
    """`redact_text` runs on every free-text field before the record is
    built (`head_sha`, `candidate_lineage`). The prior version of this test
    also `monkeypatch.setenv("GITHUB_TOKEN", ...)`, but nothing in
    `build_record`/`record_run` ever reads process env into a record field,
    so that assertion passed unconditionally regardless of redaction and
    proved nothing -- removed. In its place: the secret must be absent not
    just from the in-memory dict but from what `append_record` actually
    writes to disk, so a regression that redacts the return value but not
    the bytes on disk would still be caught."""
    inputs = telemetry.RunRecordInputs(
        profile="full",
        status="BLOCKED",
        duration_seconds=1.0,
        head_sha="deadbeef api_key=ghp_supersecrettoken1234567890 trailing",
        candidate_lineage="feature/api_key=verysecretvalue123",
    )

    record = telemetry.build_record(inputs)
    telemetry.append_record(record)
    on_disk = telemetry.telemetry_path().read_text(encoding="utf-8")

    assert "ghp_supersecrettoken1234567890" not in on_disk
    assert "verysecretvalue123" not in on_disk
    assert "[REDACTED]" in record["head_sha"]


def test_count_prior_attempts_increments_per_lineage(state_env):
    telemetry.append_record(
        telemetry.build_record(
            telemetry.RunRecordInputs(
                profile="quick",
                status="FAIL",
                duration_seconds=1.0,
                candidate_lineage="123",
                attempt=1,
            )
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


def test_concurrent_writers_on_one_lineage_get_distinct_attempt_numbers(tmp_path):
    """Regression for the count-then-append race: `count_prior_attempts`
    used to read the JSONL outside the `flock` `append_record` takes, so two
    processes racing on the same lineage could both count the same prior
    records and both write `attempt: N`. Real OS processes, not threads and
    not a simulated call sequence, so the race window is the real one."""
    state_home = str(tmp_path / "xdg-state-concurrent")
    worker_count = 8
    processes = [
        multiprocessing.Process(
            target=_record_run_worker, args=(state_home, "race-lineage")
        )
        for _ in range(worker_count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0

    records = _read_lines(telemetry.telemetry_path({"XDG_STATE_HOME": state_home}))
    attempts = sorted(
        record["attempt"]
        for record in records
        if record["candidate_lineage"] == "race-lineage"
    )
    assert attempts == list(range(1, worker_count + 1))
