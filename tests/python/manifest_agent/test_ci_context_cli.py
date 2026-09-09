"""Unit coverage for `tools/project_checks/ci_context_cli.py`.

No network and no `gh` subprocess: the `gh_api` fetcher is injected via
`make_fetch_jobs`, and `main()`'s hard-wired `_gh_api` is monkeypatched so the
whole CLI path (env -> fetch -> --output file) runs against fixtures only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.project_checks import ci_context_cli


def _fake_jobs_payload() -> dict:
    return {
        "jobs": [
            {"id": 111, "name": "Shadow Checks (structure)", "conclusion": "success"},
            {"id": 222, "name": "Shadow Checks (lint)", "conclusion": "failure"},
            {"id": 333, "name": "Lint & Validate", "conclusion": "success"},
            # Regression fixture: the aggregate job's OWN name ends in a
            # parenthesized word too. A loose suffix regex on `(<group>)`
            # would misclassify this as a producer for group "non-blocking",
            # which `aggregate.py` then rejects as unexpected -- silently
            # self-invalidating every real run. Must never appear in the
            # selected jobs below.
            {
                "id": 444,
                "name": "Shadow Checks Aggregate (non-blocking)",
                "conclusion": "success",
            },
        ]
    }


def _fake_artifacts_payload() -> dict:
    return {
        "artifacts": [
            {"id": 9001, "name": "shadow-receipt-structure-1"},
            {"id": 9002, "name": "shadow-receipt-lint-1"},
        ]
    }


class TestMakeFetchJobs:
    def test_selects_only_shadow_group_jobs_and_maps_fields(self) -> None:
        def fake_gh_api(url: str) -> dict:
            if "/artifacts" in url:
                return _fake_artifacts_payload()
            return _fake_jobs_payload()

        fetch_jobs = ci_context_cli.make_fetch_jobs(gh_api=fake_gh_api)
        jobs = fetch_jobs("owner/repo", "42", "1")

        by_group = {job["group"]: job for job in jobs}
        assert set(by_group) == {"structure", "lint"}
        assert by_group["structure"]["job_id"] == "111"
        assert by_group["structure"]["conclusion"] == "success"
        assert by_group["structure"]["artifact_id"] == "9001"
        assert by_group["structure"]["run_attempt"] == 1
        assert by_group["lint"]["conclusion"] == "failure"

    def test_missing_artifact_yields_empty_artifact_id(self) -> None:
        def fake_gh_api(url: str) -> dict:
            if "/artifacts" in url:
                return {"artifacts": []}
            return {
                "jobs": [
                    {"id": 1, "name": "Shadow Checks (test)", "conclusion": "success"}
                ]
            }

        fetch_jobs = ci_context_cli.make_fetch_jobs(gh_api=fake_gh_api)
        (job,) = fetch_jobs("owner/repo", "42", "1")
        assert job["artifact_id"] == ""


class TestMain:
    def test_writes_context_file_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("GITHUB_WORKFLOW", "Manifest CI")
        monkeypatch.setenv("GITHUB_RUN_ID", "42")
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
        monkeypatch.setenv("GITHUB_SHA", "deadbeef")

        def fake_gh_api(url: str) -> dict:
            if "/artifacts" in url:
                return _fake_artifacts_payload()
            return _fake_jobs_payload()

        monkeypatch.setattr(ci_context_cli, "_gh_api", fake_gh_api)

        output = tmp_path / "context.json"
        exit_code = ci_context_cli.main(["--output", str(output)])

        assert exit_code == ci_context_cli.PASS
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["repository"] == "owner/repo"
        assert payload["run_attempt"] == 1
        assert {job["group"] for job in payload["producer_jobs"]} == {
            "structure",
            "lint",
        }

    def test_missing_env_is_blocked_and_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in (
            "GITHUB_REPOSITORY",
            "GITHUB_WORKFLOW",
            "GITHUB_RUN_ID",
            "GITHUB_RUN_ATTEMPT",
            "GITHUB_SHA",
        ):
            monkeypatch.delenv(key, raising=False)

        output = tmp_path / "context.json"
        exit_code = ci_context_cli.main(["--output", str(output)])

        assert exit_code == ci_context_cli.BLOCKED
        assert not output.exists()
