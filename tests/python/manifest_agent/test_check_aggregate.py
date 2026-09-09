"""`aggregate_results` contracts: validating CI producer receipts against a
current-run context and emitting one aggregate verdict."""

from __future__ import annotations

import pytest

from manifest_agent.checks.aggregate import aggregate_results
from manifest_agent.checks.runner import _config_digest
from tests.python.manifest_agent.aggregate_fixtures import (
    TESTED_SHA,
    clean_pair,
    context,
    job,
    load_two_group_registry,
    receipt,
    result,
)
from tests.python.manifest_agent.check_registry_fixtures import (
    _registry_file as _registry_file,
)


@pytest.fixture
def registry(registry_file):
    return load_two_group_registry(registry_file)


@pytest.fixture
def digest(registry):
    return _config_digest(registry)


def test_matching_receipts_pass(registry, digest):
    receipts, run_context = clean_pair(digest)

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "PASS"
    assert report["received_groups"] == ["lint", "test"]
    assert sorted(r["id"] for r in report["results"]) == ["lint.a", "test.a"]
    assert report["diagnostics"] == []


def test_missing_check_id_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    receipts[1]["results"] = []

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("missing check results" in d for d in report["diagnostics"])


def test_extra_check_id_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    receipts[0]["results"].append(result("lint.unexpected"))

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any(
        "not a check required by this profile" in d for d in report["diagnostics"]
    )


def test_duplicate_check_id_across_receipts_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    receipts.append(receipt(group="lint", results=[result("lint.a")], digest=digest))
    run_context["producer_jobs"].append(job("lint", job_id="job-lint-2"))

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("duplicate receipt for group" in d for d in report["diagnostics"])


def test_wrong_group_receipt_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    # lint.a is reported inside the "test" receipt: the id belongs to "lint".
    receipts[1]["results"] = [result("lint.a")]

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("does not belong to receipt group" in d for d in report["diagnostics"])


def test_old_run_attempt_producer_job_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    run_context["run_attempt"] = 2

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("stale run attempt" in d for d in report["diagnostics"])


def test_wrong_head_sha_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    receipts[0]["head_sha"] = "f" * 40

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any(
        "does not match the current run's tested_sha" in d
        for d in report["diagnostics"]
    )


def test_wrong_config_digest_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    receipts[0]["config_digest"] = "0" * 64

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("config_digest does not match" in d for d in report["diagnostics"])


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "failure"])
def test_non_success_producer_job_is_rejected(registry, digest, conclusion):
    receipts, run_context = clean_pair(digest)
    run_context["producer_jobs"] = [job("lint", conclusion=conclusion), job("test")]

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("did not succeed" in d for d in report["diagnostics"])


def test_missing_artifact_identity_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    run_context["producer_jobs"][0]["artifact_id"] = ""

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("missing an artifact identity" in d for d in report["diagnostics"])


def test_unknown_context_field_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    run_context["extra"] = "surprise"

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("unexpected or missing fields" in d for d in report["diagnostics"])


def test_unknown_receipt_field_is_rejected(registry, digest):
    receipts, run_context = clean_pair(digest)
    receipts[0]["extra"] = "surprise"

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("receipt[0]" in d and "unexpected" in d for d in report["diagnostics"])


def test_pending_coverage_never_upgrades_to_pass(registry_file, digest):
    registry = load_two_group_registry(
        registry_file,
        coverage_pending={
            "quick": [],
            "full": ["test.a: whole-project coverage"],
            "security": [],
            "release": [],
        },
    )
    receipts, run_context = clean_pair(_config_digest(registry))

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert report["coverage_pending"] == ["test.a: whole-project coverage"]


def test_fail_result_wins_over_coexisting_blocked_diagnostic(registry, digest):
    receipts, run_context = clean_pair(digest)
    receipts[0]["results"] = [result("lint.a", status="FAIL")]
    receipts[0]["status"] = "FAIL"
    # A second, independent trust violation for the *other* group.
    receipts[1]["head_sha"] = "f" * 40

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "FAIL"
    assert any(
        "does not match the current run's tested_sha" in d
        for d in report["diagnostics"]
    )
    assert any(r["id"] == "lint.a" and r["status"] == "FAIL" for r in report["results"])


def test_non_dict_context_is_rejected(registry, digest):
    receipts, _ = clean_pair(digest)

    report = aggregate_results(registry, "full", receipts, ["not", "a", "dict"])

    assert report["status"] == "BLOCKED"
    assert "context must be a JSON object" in report["diagnostics"]


def test_non_dict_receipt_is_rejected(registry, digest):
    _, run_context = clean_pair(digest)

    report = aggregate_results(registry, "full", ["not a dict"], run_context)

    assert report["status"] == "BLOCKED"
    assert any(
        "receipt[0]: receipt must be a JSON object" in d for d in report["diagnostics"]
    )


def test_receipt_head_sha_must_match_tested_sha_not_just_be_consistent(
    registry, digest
):
    """Both receipts agreeing with each other is not enough without `context`."""
    shared_wrong_sha = "9" * 40
    receipts = [
        receipt(
            group="lint",
            results=[result("lint.a")],
            digest=digest,
            head_sha=shared_wrong_sha,
        ),
        receipt(
            group="test",
            results=[result("test.a")],
            digest=digest,
            head_sha=shared_wrong_sha,
        ),
    ]
    run_context = context(
        producer_jobs=[job("lint"), job("test")], tested_sha=TESTED_SHA
    )

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert (
        sum(
            "does not match the current run's tested_sha" in d
            for d in report["diagnostics"]
        )
        == 2
    )


def test_not_applicable_on_changed_selection_check_aggregates_cleanly(registry, digest):
    """`lint.a` is a `changed`-selection check: NOT_APPLICABLE is legitimate
    when its selector matched zero inputs, and must not block the verdict."""
    receipts, run_context = clean_pair(digest)
    receipts[0]["results"] = [result("lint.a", status="NOT_APPLICABLE")]
    receipts[0]["results"][0]["returncode"] = None

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "PASS"
    assert report["diagnostics"] == []


def test_not_applicable_on_project_selection_check_is_blocked(registry, digest):
    """`test.a` is a whole-project (`project`-selection, no path filters)
    check: it can never be legitimately NOT_APPLICABLE, so a receipt
    reporting it that way must BLOCK the aggregate verdict, not pass
    through silently."""
    receipts, run_context = clean_pair(digest)
    receipts[1]["results"] = [result("test.a", status="NOT_APPLICABLE")]
    receipts[1]["results"][0]["returncode"] = None

    report = aggregate_results(registry, "full", receipts, run_context)

    assert report["status"] == "BLOCKED"
    assert any("test.a" in d and "NOT_APPLICABLE" in d for d in report["diagnostics"])
