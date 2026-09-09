"""Collect the current CI run's identity for shared-check aggregation.

Read-only: derives the repository/workflow/run identity from `GITHUB_*`
environment variables the runner itself sets, then fetches this run's own
producer job list (conclusions, artifact identities) through an injectable
read-only fetch function so tests can supply fixtures instead of reaching the
network. A repository-controlled file is never treated as authority here —
only trusted runner-set environment plus the live current-run API response
feed the returned context, which `manifest check-aggregate` then still
validates as untrusted input. Any fetch failure is BLOCKED rather than
silently degraded to an empty or partial context.

Threat model note (until Phase 5): a pull request can edit the workflow file
that produces the shared-check evidence between the moment the checks run and
the moment this context is collected, so a sufficiently motivated change
could alter what "the current run" means for its own aggregation. Closing
that gap needs pinning the workflow to a protected ref (or an equivalent
supply-chain control) and is out of scope for this module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

REQUIRED_ENV_KEYS = (
    "GITHUB_REPOSITORY",
    "GITHUB_WORKFLOW",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_SHA",
)

FetchJobs = Callable[[str, str, str], list[Mapping[str, object]]]


class CIContextBlockedError(RuntimeError):
    """The current run's identity or producer job evidence is unavailable."""


def _read_required_env(env: Mapping[str, str]) -> dict[str, str]:
    missing = [key for key in REQUIRED_ENV_KEYS if not env.get(key)]
    if missing:
        raise CIContextBlockedError(
            "missing required CI environment values: " + ", ".join(missing)
        )
    return {key: env[key] for key in REQUIRED_ENV_KEYS}


def _fetch_producer_jobs(
    fetch_jobs: FetchJobs, repository: str, run_id: str, run_attempt: str
) -> list[dict[str, object]]:
    try:
        jobs = fetch_jobs(repository, run_id, run_attempt)
    except Exception as error:  # any fetch failure is BLOCKED, not just known types
        raise CIContextBlockedError(
            f"current-run producer job fetch failed: {error}"
        ) from error
    if not isinstance(jobs, list):
        raise CIContextBlockedError(
            "current-run producer job fetch returned a non-list result"
        )
    # `fetch_jobs` is called scoped to exactly this run attempt (it is one of
    # its own parameters), so every job it returns belongs to this attempt.
    # Stamping it here — rather than trusting a per-job field the provider
    # response may or may not carry — is what lets `aggregate_results` reject
    # a `--context` document whose job entries were edited to claim a
    # different (stale) attempt.
    return [{**job, "run_attempt": int(run_attempt)} for job in jobs]


def collect_ci_context(
    env: Mapping[str, str], fetch_jobs: FetchJobs
) -> dict[str, object]:
    """Build the trusted current-run context consumed by `check-aggregate`.

    `fetch_jobs(repository, run_id, run_attempt)` must return this run's own
    job records, read-only, from the CI provider's API. Raising from
    `fetch_jobs` (network error, auth failure, non-2xx, malformed response) is
    the expected way to signal unavailable evidence; it is converted here into
    `CIContextBlockedError` rather than an empty/partial context.
    """
    values = _read_required_env(env)
    producer_jobs = _fetch_producer_jobs(
        fetch_jobs,
        values["GITHUB_REPOSITORY"],
        values["GITHUB_RUN_ID"],
        values["GITHUB_RUN_ATTEMPT"],
    )
    return {
        "repository": values["GITHUB_REPOSITORY"],
        "workflow": values["GITHUB_WORKFLOW"],
        "run_id": values["GITHUB_RUN_ID"],
        "run_attempt": int(values["GITHUB_RUN_ATTEMPT"]),
        "tested_sha": values["GITHUB_SHA"],
        "producer_jobs": producer_jobs,
    }
