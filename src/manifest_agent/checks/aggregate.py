"""Validate per-group CI producer receipts and emit one aggregate verdict.

`aggregate_results` treats every receipt (a `manifest check --json` report a
CI producer job uploaded as an artifact) and the supplied `--context`
document as untrusted assertions, never as proof on their own. Nothing is
accepted as PASS unless: the union of receipts covers the exact set of checks
the profile requires (no missing, extra, or duplicate check ids); each
receipt's identity (`head_sha`, `tree_sha` via `config_digest`/group match)
lines up with the current run described by `context`; and each receipt's
declared producer job succeeded, in the current run attempt, with a real
artifact identity. Any mismatch becomes a BLOCKED diagnostic instead of being
silently dropped. Pending Phase 3 coverage recorded in the registry can never
be upgraded to PASS by aggregation — it still forces BLOCKED.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import CheckSpec
from .path_filters import has_path_filters
from .registry import VALID_GROUPS, applicable_pending, resolve_checks
from .runner import _config_digest

RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "profile",
        "group",
        "partial",
        "candidate_digest",
        "source_digest",
        "head_sha",
        "base_sha",
        "tree_sha",
        "config_digest",
        "coverage_pending",
        "required_ids",
        "results",
        "status",
        "duration_seconds",
    }
)
# Receipt-local, non-authoritative fields: `run_profile` reports them for its
# own candidate/local view, but the aggregator independently recomputes
# `config_digest` from the loaded registry and coverage from the registry's
# `coverage_pending` + the resolved profile, so these are only type-checked,
# never trusted as the source of truth for the aggregate verdict.
RECEIPT_LOCAL_KEYS = frozenset(
    {"candidate_digest", "coverage_pending", "required_ids", "duration_seconds"}
)
RESULT_KEYS = frozenset(
    {"id", "status", "returncode", "duration_seconds", "diagnostics", "selected_inputs"}
)
RESULT_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "NOT_APPLICABLE"})
RECEIPT_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED"})
CONTEXT_KEYS = frozenset(
    {"repository", "workflow", "run_id", "run_attempt", "tested_sha", "producer_jobs"}
)
JOB_KEYS = frozenset({"group", "job_id", "conclusion", "artifact_id", "run_attempt"})


@dataclass(frozen=True)
class _Trust:
    """Everything a receipt is checked against, gathered once per call."""

    profile: str
    expected_groups: frozenset[str]
    confirmed_job_groups: frozenset[str]
    expected_config_digest: str
    tested_sha: str | None
    id_to_group: dict[str, str]
    id_to_spec: dict[str, CheckSpec]


def _expected(
    registry: dict[str, Any], profile: str
) -> tuple[tuple[CheckSpec, ...], dict[str, str]]:
    checks = resolve_checks(registry, profile, None)
    return checks, {check.id: check.group for check in checks}


def _not_applicable_allowed(check: CheckSpec) -> bool:
    """Whole-project checks can never be NOT_APPLICABLE; only a check whose
    selection is scoped to changed files (directly, or via project-selection
    path filters) can legitimately match zero inputs."""
    return check.selection == "changed" or has_path_filters(check)


def _context_errors(context: Any) -> list[str]:
    if not isinstance(context, dict):
        return ["context must be a JSON object"]
    unknown = set(context) - CONTEXT_KEYS
    missing = CONTEXT_KEYS - set(context)
    if unknown or missing:
        return [
            "context has unexpected or missing fields: "
            f"unexpected={sorted(unknown)} missing={sorted(missing)}"
        ]
    errors = []
    for key in ("repository", "workflow", "run_id", "tested_sha"):
        if not isinstance(context[key], str) or not context[key]:
            errors.append(f"context.{key} must be a non-empty string")
    if not isinstance(context["run_attempt"], int) or isinstance(
        context["run_attempt"], bool
    ):
        errors.append("context.run_attempt must be an integer")
    if not isinstance(context["producer_jobs"], list):
        errors.append("context.producer_jobs must be a list")
    return errors


def _job_errors(
    job: Any, run_attempt: int, expected_groups: frozenset[str]
) -> tuple[list[str], str | None]:
    if not isinstance(job, dict):
        return ["producer job entry must be a JSON object"], None
    unknown = set(job) - JOB_KEYS
    missing = JOB_KEYS - set(job)
    if unknown or missing:
        return [
            "producer job entry has unexpected or missing fields: "
            f"unexpected={sorted(unknown)} missing={sorted(missing)}"
        ], None
    group = job["group"]
    errors = []
    if group not in expected_groups:
        errors.append(f"producer job references unexpected group: {group!r}")
    if not isinstance(job["job_id"], str) or not job["job_id"]:
        errors.append(f"producer job for group {group!r} is missing a job id")
    if not isinstance(job["artifact_id"], str) or not job["artifact_id"]:
        errors.append(
            f"producer job for group {group!r} is missing an artifact identity"
        )
    if job["conclusion"] != "success":
        errors.append(
            f"producer job for group {group!r} did not succeed: "
            f"conclusion={job['conclusion']!r}"
        )
    if job["run_attempt"] != run_attempt:
        errors.append(f"producer job for group {group!r} is from a stale run attempt")
    return errors, group


def _producer_groups(
    context: dict[str, Any], expected_groups: frozenset[str]
) -> tuple[list[str], frozenset[str]]:
    errors: list[str] = []
    seen: set[str] = set()
    confirmed: set[str] = set()
    for job in context["producer_jobs"]:
        job_errors, group = _job_errors(job, context["run_attempt"], expected_groups)
        errors.extend(job_errors)
        if group is None:
            continue
        if group in seen:
            errors.append(f"duplicate producer job for group: {group!r}")
        seen.add(group)
        if not job_errors:
            confirmed.add(group)
    missing = expected_groups - seen
    if missing:
        errors.append(f"missing producer job for groups: {sorted(missing)}")
    return errors, frozenset(confirmed)


def _receipt_type_errors(receipt: Any) -> list[str]:
    if not isinstance(receipt, dict):
        return ["receipt must be a JSON object"]
    unknown = set(receipt) - RECEIPT_KEYS
    missing = RECEIPT_KEYS - set(receipt)
    if unknown or missing:
        return [
            "receipt has unexpected or missing fields: "
            f"unexpected={sorted(unknown)} missing={sorted(missing)}"
        ]
    return []


def _receipt_identity_errors(receipt: dict[str, Any], trust: _Trust) -> list[str]:
    errors = []
    if receipt["schema_version"] != 1:
        errors.append("receipt schema_version must be 1")
    if receipt["profile"] != trust.profile:
        errors.append(
            f"receipt profile {receipt['profile']!r} does not match {trust.profile!r}"
        )
    if receipt["group"] not in VALID_GROUPS:
        errors.append(f"receipt group {receipt['group']!r} is not a known group")
    if receipt["partial"] is not True:
        errors.append("receipt must be a partial (single-group) result")
    if receipt["status"] not in RECEIPT_STATUSES:
        errors.append(f"receipt status {receipt['status']!r} is not recognized")
    if receipt["config_digest"] != trust.expected_config_digest:
        errors.append("receipt config_digest does not match the loaded registry")
    if trust.tested_sha is None:
        errors.append("receipt cannot be verified without a trustworthy context")
    elif receipt["head_sha"] != trust.tested_sha:
        errors.append("receipt head_sha does not match the current run's tested_sha")
    errors.extend(_receipt_local_field_errors(receipt))
    return errors


def _receipt_local_field_errors(receipt: dict[str, Any]) -> list[str]:
    """Type-check receipt-local fields; never used as the verdict's authority."""
    errors = []
    if not isinstance(receipt["candidate_digest"], str):
        errors.append("receipt candidate_digest must be a string")
    if not isinstance(receipt["coverage_pending"], list):
        errors.append("receipt coverage_pending must be a list")
    if not isinstance(receipt["required_ids"], list):
        errors.append("receipt required_ids must be a list")
    duration = receipt["duration_seconds"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        errors.append("receipt duration_seconds must be a number")
    return errors


def _result_errors(
    result: Any, group: str, trust: _Trust
) -> tuple[list[str], dict[str, Any] | None]:
    if not isinstance(result, dict):
        return ["result entry must be a JSON object"], None
    unknown = set(result) - RESULT_KEYS
    missing = RESULT_KEYS - set(result)
    if unknown or missing:
        return [
            "result entry has unexpected or missing fields: "
            f"unexpected={sorted(unknown)} missing={sorted(missing)}"
        ], None
    check_id = result["id"]
    errors = []
    if not isinstance(check_id, str) or not check_id:
        errors.append("result id must be a non-empty string")
    if result["status"] not in RESULT_STATUSES:
        errors.append(
            f"result {check_id!r} has unrecognized status {result['status']!r}"
        )
    owner_group = trust.id_to_group.get(check_id)
    if owner_group is None:
        errors.append(f"result {check_id!r} is not a check required by this profile")
    elif owner_group != group:
        errors.append(f"result {check_id!r} does not belong to receipt group {group!r}")
    spec = trust.id_to_spec.get(check_id)
    if (
        result["status"] == "NOT_APPLICABLE"
        and spec is not None
        and not _not_applicable_allowed(spec)
    ):
        errors.append(
            f"result {check_id!r} is a whole-project check and cannot be NOT_APPLICABLE"
        )
    return errors, (None if errors else result)


def _receipt_results(
    receipt: dict[str, Any], group: str, trust: _Trust
) -> tuple[list[str], list[dict[str, Any]]]:
    if not isinstance(receipt["results"], list):
        return ["receipt results must be a list"], []
    errors: list[str] = []
    valid: list[dict[str, Any]] = []
    for result in receipt["results"]:
        result_errors, parsed = _result_errors(result, group, trust)
        errors.extend(result_errors)
        if parsed is not None:
            valid.append(parsed)
    return errors, valid


def _receipt_errors(
    receipt: Any, trust: _Trust
) -> tuple[list[str], dict[str, Any] | None]:
    type_errors = _receipt_type_errors(receipt)
    if type_errors:
        return type_errors, None
    errors = _receipt_identity_errors(receipt, trust)
    group = receipt["group"]
    if group not in trust.expected_groups:
        errors.append(
            f"receipt group {group!r} is not required by profile {trust.profile!r}"
        )
    elif group not in trust.confirmed_job_groups:
        errors.append(f"receipt group {group!r} has no confirmed producer job")
    result_errors, results = _receipt_results(receipt, group, trust)
    errors.extend(result_errors)
    if errors:
        return errors, None
    return [], {"group": group, "results": results}


def _merge_receipts(
    receipts: list[Any], trust: _Trust
) -> tuple[list[str], list[dict[str, Any]], set[str]]:
    errors: list[str] = []
    merged: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    seen_ids: set[str] = set()
    for index, receipt in enumerate(receipts):
        receipt_errors, parsed = _receipt_errors(receipt, trust)
        errors.extend(f"receipt[{index}]: {message}" for message in receipt_errors)
        if parsed is None:
            continue
        group = parsed["group"]
        if group in seen_groups:
            errors.append(f"receipt[{index}]: duplicate receipt for group {group!r}")
            continue
        seen_groups.add(group)
        for result in parsed["results"]:
            check_id = result["id"]
            if check_id in seen_ids:
                errors.append(f"duplicate check result: {check_id!r}")
                continue
            seen_ids.add(check_id)
            merged.append(result)
    missing_ids = set(trust.id_to_group) - seen_ids
    if missing_ids:
        errors.append(f"missing check results: {sorted(missing_ids)}")
    return errors, merged, seen_groups


def _final_status(
    statuses: set[str], diagnostics: list[str], pending: list[str]
) -> str:
    if "FAIL" in statuses:
        return "FAIL"
    if "BLOCKED" in statuses or diagnostics or pending:
        return "BLOCKED"
    return "PASS"


def aggregate_results(
    registry: dict[str, Any],
    profile: str,
    receipts: list[Any],
    context: Any,
) -> dict[str, Any]:
    """Cross-check CI producer receipts against `context` and the registry.

    `receipts` are per-group `manifest check --json` reports collected from
    CI producer job artifacts; `context` is the current run's identity plus
    each expected producer job's authoritative conclusion and artifact
    identity. Both are untrusted input and are validated structurally and
    cross-referentially before any result contributes to the verdict.
    """
    full_checks, id_to_group = _expected(registry, profile)
    expected_groups = frozenset(id_to_group.values())
    id_to_spec = {check.id: check for check in full_checks}

    diagnostics = _context_errors(context)
    confirmed_job_groups: frozenset[str] = frozenset()
    tested_sha: str | None = None
    if not diagnostics:
        tested_sha = context["tested_sha"]
        job_errors, confirmed_job_groups = _producer_groups(context, expected_groups)
        diagnostics.extend(job_errors)

    trust = _Trust(
        profile=profile,
        expected_groups=expected_groups,
        confirmed_job_groups=confirmed_job_groups,
        expected_config_digest=_config_digest(registry),
        tested_sha=tested_sha,
        id_to_group=id_to_group,
        id_to_spec=id_to_spec,
    )
    receipt_errors, results, received_groups = _merge_receipts(receipts, trust)
    diagnostics.extend(receipt_errors)

    pending = applicable_pending(registry, profile, None, full_checks)
    statuses = {result["status"] for result in results}
    return {
        "schema_version": 1,
        "profile": profile,
        "aggregate": True,
        "tested_sha": tested_sha,
        "expected_groups": sorted(expected_groups),
        "received_groups": sorted(received_groups),
        "required_ids": sorted(id_to_group),
        "coverage_pending": pending,
        "results": sorted(results, key=lambda item: item["id"]),
        "diagnostics": diagnostics,
        "status": _final_status(statuses, diagnostics, pending),
    }
