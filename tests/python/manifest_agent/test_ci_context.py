"""Contracts for the read-only current-run CI context collector."""

from __future__ import annotations

import pytest

from manifest_agent.checks.aggregate import aggregate_results
from manifest_agent.checks.runner import _config_digest
from tests.python.manifest_agent.aggregate_fixtures import (
    load_two_group_registry,
    receipt,
    result,
)
from tests.python.manifest_agent.check_registry_fixtures import (
    _registry_file as _registry_file,
)
from tools.project_checks.ci_context import CIContextBlockedError, collect_ci_context

ENV = {
    "GITHUB_REPOSITORY": "acme/example",
    "GITHUB_WORKFLOW": "ci.yml",
    "GITHUB_RUN_ID": "1001",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_SHA": "a" * 40,
}


def _fake_fetch(jobs):
    def fetch(repository: str, run_id: str, run_attempt: str):
        assert repository == ENV["GITHUB_REPOSITORY"]
        assert run_id == ENV["GITHUB_RUN_ID"]
        assert run_attempt == ENV["GITHUB_RUN_ATTEMPT"]
        return jobs

    return fetch


def test_collect_ci_context_stamps_the_current_run_attempt_onto_every_job():
    jobs = [
        {"group": "lint", "job_id": "1", "conclusion": "success", "artifact_id": "a"},
        {
            "group": "test",
            "job_id": "2",
            "conclusion": "success",
            "artifact_id": "b",
            # A provider response that already carries a (stale) run_attempt
            # must not leak through: the current run's own attempt wins.
            "run_attempt": 999,
        },
    ]

    result = collect_ci_context(ENV, _fake_fetch(jobs))

    assert result == {
        "repository": "acme/example",
        "workflow": "ci.yml",
        "run_id": "1001",
        "run_attempt": 1,
        "tested_sha": "a" * 40,
        "producer_jobs": [
            {
                "group": "lint",
                "job_id": "1",
                "conclusion": "success",
                "artifact_id": "a",
                "run_attempt": 1,
            },
            {
                "group": "test",
                "job_id": "2",
                "conclusion": "success",
                "artifact_id": "b",
                "run_attempt": 1,
            },
        ],
    }


@pytest.mark.parametrize(
    "missing", ["GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ATTEMPT"]
)
def test_collect_ci_context_missing_env_is_blocked(missing):
    env = {key: value for key, value in ENV.items() if key != missing}

    with pytest.raises(CIContextBlockedError, match=missing):
        collect_ci_context(env, _fake_fetch([]))


def test_collect_ci_context_fetch_failure_is_blocked():
    def failing_fetch(repository: str, run_id: str, run_attempt: str):
        raise RuntimeError("api unavailable")

    with pytest.raises(CIContextBlockedError, match="api unavailable"):
        collect_ci_context(ENV, failing_fetch)


def test_collect_ci_context_non_list_fetch_result_is_blocked():
    def bad_fetch(repository: str, run_id: str, run_attempt: str):
        return {"not": "a list"}

    with pytest.raises(CIContextBlockedError, match="non-list"):
        collect_ci_context(ENV, bad_fetch)


def test_collect_ci_context_output_is_accepted_by_aggregate_results(registry_file):
    """The two sides of the trust boundary must not drift: a context built by
    `collect_ci_context` from a fake-but-valid fetch must be usable, as-is, by
    `aggregate_results` to reach a non-BLOCKED verdict."""
    registry = load_two_group_registry(registry_file)
    digest = _config_digest(registry)
    tested_sha = ENV["GITHUB_SHA"]
    jobs = [
        {"group": "lint", "job_id": "1", "conclusion": "success", "artifact_id": "a"},
        {"group": "test", "job_id": "2", "conclusion": "success", "artifact_id": "b"},
    ]

    context = collect_ci_context(ENV, _fake_fetch(jobs))
    receipts = [
        receipt(
            group="lint", results=[result("lint.a")], digest=digest, head_sha=tested_sha
        ),
        receipt(
            group="test", results=[result("test.a")], digest=digest, head_sha=tested_sha
        ),
    ]

    report = aggregate_results(registry, "full", receipts, context)

    assert report["status"] == "PASS", report["diagnostics"]
