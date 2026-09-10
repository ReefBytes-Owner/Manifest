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

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import click
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW_PATH = ROOT / ".github/workflows/ci.yml"

SHADOW_GROUP_JOBS = {
    "shadow-checks-structure": "structure",
    "shadow-checks-lint": "lint",
    "shadow-checks-test": "test",
    "shadow-checks-security": "security",
    "shadow-checks-package": "package",
}
SHADOW_AGGREGATE_JOB = "shadow-checks-aggregate"
ZERO_SHA = "0000000000000000000000000000000000000000"

WRITE_PERMISSION_VALUES = {"write"}


def _load_workflow() -> dict[str, Any]:
    with CI_WORKFLOW_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _jobs() -> dict[str, Any]:
    return _load_workflow()["jobs"]


def _run_texts(job: dict[str, Any]) -> list[str]:
    return [step["run"] for step in job.get("steps", []) if "run" in step]


def _manifest_check_lines(job: dict[str, Any]) -> list[str]:
    # The shared-check invocation now lives inside a multi-line script (it
    # also captures the exit code and writes a receipt-presence output), so
    # it must be located line-by-line rather than assuming a step's whole
    # `run:` block is nothing but the command.
    return [
        line.strip()
        for run in _run_texts(job)
        for line in run.splitlines()
        if re.search(r"\bmanifest\s+check\b", line) and not line.strip().startswith("#")
    ]


def _step_with_run_matching(job: dict[str, Any], pattern: str) -> dict[str, Any]:
    (step,) = [
        step for step in job.get("steps", []) if re.search(pattern, step.get("run", ""))
    ]
    return step


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
        check_lines = _manifest_check_lines(job)
        assert len(check_lines) == 1, (
            f"{job_name}: expected exactly one line invoking the shared "
            f"`manifest check` command, found {len(check_lines)}: {check_lines}"
        )
        (command,) = check_lines
        assert command.startswith("uv run manifest check "), (
            f"{job_name}: shared-check step must invoke `uv run manifest "
            f"check ...` verbatim, not a drifted variant: {command!r}"
        )
        assert f"--group {group}" in command, (
            f"{job_name}: shared-check step must select group {group!r}: {command!r}"
        )
        # `--project-config` and `--base` are both `required=True` on the
        # `manifest check` CLI (src/manifest_agent/checks/cli.py) -- a
        # command missing either one exits 2 (UsageError) before running a
        # single check, so the shadow producer never writes a receipt.
        # Verify each flag is present and followed by a real, non-empty
        # value token (not just the bare substring somewhere in the line).
        assert re.search(r"--project-config\s+\S+", command), (
            f"{job_name}: shared-check step must pass a non-empty "
            f"--project-config, or the CLI exits 2 before running any "
            f"check: {command!r}"
        )
        assert re.search(r"--base\s+\S+", command), (
            f"{job_name}: shared-check step must pass a non-empty --base, "
            f"or the CLI raises UsageError before running any check: "
            f"{command!r}"
        )

    def test_command_parses_with_the_real_cli(self, job_name: str, group: str) -> None:
        # The strongest available check without a network call: feed the
        # extracted argv into the real Click command's argument parser
        # (`make_context`) and confirm it does not raise `UsageError` (e.g.
        # missing `--project-config`/`--base`). This deliberately stops at
        # parsing -- it never invokes the command callback, so it exercises
        # no check body, performs no candidate materialization, and writes
        # no files -- while still proving today's command (missing both
        # required options) fails this exact assertion.
        from manifest_agent.checks.cli import check as check_command

        job = _jobs()[job_name]
        (command,) = _manifest_check_lines(job)
        assert command.startswith("uv run manifest check ")
        argv = command[len("uv run manifest check ") :].split()
        # BASE_SHA is populated at runtime from a prior step's output via
        # `env:`, not interpolated into the script body; substitute a
        # syntactically valid placeholder revision for this parse-only check.
        argv = ["HEAD" if token == '"${BASE_SHA}"' else token for token in argv]

        try:
            check_command.make_context("check", list(argv), resilient_parsing=False)
        except click.UsageError as error:
            pytest.fail(
                f"{job_name}: extracted invocation does not parse against "
                f"the real CLI: {argv!r}\n{error.format_message()}"
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

    def test_job_level_continue_on_error(self, job_name: str, group: str) -> None:
        # JOB-level, not just step-level: a step-only continue-on-error
        # still lets an unrelated step (checkout, uv install, upload)
        # redden the whole job and thus the workflow conclusion.
        job = _jobs()[job_name]
        assert job.get("continue-on-error") is True, (
            f"{job_name}: must set job-level `continue-on-error: true` so "
            f"the shadow path can never turn the overall workflow red"
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

    def test_rejects_producers_with_no_receipt_evidence(self) -> None:
        # The rejection must be an allow-list check (`!= "true"`), not a
        # deny-list of specific bad values — a deny-list silently treats an
        # unanticipated value as a pass. This asserts against the
        # `receipt_written` signal, not `steps.shadow.outcome`: `outcome` is
        # "failure" both when a producer crashed with no receipt AND when it
        # ran cleanly and reported FAIL/BLOCKED with one, so gating on it
        # rejected every real run once any group returned FAIL/BLOCKED (see
        # docs/SHARED_CHECKS.md). The old outcome-based assertion is
        # deliberately gone, not just relaxed.
        job = _jobs()[SHADOW_AGGREGATE_JOB]
        run_text = "\n".join(_run_texts(job))
        assert not re.search(r'!=\s*["\']success["\']', run_text), (
            "aggregate job must no longer gate on `steps.shadow.outcome == "
            "'success'` -- that rejects every real run once any group "
            "returns FAIL/BLOCKED, which is the defect this chunk fixes"
        )
        assert re.search(r'!=\s*["\']true["\']', run_text), (
            "aggregate job must explicitly reject any producer whose "
            "receipt-written evidence is not exactly 'true' (so a skipped, "
            "cancelled, or crashed-with-no-receipt producer is rejected, "
            "not just a hardcoded case)"
        )
        for env_value in re.findall(r"needs\.[\w-]+\.outputs\.(\w+)", run_text):
            assert env_value == "shadow_receipt_written", (
                f"aggregate rejection step reads producer output {env_value!r}; "
                "expected shadow_receipt_written for every producer"
            )

    def test_rejection_step_is_not_step_level_continue_on_error(self) -> None:
        # The rejection text alone proves nothing if the step that runs it
        # is itself `continue-on-error: true` at STEP level — a failing
        # `sys.exit(1)` would then be swallowed before it can short-circuit
        # the remaining steps (context build, receipt download, aggregate).
        # Job-level continue-on-error (tested separately) is what keeps the
        # overall workflow green; this step must still surface its own
        # failure so later steps in the same job do not run on bad evidence.
        job = _jobs()[SHADOW_AGGREGATE_JOB]
        reject_step = _step_with_run_matching(job, r'!=\s*["\']true["\']')
        assert reject_step.get("continue-on-error") is not True, (
            "the producer-rejection step must not itself be "
            "`continue-on-error: true` at step level, or its failure would "
            "never short-circuit the remaining aggregate steps"
        )

    def test_downloads_receipts_for_every_shadow_group(self) -> None:
        job = _jobs()[SHADOW_AGGREGATE_JOB]
        download_step = _step_with_run_matching(job, r"gh run download")
        run_text = download_step["run"]
        for group in SHADOW_GROUP_JOBS.values():
            assert group in run_text, (
                f"aggregate job's receipt-download step does not mention "
                f"group {group!r}: {run_text!r}"
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

    def test_job_level_continue_on_error(self) -> None:
        job = _jobs()[SHADOW_AGGREGATE_JOB]
        assert job.get("continue-on-error") is True, (
            "aggregate job must set job-level `continue-on-error: true` so "
            "the shadow path can never turn the overall workflow red"
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


@pytest.mark.parametrize("job_name", sorted(SHADOW_GROUP_JOBS))
class TestPushBaseResolution:
    # Defect 1: `push` triggers only on `branches: [main]`, and after a
    # `fetch-depth: 0` checkout `origin/main == HEAD` once the push has
    # landed, so `git merge-base HEAD origin/main` degenerated to HEAD
    # itself -- every path-filtered check then diffed HEAD against HEAD and
    # read NOT_APPLICABLE. These assert the base-resolution step no longer
    # contains that pattern and explicitly branches on `github.event.before`,
    # including the all-zeros fallback GitHub sends for a new/force-pushed ref.
    def test_resolve_step_does_not_diff_against_a_degenerate_merge_base(
        self, job_name: str
    ) -> None:
        job = _jobs()[job_name]
        base_step = _step_with_run_matching(job, r"sha=")
        run_text = base_step["run"]
        assert not re.search(r'git merge-base HEAD "origin/', run_text), (
            f"{job_name}: base-resolution step still computes a merge-base "
            f"against origin/<default>, which is HEAD itself on a push "
            f"event after a fetch-depth: 0 checkout -- the degenerate base "
            f"this chunk fixes"
        )

    def test_resolve_step_branches_on_event_before(self, job_name: str) -> None:
        job = _jobs()[job_name]
        base_step = _step_with_run_matching(job, r"sha=")
        env = base_step.get("env", {})
        assert env.get("EVENT_BEFORE") == "${{ github.event.before }}", (
            f"{job_name}: base-resolution step must read github.event.before "
            f"via env:, got {env!r}"
        )
        run_text = base_step["run"]
        assert ZERO_SHA in run_text, (
            f"{job_name}: base-resolution step must explicitly handle the "
            f"all-zeros SHA GitHub sends for a new/force-pushed ref, "
            f"expected literal {ZERO_SHA!r} in the script"
        )

    def test_resolved_base_is_not_head_for_a_normal_push(self, job_name: str) -> None:
        # Execute the extracted script for real (no network, no checkout --
        # only the branch logic) with env simulating an ordinary push whose
        # `before` differs from HEAD, and confirm the resolved `sha=` output
        # is that `before` value, not something that reduces to HEAD.
        job = _jobs()[job_name]
        base_step = _step_with_run_matching(job, r"sha=")
        script = base_step["run"]
        before_sha = "cafef00d" * 5
        result = _run_bash_step(
            script,
            env={
                "EVENT_NAME": "push",
                "EVENT_BEFORE": before_sha,
                "PR_BASE_SHA": "",
            },
        )
        assert result["sha"] == before_sha, (
            f"{job_name}: expected resolved base {before_sha!r}, got "
            f"{result.get('sha')!r} (output: {result})"
        )

    def test_resolved_base_falls_back_on_zero_sha(self, job_name: str) -> None:
        job = _jobs()[job_name]
        base_step = _step_with_run_matching(job, r"sha=")
        script = base_step["run"]
        result = _run_bash_step(
            script,
            env={"EVENT_NAME": "push", "EVENT_BEFORE": ZERO_SHA, "PR_BASE_SHA": ""},
        )
        # This repo checkout has a real parent commit, so the fallback must
        # resolve to HEAD~1, never the all-zeros literal or HEAD itself.
        assert result["sha"] not in (ZERO_SHA, ""), (
            f"{job_name}: all-zeros before must fall back to a real "
            f"revision, got {result}"
        )


def _run_bash_step(script: str, env: dict[str, str]) -> dict[str, str]:
    """Execute an extracted workflow `run:` script for real and parse its
    `$GITHUB_OUTPUT` writes into a dict. No network; runs in this checkout."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as output_file:
        output_path = output_file.name
    try:
        full_env = {**os.environ, **env, "GITHUB_OUTPUT": output_path}
        subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env=full_env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        parsed: dict[str, str] = {}
        with open(output_path, encoding="utf-8") as handle:
            for line in handle:
                if "=" in line:
                    key, _, value = line.strip().partition("=")
                    parsed[key] = value
        return parsed
    finally:
        Path(output_path).unlink(missing_ok=True)


class TestCiContextAllowlistCoversNewGroups:
    # tools/project_checks/ci_context_cli.py recognizes shadow-group jobs by
    # an EXACT-match allowlist regex, deliberately not a loose suffix search
    # (see that module's docstring). The new security/package jobs must
    # match it precisely, and the aggregate job's own name must still not.
    def test_security_and_package_job_names_match_the_allowlist(self) -> None:
        from tools.project_checks.ci_context_cli import _GROUP_JOB_NAME

        jobs = _jobs()
        for job_name, group in (
            ("shadow-checks-security", "security"),
            ("shadow-checks-package", "package"),
        ):
            declared_name = jobs[job_name]["name"]
            match = _GROUP_JOB_NAME.match(declared_name)
            assert match is not None, (
                f"{job_name}: declared name {declared_name!r} does not "
                f"match the ci_context_cli.py allowlist"
            )
            assert match.group(1) == group

    def test_aggregate_job_name_still_does_not_match(self) -> None:
        from tools.project_checks.ci_context_cli import _GROUP_JOB_NAME

        declared_name = _jobs()[SHADOW_AGGREGATE_JOB]["name"]
        assert _GROUP_JOB_NAME.match(declared_name) is None, (
            f"the aggregate job's own name {declared_name!r} must never "
            f"match the shadow-group allowlist"
        )
