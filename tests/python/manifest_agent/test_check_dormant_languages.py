"""Dormant-language checks (C2) must report NOT_APPLICABLE, never PASS.

No `.tf`/`.go`/Rust source exists anywhere in this repository (verified by
`find` before this chunk landed). golangci-lint, pre-commit-terraform and
cargo checks stay registered and excluded from `config/toolchain.lock.json`
so an unattested tool never has to PASS -- but a check that "passes"
because it had nothing to look at is a false green, so the expected
outcome is NOT_APPLICABLE with zero-input evidence, not PASS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manifest_agent.checks import execute_check
from manifest_agent.checks.registry import load_registry
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "project-checks.json"
DORMANT_CHECK_IDS = (
    "hook.golangci-lint",
    "hook.terraform_fmt",
    "hook.terraform_validate",
    "hook.terraform_tflint",
    "hook.terraform_trivy",
    "hook.cargo-fmt-check",
    "hook.cargo-clippy",
)


@pytest.fixture
def candidate(source, tmp_path):
    return materialize(source, tmp_path)


@pytest.mark.parametrize("check_id", DORMANT_CHECK_IDS)
def test_dormant_language_check_is_not_applicable_with_zero_input_evidence(
    check_id, candidate
):
    registry = load_registry(REGISTRY_PATH)
    check = next(check for check in registry["checks"] if check.id == check_id)
    # The fixture candidate (test_check_candidate.source) has no `.tf`/`.go`/
    # `.rs` files, matching the real repository's own state.
    result = execute_check(check, candidate, {})
    assert result.status == "NOT_APPLICABLE"
    assert result.status != "PASS"
    assert "zero applicable" in result.diagnostics
    assert result.selected_inputs == ()


def test_dormant_checks_are_excluded_from_the_toolchain_lock():
    """If a `.tf`/`.go`/Rust file ever lands, the check must go BLOCKED
    (tool not provisioned) rather than silently resolving against whatever
    happens to be on PATH -- so these tools are deliberately absent from
    the lock (phase-3-5-decisions.md 3b)."""
    import json

    lock_path = REGISTRY_PATH.parent / "toolchain.lock.json"
    lock = json.loads(lock_path.read_text())
    dormant_tool_names = {"golangci-lint", "terraform", "tflint", "trivy", "cargo"}
    assert not dormant_tool_names & lock["tools"].keys()
