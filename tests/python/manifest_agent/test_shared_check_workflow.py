"""Workflow assertions for the shadow shared-check migration path (Task 9).

These tests parse `.github/workflows/ci.yml` and assert structural properties
of the *shadow* jobs added alongside the pre-existing `lint`/`test`/`validate`
required jobs — the shadow path is informational only (never a required
status), but its wiring must still be honest: it must invoke the shared
`manifest check` entry exactly (no drifted inline duplicate of the check
logic), carry a finite timeout, hold only read-scoped credentials, upload the
*current* run-attempt's receipts, and its aggregate job must use
`if: always()` while still rejecting a skipped/cancelled/failed upstream
producer rather than silently treating it as success.

Phase 3 is not done yet (`config/project-checks.json` still carries
`coverage_pending` for every group), so the shadow path cannot be promoted to
a required status — these tests only assert the shadow wiring itself, never
that it is required.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW_PATH = ROOT / ".github/workflows/ci.yml"

SHADOW_GROUP_JOBS = {
    "shadow-checks-structure": "structure",
    "shadow-checks-lint": "lint",
    "shadow-checks-test": "test",
}
SHADOW_AGGREGATE_JOB = "shadow-checks-aggregate"

WRITE_PERMISSION_VALUES = {"write"}


def _load_workflow() -> dict[str, Any]:
    with CI_WORKFLOW_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _jobs() -> dict[str, Any]:
    return _load_workflow()["jobs"]


def _run_texts(job: dict[str, Any]) -> list[str]:
    return [step["run"] for step in job.get("steps", []) if "run" in step]


class TestShadowGroupJobsExist:
    def test_all_shadow_group_jobs_present(self) -> None:
        jobs = _jobs()
        missing = [name for name in SHADOW_GROUP_JOBS if name not in jobs]
        assert not missing, f"missing shadow job(s): {missing}"

    def test_aggregate_job_present(self) -> None:
        assert SHADOW_AGGREGATE_JOB in _jobs()

    def test_legacy_required_jobs_untouched_by_name(self) -> None:
        # The shadow migration must add jobs, never replace lint/test/validate.
        jobs = _jobs()
        for legacy in ("lint", "test", "validate"):
            assert legacy in jobs, f"legacy required job {legacy!r} was removed"


@pytest.mark.parametrize("job_name,group", sorted(SHADOW_GROUP_JOBS.items()))
class TestShadowGroupJobShape:
    def test_invokes_shared_command_exactly_once(
        self, job_name: str, group: str
    ) -> None:
        job = _jobs()[job_name]
        check_steps = [
            run for run in _run_texts(job) if re.search(r"\bmanifest\s+check\b", run)
        ]
        assert len(check_steps) == 1, (
            f"{job_name}: expected exactly one step invoking the shared "
            f"`manifest check` command, found {len(check_steps)}: {check_steps}"
        )
        (command,) = check_steps
        command = command.strip()
        assert command.startswith("uv run manifest check "), (
            f"{job_name}: shared-check step must invoke `uv run manifest "
            f"check ...` verbatim, not a drifted variant: {command!r}"
        )
        assert f"--group {group}" in command, (
            f"{job_name}: shared-check step must select group {group!r}: {command!r}"
        )

    def test_no_drifted_duplicate_check_logic(self, job_name: str, group: str) -> None:
        # None of the OTHER run steps in a shadow job may re-implement check
        # logic (e.g. a stray `ruff check` or `shellcheck` call) — the shadow
        # job's only verification step is the shared command.
        job = _jobs()[job_name]
        banned = re.compile(r"\b(ruff|shellcheck|yamllint|pytest|shfmt)\b")
        for run in _run_texts(job):
            if re.search(r"\bmanifest\s+check\b", run):
                continue
            assert not banned.search(run), (
                f"{job_name}: non-shared-check step duplicates check logic: {run!r}"
            )

    def test_has_finite_timeout(self, job_name: str, group: str) -> None:
        job = _jobs()[job_name]
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int) and timeout > 0, (
            f"{job_name}: timeout-minutes must be a positive, finite integer, "
            f"got {timeout!r}"
        )

    def test_credentials_are_read_only(self, job_name: str, group: str) -> None:
        job = _jobs()[job_name]
        permissions = job.get("permissions")
        assert permissions is not None, (
            f"{job_name}: must declare explicit least-privilege `permissions:`"
        )
        assert isinstance(permissions, dict)
        for scope, level in permissions.items():
            assert level not in WRITE_PERMISSION_VALUES, (
                f"{job_name}: permission {scope!r} must not grant write access "
                f"on the shadow path, got {level!r}"
            )

    def test_uploads_current_attempt_receipt(self, job_name: str, group: str) -> None:
        job = _jobs()[job_name]
        upload_steps = [
            step
            for step in job.get("steps", [])
            if "upload-artifact" in str(step.get("uses", ""))
        ]
        assert len(upload_steps) == 1, (
            f"{job_name}: expected exactly one upload-artifact step, found "
            f"{len(upload_steps)}"
        )
        (upload,) = upload_steps
        name = upload.get("with", {}).get("name", "")
        assert "github.run_attempt" in name, (
            f"{job_name}: receipt artifact name must key off "
            f"github.run_attempt so re-runs cannot collide with the prior "
            f"attempt's evidence, got {name!r}"
        )

    def test_shadow_job_not_gating(self, job_name: str, group: str) -> None:
        # `needs:` on the pre-existing required jobs must remain untouched;
        # a shadow job must not appear in another (legacy) job's `needs:`.
        jobs = _jobs()
        for legacy in ("lint", "test", "validate"):
            needs = jobs[legacy].get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            assert job_name not in needs, (
                f"legacy job {legacy!r} must not depend on shadow job "
                f"{job_name!r} — the shadow path must never gate the merge"
            )


class TestShadowAggregateJob:
    def test_runs_always(self) -> None:
        job = _jobs()[SHADOW_AGGREGATE_JOB]
        assert job.get("if") == "always()", (
            "aggregate job must run with `if: always()` so a failed/skipped "
            "producer is still evaluated rather than short-circuiting the job"
        )

    def test_needs_every_shadow_group_job(self) -> None:
        job = _jobs()[SHADOW_AGGREGATE_JOB]
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        missing = [name for name in SHADOW_GROUP_JOBS if name not in needs]
        assert not missing, f"aggregate job is missing needs: {missing}"

    def test_rejects_unsuccessful_upstream_conclusions(self) -> None:
        # The rejection must be an allow-list check (`!= "success"`), not a
        # deny-list of specific bad values — a deny-list silently treats an
        # unanticipated conclusion value as a pass.
        job = _jobs()[SHADOW_AGGREGATE_JOB]
        run_text = "\n".join(_run_texts(job))
        assert re.search(r'!=\s*["\']success["\']', run_text), (
            "aggregate job must explicitly reject any producer result that "
            "is not exactly 'success' (so skipped/cancelled/failed producers "
            "are all rejected, not just a hardcoded 'failure' case)"
        )

    def test_credentials_are_read_only(self) -> None:
        job = _jobs()[SHADOW_AGGREGATE_JOB]
        permissions = job.get("permissions")
        assert permissions is not None, "aggregate job must declare `permissions:`"
        for scope, level in permissions.items():
            assert level not in WRITE_PERMISSION_VALUES, (
                f"aggregate job permission {scope!r} must not grant write "
                f"access, got {level!r}"
            )

    def test_not_in_legacy_needs(self) -> None:
        jobs = _jobs()
        for legacy in ("lint", "test", "validate"):
            needs = jobs[legacy].get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            assert SHADOW_AGGREGATE_JOB not in needs, (
                f"legacy job {legacy!r} must not depend on the shadow "
                f"aggregate job — it must never gate the merge"
            )
