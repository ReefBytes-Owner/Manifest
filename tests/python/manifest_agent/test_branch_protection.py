"""`manifest branch-protection`: dry-run-by-default reconciliation against a
real fake `gh` on a real PATH, over the real subprocess entry point.

Every test below drives `python -m manifest_agent branch-protection` as a
real subprocess against a real, executable fake-`gh` script placed on a real
PATH (never a monkeypatched `subprocess` or an assertion against our own
fixture in isolation). The fake script logs every call and answers from a
canned per-call-sequence response directory -- see
`tests/python/manifest_agent/data/fake_gh.py.tmpl`.

Sibling module `test_branch_protection_real.py` covers the committed
`config/branch-protection.json` against the real `.github/workflows/ci.yml`
and the source guard on the "PUT" literal.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from manifest_agent import protection

REPO_ROOT_MARKER = Path(__file__).resolve().parents[3]
REPO_SRC = REPO_ROOT_MARKER / "src"

_FAKE_GH_TEMPLATE = Path(__file__).parent / "data" / "fake_gh.py.tmpl"

TEST_REPO = "acme/example"
TEST_BRANCH = "main"


def _write_fake_gh(bin_dir: Path) -> Path:
    """Render the fake-gh template as a real, executable script on a real PATH."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "gh"
    rendered = _FAKE_GH_TEMPLATE.read_text(encoding="utf-8").replace(
        "__PYTHON__", sys.executable
    )
    script.write_text(rendered, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _config_dict(workflow: str = "workflow.yml") -> dict[str, Any]:
    """The same shape as the committed `config/branch-protection.json`, with
    a test-local workflow path substituted."""
    return {
        "schema_version": 1,
        "source": "test",
        "branch": TEST_BRANCH,
        "workflow": workflow,
        "required_status_checks": {
            "strict": True,
            "jobs": ["lint", "test", "validate", "checks-aggregate-full"],
        },
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": True,
            "required_approving_review_count": 1,
            "require_last_push_approval": True,
        },
        "required_conversation_resolution": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "restrictions": None,
    }


def _write_workflow(
    path: Path,
    *,
    aggregate_continue_on_error: bool = False,
    producer_continue_on_error: bool = False,
) -> None:
    agg = str(aggregate_continue_on_error).lower()
    producer = str(producer_continue_on_error).lower()
    path.write_text(
        f"""\
name: CI
on: push
jobs:
  lint:
    name: Lint & Validate
    runs-on: ubuntu-latest
    steps: []
  test:
    name: Test
    needs: lint
    runs-on: ubuntu-latest
    steps: []
  validate:
    name: Validate Structure
    runs-on: ubuntu-latest
    steps: []
  shadow-checks-structure:
    name: Shadow Checks (structure)
    runs-on: ubuntu-latest
    continue-on-error: {producer}
    steps: []
  checks-aggregate-full:
    name: Checks Aggregate (full, non-blocking)
    needs: [shadow-checks-structure]
    runs-on: ubuntu-latest
    continue-on-error: {agg}
    steps: []
""",
        encoding="utf-8",
    )


def _raw_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The full GitHub API shape a live GET would return for this payload --
    the inverse of `protection.normalize_live`."""
    checks = payload["required_status_checks"]
    reviews = payload["required_pull_request_reviews"]
    return {
        "url": f"https://api.github.com/repos/{TEST_REPO}/branches/{TEST_BRANCH}/protection",
        "required_status_checks": {
            "strict": checks["strict"],
            "contexts": checks["contexts"],
            "checks": [{"context": c, "app_id": None} for c in checks["contexts"]],
        },
        "enforce_admins": {"enabled": payload["enforce_admins"]},
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": reviews["dismiss_stale_reviews"],
            "require_code_owner_reviews": reviews["require_code_owner_reviews"],
            "required_approving_review_count": reviews[
                "required_approving_review_count"
            ],
            "require_last_push_approval": reviews["require_last_push_approval"],
        },
        "required_conversation_resolution": {
            "enabled": payload["required_conversation_resolution"]
        },
        "allow_force_pushes": {"enabled": payload["allow_force_pushes"]},
        "allow_deletions": {"enabled": payload["allow_deletions"]},
        "restrictions": payload["restrictions"],
    }


def _mutated(payload: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """A shallow copy of `payload` with top-level keys overridden -- the
    "live state differs from proposed on exactly this setting" shape."""
    result = dict(payload)
    result.update(overrides)
    return result


def _run_cli(args: list[str], env_vars: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", "-m", "manifest_agent", "branch-protection", *args],
        capture_output=True,
        env=env_vars,
        timeout=60,
    )


def _base_env(tmp_path: Path, *, path: str) -> dict[str, str]:
    return {
        "PATH": path,
        "PYTHONPATH": os.pathsep.join((str(REPO_SRC), str(REPO_ROOT_MARKER))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(tmp_path),
    }


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    """A real bin dir with a fake `gh` on a real PATH, a real responses dir,
    a real log path, and a real temp config+workflow pair."""
    bin_dir = tmp_path / "bin"
    _write_fake_gh(bin_dir)
    return {
        "tmp_path": tmp_path,
        "bin_dir": bin_dir,
        "responses_dir": tmp_path / "responses",
        "log_path": tmp_path / "gh.log",
        "config_path": tmp_path / "branch-protection.json",
        "workflow_path": tmp_path / "workflow.yml",
    }


def _cli_env(env: dict[str, Any], *, path: str | None = None) -> dict[str, str]:
    base = _base_env(
        env["tmp_path"], path=path if path is not None else str(env["bin_dir"])
    )
    base["FAKE_GH_LOG"] = str(env["log_path"])
    base["FAKE_GH_RESPONSES"] = str(env["responses_dir"])
    return base


def _cli_args(env: dict[str, Any], *extra: str) -> list[str]:
    return [
        "--repo",
        TEST_REPO,
        "--config",
        str(env["config_path"]),
        "--workflow",
        str(env["workflow_path"]),
        *extra,
    ]


def _log_entries(env: dict[str, Any]) -> list[dict[str, Any]]:
    if not env["log_path"].exists():
        return []
    return [
        json.loads(line) for line in env["log_path"].read_text().splitlines() if line
    ]


def _write_response(
    env: dict[str, Any],
    key: str,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> None:
    """`key` is `"<method>-<call-number>"`, e.g. `"get-1"`, `"put-1"`."""
    responses_dir = env["responses_dir"]
    responses_dir.mkdir(parents=True, exist_ok=True)
    payload = {"returncode": returncode, "stdout": stdout, "stderr": stderr}
    (responses_dir / f"{key}.json").write_text(json.dumps(payload), encoding="utf-8")


def _setup_target(
    env: dict[str, Any], *, aggregate_continue_on_error: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write a temp config+workflow pair and return `(config, proposed_payload)`."""
    config = _config_dict()
    env["config_path"].write_text(json.dumps(config), encoding="utf-8")
    _write_workflow(
        env["workflow_path"], aggregate_continue_on_error=aggregate_continue_on_error
    )
    workflow_jobs = protection.parse_workflow(env["workflow_path"])
    contexts = protection.resolve_contexts(config, workflow_jobs)
    return config, protection.build_payload(config, contexts)


def _stage_get(
    env: dict[str, Any], sequence: int, payload: dict[str, Any], **kwargs: Any
) -> None:
    _write_response(
        env, f"get-{sequence}", stdout=json.dumps(_raw_from_payload(payload)), **kwargs
    )


# ---------------------------------------------------------------------------
# (a) dry-run with drift
# ---------------------------------------------------------------------------


def test_dry_run_drift_exits_one_and_changes_nothing(env: dict[str, Any]) -> None:
    """Drift in dry-run exits 1, issues only the read, and states up front
    that nothing changed."""
    _, proposed = _setup_target(env)
    _stage_get(env, 1, _mutated(proposed, enforce_admins=False))

    result = _run_cli(_cli_args(env), _cli_env(env))
    assert result.returncode == 1
    output = result.stdout.decode()
    assert "DRY RUN" in output and "nothing was changed" in output.lower()

    entries = _log_entries(env)
    assert len(entries) == 1
    assert "-X" not in entries[0]["argv"]
    assert "--input" not in entries[0]["argv"]


# ---------------------------------------------------------------------------
# (b) live already equal to proposed
# ---------------------------------------------------------------------------


def test_dry_run_in_sync_exits_zero_with_no_diff(env: dict[str, Any]) -> None:
    """A live state that already matches proposed exits 0 with an empty diff."""
    _, proposed = _setup_target(env)
    _stage_get(env, 1, proposed)

    result = _run_cli(_cli_args(env, "--json"), _cli_env(env))
    assert result.returncode == 0
    body = json.loads(result.stdout.decode())
    assert body["status"] == "in_sync"
    assert body["diff"] == []


# ---------------------------------------------------------------------------
# (c) no gh on PATH
# ---------------------------------------------------------------------------


def test_no_gh_on_path_is_blocked(env: dict[str, Any]) -> None:
    """A missing `gh` is BLOCKED (exit 3), and no `gh` call is ever attempted."""
    _setup_target(env)

    result = _run_cli(_cli_args(env), _cli_env(env, path=""))
    assert result.returncode == 3
    assert "blocked" in result.stdout.decode().lower()
    assert not env["log_path"].exists()


# ---------------------------------------------------------------------------
# (d) gh exits non-zero, non-404
# ---------------------------------------------------------------------------


def test_non_404_gh_error_is_blocked(env: dict[str, Any]) -> None:
    """A non-404 `gh` failure is BLOCKED, never treated as absent or in sync."""
    _setup_target(env)
    _write_response(env, "get-1", returncode=1, stderr="HTTP 401: Bad credentials")

    result = _run_cli(_cli_args(env), _cli_env(env))
    assert result.returncode == 3
    assert "blocked" in result.stdout.decode().lower()


# ---------------------------------------------------------------------------
# (e) 404 not-protected -- every setting absent
# ---------------------------------------------------------------------------


def test_404_not_protected_reports_every_setting_absent(env: dict[str, Any]) -> None:
    """A 404 "Branch not protected" GET is not an error: drift is reported
    with every live setting absent."""
    _setup_target(env)
    _write_response(
        env,
        "get-1",
        returncode=1,
        stderr="HTTP 404: Branch not protected (https://docs.github.com/rest)",
    )

    result = _run_cli(_cli_args(env, "--json"), _cli_env(env))
    assert result.returncode == 1
    body = json.loads(result.stdout.decode())
    assert body["status"] == "drift"
    settings = {item["setting"] for item in body["diff"]}
    assert "enforce_admins" in settings
    assert "required_status_checks.strict" in settings
    for item in body["diff"]:
        assert item["live"] in (None, [])


# ---------------------------------------------------------------------------
# (f) --apply refused when preconditions unmet -- no PUT issued
# ---------------------------------------------------------------------------


def test_apply_refused_when_aggregate_has_continue_on_error(
    env: dict[str, Any],
) -> None:
    """--apply refuses (exit 3) and issues no write when the aggregate job
    still carries `continue-on-error: true`."""
    _, proposed = _setup_target(env, aggregate_continue_on_error=True)
    _stage_get(env, 1, _mutated(proposed, enforce_admins=False))

    result = _run_cli(_cli_args(env, "--apply", "--json"), _cli_env(env))
    assert result.returncode == 3
    body = json.loads(result.stdout.decode())
    assert body["status"] == "blocked"
    unmet = [item for item in body["preconditions"] if not item["met"]]
    assert any("continue-on-error" in item["reason"] for item in unmet)

    entries = _log_entries(env)
    assert all("PUT" not in item["argv"] for item in entries)


# ---------------------------------------------------------------------------
# (g) --apply with preconditions met -- exactly one PUT, exact payload, then a GET
# ---------------------------------------------------------------------------


def test_apply_issues_one_put_with_exact_payload_then_reads_back(
    env: dict[str, Any],
) -> None:
    """A successful --apply issues exactly one PUT whose stdin JSON is the
    exact proposed payload, then re-reads and confirms."""
    _, proposed = _setup_target(env, aggregate_continue_on_error=False)
    _stage_get(env, 1, _mutated(proposed, enforce_admins=False))
    _write_response(env, "put-1", stdout="{}")
    _stage_get(env, 2, proposed)

    result = _run_cli(_cli_args(env, "--apply", "--json"), _cli_env(env))
    assert result.returncode == 0
    body = json.loads(result.stdout.decode())
    assert body["status"] == "applied"

    entries = _log_entries(env)
    assert len(entries) == 3
    methods = ["PUT" if "PUT" in entry["argv"] else "GET" for entry in entries]
    assert methods == ["GET", "PUT", "GET"]

    put_entry = entries[1]
    assert "--input" in put_entry["argv"]
    assert json.loads(put_entry["stdin"]) == proposed


# ---------------------------------------------------------------------------
# (h) --apply, post-PUT GET still shows old state -- exit 2, residual drift
# ---------------------------------------------------------------------------


def test_apply_residual_drift_after_put_exits_two(env: dict[str, Any]) -> None:
    """When the write does not stick, the confirming re-read reports
    residual drift and exits 2 -- the PUT response alone is never trusted."""
    _, proposed = _setup_target(env, aggregate_continue_on_error=False)
    old_live = _mutated(proposed, enforce_admins=False)
    _stage_get(env, 1, old_live)
    _write_response(env, "put-1", stdout="{}")
    _stage_get(env, 2, old_live)  # unchanged after the write

    result = _run_cli(_cli_args(env, "--apply", "--json"), _cli_env(env))
    assert result.returncode == 2
    body = json.loads(result.stdout.decode())
    assert body["status"] == "residual_drift"
    assert body["diff"]


# ---------------------------------------------------------------------------
# CLI help states dry-run-by-default
# ---------------------------------------------------------------------------


def test_cli_help_states_dry_run_default(tmp_path: Path) -> None:
    """`--help` states that the command is dry-run by default."""
    env_vars = _base_env(tmp_path, path=os.environ.get("PATH", os.defpath))
    result = subprocess.run(
        [sys.executable, "-B", "-m", "manifest_agent", "branch-protection", "--help"],
        capture_output=True,
        env=env_vars,
        timeout=30,
    )
    assert result.returncode == 0
    text = result.stdout.decode().lower()
    assert "dry-run" in text or "dry run" in text
