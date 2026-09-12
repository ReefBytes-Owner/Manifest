"""Contract tests for the scheduled, network-enabled dependency audits."""

from pathlib import Path

import yaml

from manifest_agent.checks.registry import load_registry

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github/workflows/dependency-audit.yml"


def _workflow() -> dict:
    with WORKFLOW_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_dependency_audits_run_weekly_in_release_only() -> None:
    workflow = _workflow()
    triggers = workflow[True]
    assert triggers["schedule"] == [{"cron": "17 4 * * 1"}]

    (job,) = workflow["jobs"].values()
    run_text = "\n".join(step["run"] for step in job["steps"] if "run" in step)
    assert "manifest provision" in run_text
    assert "--platform linux-x64" in run_text
    assert "manifest check release" in run_text
    assert "--group security" in run_text
    assert '--output "${RUNNER_TEMP}/dependency-audit.json"' in run_text
    assert "'dependency.audit.python'" in run_text
    assert "'dependency.audit.node'" in run_text
    assert "scheduled dependency audit did not pass" in run_text
    assert (
        job["env"]["MANIFEST_TOOLCHAIN_STORE"]
        == "${{ runner.temp }}/manifest-toolchain"
    )


def test_dependency_audit_receipt_is_always_uploaded() -> None:
    (job,) = _workflow()["jobs"].values()
    (upload,) = [
        step for step in job["steps"] if "upload-artifact" in str(step.get("uses", ""))
    ]
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "${{ runner.temp }}/dependency-audit.json"
    assert upload["with"]["if-no-files-found"] == "error"


def test_dependency_audit_checks_are_release_only() -> None:
    registry = load_registry(ROOT / "config/project-checks.json")
    audit_ids = {"dependency.audit.python", "dependency.audit.node"}
    profiles_by_id = {
        check_id: {
            profile for profile, ids in registry["profiles"].items() if check_id in ids
        }
        for check_id in audit_ids
    }
    assert profiles_by_id == {
        "dependency.audit.python": {"release"},
        "dependency.audit.node": {"release"},
    }
