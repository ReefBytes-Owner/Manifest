"""Contracts for the read-only current-run CI context collector."""

from __future__ import annotations

import pytest

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


def test_collect_ci_context_builds_trusted_context_from_env_and_fetch():
    jobs = [
        {"group": "lint", "job_id": "1", "conclusion": "success", "artifact_id": "a"}
    ]

    result = collect_ci_context(ENV, _fake_fetch(jobs))

    assert result == {
        "repository": "acme/example",
        "workflow": "ci.yml",
        "run_id": "1001",
        "run_attempt": 1,
        "tested_sha": "a" * 40,
        "producer_jobs": jobs,
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
