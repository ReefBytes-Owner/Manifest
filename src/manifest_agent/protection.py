"""Branch-protection reconciliation: pure logic for `manifest branch-protection`.

Computes the exact GitHub branch-protection target from
`config/branch-protection.json` (settings) and the referenced CI workflow
(job-id -> status-check-context resolution), reads the live setting through an
injectable `gh` runner, diffs live against proposed, and -- only when every
precondition holds -- builds the single mutating request that would reconcile
them. This module never decides whether to apply; `protection_cli.py` owns the
dry-run-by-default policy and the `--apply` gate.

Unavailable is always BLOCKED, never a pass: a missing/unauthenticated `gh`, an
unparseable response, or any non-404 API error all raise or return a blocked
result -- never "no drift". A 404 "Branch not protected" response is the one
legitimate non-error absence; every setting is then reported as absent so the
diff shows the full proposed state.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "branch-protection.json"

# `gh api` argv -> (returncode, stdout, stderr). Callers pass the request body
# (empty bytes for a read-only call) as the second argument.
GhRunner = Callable[[Sequence[str], bytes], "tuple[int, str, str]"]
Resolver = Callable[[str], "str | None"]


class BlockedError(Exception):
    """Raised when protection state cannot be determined -- never a silent pass."""


def default_resolver(path_env: str | None) -> Resolver:
    """An explicit, injectable PATH-bound resolver for the `gh` executable --
    never a bare `os.environ["PATH"]` read buried in the runner."""

    def resolve(executable: str) -> str | None:
        """Return the absolute path to `executable` on this resolver's PATH, or None."""
        return shutil.which(executable, path=path_env)

    return resolve


@dataclass(frozen=True)
class JobInfo:
    """One `jobs.<id>` entry from the referenced workflow file."""

    name: str
    needs: tuple[str, ...]
    continue_on_error: bool


def load_config(config_path: Path) -> dict[str, Any]:
    """Parse the declared branch-protection target JSON."""
    return json.loads(config_path.read_text(encoding="utf-8"))


def parse_workflow(workflow_path: Path) -> dict[str, JobInfo]:
    """Parse the workflow's `jobs:` map. `yaml.safe_load` only -- CON-013."""
    document = yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}
    jobs = document.get("jobs") or {}
    result: dict[str, JobInfo] = {}
    for job_id, spec in jobs.items():
        spec = spec or {}
        needs = spec.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        result[job_id] = JobInfo(
            name=str(spec.get("name", job_id)),
            needs=tuple(needs),
            continue_on_error=bool(spec.get("continue-on-error", False)),
        )
    return result


def aggregate_job_id(config: dict[str, Any]) -> str:
    """The required aggregate job, by construction the last entry in
    `required_status_checks.jobs` (phase-3-5-decisions.md Correction 1: there
    is exactly one required aggregate check, and it is listed last)."""
    jobs = config["required_status_checks"]["jobs"]
    return jobs[-1]


def resolve_contexts(
    config: dict[str, Any], workflow_jobs: dict[str, JobInfo]
) -> tuple[str, ...]:
    """Required job ids -> their workflow `name:` (the GitHub status-check
    context). A job id absent from the workflow is BLOCKED, never silently
    dropped from the required set."""
    required_jobs = config["required_status_checks"]["jobs"]
    missing = [job_id for job_id in required_jobs if job_id not in workflow_jobs]
    if missing:
        raise BlockedError(f"job id(s) not found in workflow: {', '.join(missing)}")
    return tuple(workflow_jobs[job_id].name for job_id in required_jobs)


@dataclass(frozen=True)
class Precondition:
    """One owner-verifiable gate that must hold before `--apply` is permitted."""

    name: str
    met: bool
    reason: str = ""


def check_preconditions(
    config: dict[str, Any], workflow_jobs: dict[str, JobInfo]
) -> tuple[Precondition, ...]:
    """Every gate `--apply` must satisfy: required jobs exist, and the
    aggregate job plus its producers carry no `continue-on-error: true`
    (a required check that cannot fail is a false green)."""
    required_jobs = config["required_status_checks"]["jobs"]
    missing = [job_id for job_id in required_jobs if job_id not in workflow_jobs]
    preconditions = [
        Precondition(
            "required jobs exist in workflow",
            met=not missing,
            reason="" if not missing else f"missing job id(s): {', '.join(missing)}",
        )
    ]
    agg_id = aggregate_job_id(config)
    agg = workflow_jobs.get(agg_id)
    name = f"{agg_id} and its producer jobs carry no continue-on-error"
    if agg is None:
        preconditions.append(
            Precondition(name, met=False, reason=f"{agg_id} not found in workflow")
        )
    else:
        offenders = [agg_id] if agg.continue_on_error else []
        for needed in agg.needs:
            producer = workflow_jobs.get(needed)
            if producer is not None and producer.continue_on_error:
                offenders.append(needed)
        reason = (
            ""
            if not offenders
            else (
                f"continue-on-error: true on {', '.join(offenders)} -- a required "
                "check that cannot fail is a false green"
            )
        )
        preconditions.append(Precondition(name, met=not offenders, reason=reason))
    return tuple(preconditions)


def preconditions_met(preconditions: Sequence[Precondition]) -> bool:
    """True only when every precondition holds -- the single gate `--apply` checks."""
    return all(item.met for item in preconditions)


def build_payload(config: dict[str, Any], contexts: Sequence[str]) -> dict[str, Any]:
    """The exact REST body for the mutating branch-protection request."""
    checks = config["required_status_checks"]
    reviews = config["required_pull_request_reviews"]
    return {
        "required_status_checks": {
            "strict": checks["strict"],
            "contexts": list(contexts),
        },
        "enforce_admins": config["enforce_admins"],
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": reviews["dismiss_stale_reviews"],
            "require_code_owner_reviews": reviews["require_code_owner_reviews"],
            "required_approving_review_count": reviews[
                "required_approving_review_count"
            ],
            "require_last_push_approval": reviews["require_last_push_approval"],
        },
        "required_conversation_resolution": config["required_conversation_resolution"],
        "allow_force_pushes": config["allow_force_pushes"],
        "allow_deletions": config["allow_deletions"],
        "restrictions": config["restrictions"],
    }


# The shape every setting takes when the branch has no protection at all --
# every value absent, never guessed.
ABSENT_LIVE: dict[str, Any] = {
    "required_status_checks": {"strict": None, "contexts": []},
    "enforce_admins": None,
    "required_pull_request_reviews": {
        "dismiss_stale_reviews": None,
        "require_code_owner_reviews": None,
        "required_approving_review_count": None,
        "require_last_push_approval": None,
    },
    "required_conversation_resolution": None,
    "allow_force_pushes": None,
    "allow_deletions": None,
    "restrictions": None,
}


def normalize_live(raw: dict[str, Any]) -> dict[str, Any]:
    """Reshape a live GET body into the same shape `build_payload` emits."""
    checks = raw.get("required_status_checks") or {}
    reviews = raw.get("required_pull_request_reviews") or {}
    conversation = raw.get("required_conversation_resolution") or {}
    force_pushes = raw.get("allow_force_pushes") or {}
    deletions = raw.get("allow_deletions") or {}
    return {
        "required_status_checks": {
            "strict": checks.get("strict"),
            "contexts": list(checks.get("contexts") or []),
        },
        "enforce_admins": (raw.get("enforce_admins") or {}).get("enabled"),
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": reviews.get("dismiss_stale_reviews"),
            "require_code_owner_reviews": reviews.get("require_code_owner_reviews"),
            "required_approving_review_count": reviews.get(
                "required_approving_review_count"
            ),
            "require_last_push_approval": reviews.get("require_last_push_approval"),
        },
        "required_conversation_resolution": conversation.get("enabled"),
        "allow_force_pushes": force_pushes.get("enabled"),
        "allow_deletions": deletions.get("enabled"),
        "restrictions": raw.get("restrictions"),
    }


@dataclass(frozen=True)
class LiveResult:
    """One `get_live` outcome: `status` is `"ok"` (live populated), `"absent"`
    (404 not-protected, live is `ABSENT_LIVE`), or `"blocked"` (live is None,
    `reason` explains why)."""

    status: str
    live: dict[str, Any] | None
    reason: str = ""


def get_live(gh_runner: GhRunner, repo: str, branch: str) -> LiveResult:
    """One read-only `gh api` GET. A non-404 error, an unparseable body, or a
    non-zero exit with no recognizable "not protected" text is BLOCKED."""
    returncode, stdout, stderr = gh_runner(
        ("api", f"repos/{repo}/branches/{branch}/protection"), b""
    )
    if returncode == 0:
        try:
            raw = json.loads(stdout)
        except ValueError as error:
            return LiveResult(
                "blocked", None, reason=f"unparseable gh response: {error}"
            )
        return LiveResult("ok", normalize_live(raw))
    combined = f"{stdout}\n{stderr}".lower()
    if "not protected" in combined:
        return LiveResult("absent", dict(ABSENT_LIVE))
    detail = stderr.strip() or stdout.strip() or "no output"
    return LiveResult("blocked", None, reason=f"gh api exited {returncode}: {detail}")


@dataclass(frozen=True)
class SettingDiff:
    """One leaf setting where live and proposed disagree."""

    setting: str
    live: Any
    proposed: Any


def diff_settings(
    live: dict[str, Any], proposed: dict[str, Any], *, prefix: str = ""
) -> list[SettingDiff]:
    """Per-setting diff between live and proposed, dotted-path per leaf key."""
    diffs: list[SettingDiff] = []
    for key, proposed_value in proposed.items():
        path = f"{prefix}{key}"
        live_value = live.get(key) if isinstance(live, dict) else None
        if isinstance(proposed_value, dict):
            diffs.extend(
                diff_settings(live_value or {}, proposed_value, prefix=f"{path}.")
            )
        elif live_value != proposed_value:
            diffs.append(SettingDiff(path, live_value, proposed_value))
    return diffs


@dataclass(frozen=True)
class ApplyResult:
    """The outcome of the one write call `apply_protection` issues."""

    status: str  # "applied" | "blocked"
    reason: str = ""


def apply_protection(
    gh_runner: GhRunner, repo: str, branch: str, payload: dict[str, Any]
) -> ApplyResult:
    """Issue exactly one mutating request with the exact payload on stdin.

    This is the only function in the module that issues the write call --
    see `test_branch_protection.py::test_put_literal_confined_to_apply_protection`,
    which greps the module source and asserts the HTTP method literal below
    appears only inside this function's body.
    """
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    returncode, stdout, stderr = gh_runner(
        (
            "api",
            "-X",
            "PUT",
            f"repos/{repo}/branches/{branch}/protection",
            "--input",
            "-",
        ),
        body,
    )
    if returncode != 0:
        detail = stderr.strip() or stdout.strip() or "no output"
        return ApplyResult(
            "blocked", reason=f"gh api PUT exited {returncode}: {detail}"
        )
    return ApplyResult("applied")
