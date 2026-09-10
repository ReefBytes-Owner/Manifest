"""Shared registry/receipt/context builders for aggregate CI-evidence tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from manifest_agent.checks import receipt as _receipt
from manifest_agent.checks.registry import load_registry
from tests.python.manifest_agent.check_registry_fixtures import _check

REPOSITORY = "acme/example"
WORKFLOW = "ci.yml"
RUN_ID = "1001"
TESTED_SHA = "a" * 40
# Every fixture receipt agrees on this toolchain identity by default -- tests
# that need to exercise "mixed toolchain across groups" override it directly.
DEFAULT_TOOLCHAIN_DIGEST = _receipt.toolchain_digest({"demo": "f" * 64})

_PROFILES = {
    "quick": ["lint.a"],
    "full": ["lint.a", "test.a"],
    "security": ["lint.a"],
    "release": ["lint.a", "test.a"],
}


def write_two_group_registry(
    registry_file: Callable[..., Path], **overrides: object
) -> Path:
    """A minimal registry with one "full"-profile check per group (lint/test)."""
    lint_check = _check("lint.a", group="lint", category="lint", selection="changed")
    test_check = _check("test.a", group="test", selection="project")
    kwargs: dict[str, object] = {
        "checks": [lint_check, test_check],
        "profiles": _PROFILES,
    }
    kwargs.update(overrides)
    return registry_file(**kwargs)


def load_two_group_registry(registry_file: Callable[..., Path], **overrides: object):
    return load_registry(write_two_group_registry(registry_file, **overrides))


def result(check_id: str, status: str = "PASS") -> dict:
    return {
        "id": check_id,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_seconds": 0.1,
        "diagnostics": "",
        "selected_inputs": [],
    }


def receipt(
    *, group: str, results: list[dict], digest: str, **overrides: object
) -> dict:
    built = {
        "schema_version": 2,
        "profile": "full",
        "group": group,
        "partial": True,
        "candidate_digest": "candidate-digest",
        "source_digest": "source-digest",
        "head_sha": TESTED_SHA,
        "base_sha": "b" * 40,
        "tree_sha": "c" * 40,
        "config_digest": digest,
        "coverage_pending": [],
        "required_ids": [result["id"] for result in results],
        "results": results,
        "status": "PASS",
        "duration_seconds": 0.1,
        "toolchain_digest": DEFAULT_TOOLCHAIN_DIGEST,
        "interpreter_version": "",
        "interpreter_executable_sha256": "",
        "environment_digest": "e" * 64,
        "expires_at": None,
        "receipt_key": "k" * 64,
    }
    built.update(overrides)
    return built


def job(group: str, **overrides: object) -> dict:
    built = {
        "group": group,
        "job_id": f"job-{group}",
        "conclusion": "success",
        "artifact_id": f"artifact-{group}",
        "run_attempt": 1,
    }
    built.update(overrides)
    return built


def context(*, producer_jobs: list[dict], **overrides: object) -> dict:
    built = {
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "run_id": RUN_ID,
        "run_attempt": 1,
        "tested_sha": TESTED_SHA,
        "producer_jobs": producer_jobs,
    }
    built.update(overrides)
    return built


def clean_pair(digest: str) -> tuple[list[dict], dict]:
    """A trustworthy, matching (receipts, context) pair for the "full" profile."""
    receipts = [
        receipt(group="lint", results=[result("lint.a")], digest=digest),
        receipt(group="test", results=[result("test.a")], digest=digest),
    ]
    run_context = context(producer_jobs=[job("lint"), job("test")])
    return receipts, run_context
