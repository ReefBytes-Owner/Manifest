#!/usr/bin/env python3
"""CLI wrapper: write the current-run context `manifest check-aggregate` needs.

Read-only. Combines the trusted `GITHUB_*` env the runner sets with this
run's own job list, fetched via `gh api` (the same read-only API client
`.github/workflows/manifest-release.yml` already uses for tag/release
lookups) using the default `GITHUB_TOKEN`. Shadow-group jobs are recognized
by a `(<group>)` suffix on their workflow `name:`; every other job in the run
is irrelevant to aggregation and is skipped. Any fetch failure — including a
job or artifact list that cannot be retrieved — is BLOCKED, never silently
degraded to a partial or empty context (see `ci_context.collect_ci_context`).

Never installs, never mutates repository or run state; the only side effect
is writing the requested --output file.
"""

# Job-name recognition is intentionally an exact-match allowlist, not a
# suffix search: a loose `\(<group>\)\s*$` pattern also matches the
# aggregate job's OWN name ("Shadow Checks Aggregate (non-blocking)"),
# fabricating a bogus "non-blocking" producer group that
# `aggregate.py` then rejects as unexpected -- self-invalidating every real
# run before receipts are even read. See test_ci_context_cli.py's aggregate-
# job fixture, which pins this as a regression test.

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

GhApi = Callable[[str], dict]

try:
    from tools.project_checks.ci_context import (
        CIContextBlockedError,
        collect_ci_context,
    )
except ModuleNotFoundError:  # direct script execution from this directory
    from ci_context import CIContextBlockedError, collect_ci_context

PASS = 0
BLOCKED = 3

# Exact-match allowlist of the shadow-group job names ci.yml declares
# ("Shadow Checks (structure|lint|test|security|package)"). Deliberately NOT
# a loose suffix regex -- see the module docstring note above.
_SHADOW_GROUPS = ("structure", "lint", "test", "security", "package")
_GROUP_JOB_NAME = re.compile(r"^Shadow Checks \((" + "|".join(_SHADOW_GROUPS) + r")\)$")


def _gh_api(url: str) -> dict:
    result = subprocess.run(
        ["gh", "api", url],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def _artifact_ids_by_name(gh_api, repository: str, run_id: str) -> dict[str, str]:
    payload = gh_api(f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100")
    return {entry["name"]: str(entry["id"]) for entry in payload.get("artifacts", [])}


def _shadow_group_jobs(payload: dict) -> list[tuple[str, dict]]:
    found = []
    for entry in payload.get("jobs", []):
        match = _GROUP_JOB_NAME.match(str(entry.get("name", "")).strip())
        if match:
            found.append((match.group(1), entry))
    return found


def make_fetch_jobs(gh_api: GhApi | None = None):
    """Build a `fetch_jobs(repository, run_id, run_attempt)` closure.

    Injectable so tests can supply a fake `gh_api` instead of shelling out.
    Defaults to the real `gh api` fetcher, resolved lazily so a caller that
    monkeypatches the module-level `_gh_api` (e.g. `main()`) is honored.
    """

    def _fetch_jobs(repository: str, run_id: str, run_attempt: str) -> list[dict]:
        api = gh_api if gh_api is not None else _gh_api
        payload = api(
            f"repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}"
            f"/jobs?per_page=100"
        )
        artifacts = _artifact_ids_by_name(api, repository, run_id)
        jobs = []
        for group, entry in _shadow_group_jobs(payload):
            artifact_name = f"shadow-receipt-{group}-{run_attempt}"
            jobs.append(
                {
                    "group": group,
                    "job_id": str(entry.get("id", "")),
                    "conclusion": entry.get("conclusion"),
                    "artifact_id": artifacts.get(artifact_name, ""),
                    "run_attempt": int(run_attempt),
                }
            )
        return jobs

    return _fetch_jobs


def main(argv: list[str] | None = None) -> int:
    """Write the current-run context JSON to ``--output``.

    Returns ``PASS`` (0) once the file is written, or ``BLOCKED`` (3) without
    writing anything when the current run's identity or job evidence cannot
    be established.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        context = collect_ci_context(os.environ, make_fetch_jobs())
    except CIContextBlockedError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED
    arguments.output.write_text(
        json.dumps(context, sort_keys=True) + "\n", encoding="utf-8"
    )
    return PASS


if __name__ == "__main__":
    raise SystemExit(main())
