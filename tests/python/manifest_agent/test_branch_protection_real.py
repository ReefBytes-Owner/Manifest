"""`manifest branch-protection` against the real repository: the committed
config validates against its schema, every required job id resolves against
the real `.github/workflows/ci.yml`, today's precondition state is reported
honestly, and the "PUT" literal stays confined to `apply_protection`.

No subprocess or fake `gh` here -- these exercise `protection.py` directly
against real, committed files (the sibling `test_branch_protection.py`
covers the subprocess CLI path against a fake `gh`).
"""

from __future__ import annotations

import json
from pathlib import Path

from manifest_agent import protection

REPO_ROOT_MARKER = Path(__file__).resolve().parents[3]


def test_committed_config_validates_against_schema() -> None:
    """`config/branch-protection.json` conforms to its own schema."""
    import jsonschema

    schema = json.loads(
        (REPO_ROOT_MARKER / "schemas" / "branch-protection.schema.json").read_text(
            encoding="utf-8"
        )
    )
    instance = json.loads(
        (REPO_ROOT_MARKER / "config" / "branch-protection.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(instance, schema)


def test_committed_config_jobs_exist_and_resolve_to_real_ci_names() -> None:
    """Every required job id exists in the real workflow, and the resolved
    contexts are exactly the four expected job display names -- read from
    `ci.yml` at test time, not hardcoded, so a workflow rename fails this
    test instead of silently drifting."""
    config = protection.load_config(
        REPO_ROOT_MARKER / "config" / "branch-protection.json"
    )
    workflow_path = REPO_ROOT_MARKER / config["workflow"]
    workflow_jobs = protection.parse_workflow(workflow_path)
    for job_id in config["required_status_checks"]["jobs"]:
        assert job_id in workflow_jobs, job_id

    contexts = set(protection.resolve_contexts(config, workflow_jobs))
    aggregate_name = workflow_jobs[protection.aggregate_job_id(config)].name
    assert contexts == {"Lint & Validate", "Test", "Validate Structure", aggregate_name}


def test_real_workflow_precondition_honestly_reflects_todays_state() -> None:
    """Preconditions against the real workflow have the expected structure
    regardless of outcome; as of C6b the aggregate still carries
    `continue-on-error: true`, so today it must be reported unmet with that
    reason -- but this tolerates the fix landing later (C12) without
    breaking the suite."""
    config = protection.load_config(
        REPO_ROOT_MARKER / "config" / "branch-protection.json"
    )
    workflow_path = REPO_ROOT_MARKER / config["workflow"]
    workflow_jobs = protection.parse_workflow(workflow_path)
    preconditions = protection.check_preconditions(config, workflow_jobs)
    assert len(preconditions) == 2

    agg_id = protection.aggregate_job_id(config)
    aggregate_precondition = next(p for p in preconditions if p.name.startswith(agg_id))
    if not aggregate_precondition.met:
        assert "continue-on-error" in aggregate_precondition.reason
    else:
        assert aggregate_precondition.reason == ""


def test_policy_codeowners_are_single_owner_without_placeholders() -> None:
    """Every Phase 5 policy surface resolves to the confirmed owner."""
    expected = {
        "/config/",
        "/schemas/",
        "/src/manifest_agent/checks/",
        "/src/manifest_agent/hooks/",
        "/tools/project_checks/",
        "/tools/*.py",
        "/configs/claude/scripts/constitution/",
        "/configs/claude/config/constitution_baseline.json",
        "/tools/bundle_link_baseline.json",
        "/.pre-commit-config.yaml",
        "/.gitleaks.toml",
        "/.gitleaksignore",
        "/pyproject.toml",
        "/uv.lock",
        "/configs/claude/pyproject.toml",
        "/configs/claude/uv.lock",
        "/plugins/manifest-delegate/pyproject.toml",
        "/plugins/manifest-delegate/uv.lock",
        "/plugins/stitch-design/runtime/node/package.json",
        "/plugins/stitch-design/runtime/node/package-lock.json",
        "/.github/",
        "/docs/superpowers/specs/",
    }
    entries = {}
    for line in (REPO_ROOT_MARKER / ".github/CODEOWNERS").read_text().splitlines():
        if line and not line.startswith("#"):
            pattern, *owners = line.split()
            entries[pattern] = owners

    assert expected <= entries.keys()
    assert all(entries[pattern] == ["@RB-chrismandich"] for pattern in expected)
    assert all("TBD" not in owner for owners in entries.values() for owner in owners)


def test_put_literal_confined_to_apply_protection() -> None:
    """The literal "PUT" appears in `protection.py` only inside
    `apply_protection`'s body -- the single function that may issue a write."""
    source = (REPO_ROOT_MARKER / "src" / "manifest_agent" / "protection.py").read_text(
        encoding="utf-8"
    )
    lines = source.splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith("def apply_protection")
    )
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("def ") or lines[index].startswith("class "):
            end = index
            break
    inside = "\n".join(lines[start:end])
    outside = "\n".join(lines[:start] + lines[end:])
    assert "PUT" not in outside
    assert "PUT" in inside
