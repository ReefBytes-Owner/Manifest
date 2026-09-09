"""A real `run_profile()` receipt must be accepted by `aggregate_results`.

`aggregate.RECEIPT_KEYS` is a hand-maintained mirror of the keys
`runner._report` actually emits; the two are easy to let drift (see Task 8
fix round 1). This exercises the real producer path end to end — a genuine
disposable Git candidate and a real subprocess check — instead of only the
hand-rolled receipts in `aggregate_fixtures.py`, so a future drift fails here
first.
"""

from __future__ import annotations

from manifest_agent.checks import run_profile
from manifest_agent.checks.aggregate import aggregate_results
from tests.python.manifest_agent.aggregate_fixtures import context, job
from tests.python.manifest_agent.test_check_candidate import source as source_fixture
from tests.python.manifest_agent.test_check_runner import candidate as candidate_fixture
from tests.python.manifest_agent.test_check_runner import (
    check_fixture as check_fixture_fixture,
)

source = source_fixture
candidate = candidate_fixture
check_fixture = check_fixture_fixture


def test_real_run_profile_receipt_is_accepted_by_aggregate_results(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")

    real_receipt = run_profile(registry, "full", "test", candidate, {})

    assert real_receipt["status"] == "PASS"  # sanity: the real check ran and passed
    run_context = context(
        producer_jobs=[job("test")], tested_sha=real_receipt["head_sha"]
    )

    report = aggregate_results(registry, "full", [real_receipt], run_context)

    assert report["status"] == "PASS", report["diagnostics"]
    assert report["diagnostics"] == []
