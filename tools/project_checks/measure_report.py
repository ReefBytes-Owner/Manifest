#!/usr/bin/env python3
"""Read-only derived metrics over the telemetry JSONL (5c).

Never writes to the JSONL; never talks to the network on its own -- the one
network-shaped input (PR review time) goes through an injectable `gh api`
fetcher exactly like `ci_context_cli.py`, so tests supply fixtures instead of
reaching GitHub. Renders attempts, repair cycles, check duration (p50/p95),
review time, and cost per accepted change.

**Unknown is never zero.** A run with no provider-reported cost renders as
`"unknown"`; a lineage where any attempt is uncosted renders
`"unknown (n of m attempts costed)"` -- never a total that silently drops
the uncosted attempts, and never `$0.00`.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

GhApi = Callable[[str], dict]
PASS = 0
BLOCKED = 3


def load_records(path: Path) -> list[dict]:
    """Parse every well-formed JSON object line; skip anything else rather
    than failing the whole report on one corrupt line."""
    if not path.is_file():
        return []
    records = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            parsed = json.loads(raw_line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def group_by_lineage(records: Sequence[Mapping]) -> dict[str, list[dict]]:
    """Bucket records by `candidate_lineage`; unset/empty lineage groups
    under the literal key `"(no lineage)"` rather than being dropped."""
    groups: dict[str, list[dict]] = {}
    for record in records:
        lineage = record.get("candidate_lineage")
        key = lineage if isinstance(lineage, str) and lineage else "(no lineage)"
        groups.setdefault(key, []).append(dict(record))
    return groups


def _ordered(records: Sequence[Mapping]) -> list[dict]:
    return sorted(records, key=lambda r: r.get("attempt") or 0)


def attempts(groups: Mapping[str, list[dict]]) -> dict[str, int]:
    """Attempts per lineage: the record count -- each `manifest check`/
    `manifest hook` run against that lineage is one attempt."""
    return {lineage: len(records) for lineage, records in groups.items()}


def repair_cycles(groups: Mapping[str, list[dict]]) -> dict[str, int]:
    """Count of FAIL -> re-run transitions per lineage: every FAIL record
    that was followed by a later attempt in the same lineage."""
    result = {}
    for lineage, records in groups.items():
        ordered = _ordered(records)
        result[lineage] = sum(
            1
            for prev, _next in itertools.pairwise(ordered)
            if prev.get("status") == "FAIL"
        )
    return result


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = min(len(sorted_values) - 1, round(fraction * (len(sorted_values) - 1)))
    return sorted_values[index]


def duration_percentiles(records: Sequence[Mapping]) -> dict[str, object]:
    """p50/p95 check duration in seconds; `"unknown"` (never `0`) when no
    record in the corpus carries a numeric `duration_seconds`."""
    durations = sorted(
        float(record["duration_seconds"])
        for record in records
        if isinstance(record.get("duration_seconds"), (int, float))
    )
    if not durations:
        return {"p50": "unknown", "p95": "unknown"}
    return {"p50": _percentile(durations, 0.50), "p95": _percentile(durations, 0.95)}


def cost_per_accepted_change(records: Sequence[Mapping]) -> object:
    """Sum of known costs across ALL attempts for one lineage, including
    failed ones. Never a number that silently omits uncosted attempts: if
    any attempt is unknown the result is the string
    `"unknown (n of m attempts costed)"`."""
    total_attempts = len(records)
    if total_attempts == 0:
        return "unknown"
    total = 0.0
    known = 0
    for record in records:
        cost = record.get("cost") or {}
        amount = cost.get("amount_usd")
        if cost.get("status") == "known" and isinstance(amount, (int, float)):
            total += float(amount)
            known += 1
    if known == total_attempts:
        return total
    return f"unknown ({known} of {total_attempts} attempts costed)"


def accepted_lineages(groups: Mapping[str, list[dict]]) -> dict[str, list[dict]]:
    """A lineage "accepted" a change when at least one of its attempts
    reached PASS -- cost is still summed across every attempt, not just
    the accepted one."""
    return {
        lineage: records
        for lineage, records in groups.items()
        if any(record.get("status") == "PASS" for record in records)
    }


def make_fetch_review_time(gh_api: GhApi | None = None):
    """`fetch(repository, pr_number) -> seconds|None`, injectable so tests
    supply a fake `gh_api` instead of shelling out. Any failure or missing
    approval timestamp is `None` -- rendered as `"unknown"`, not `0`."""

    def _fetch(repository: str, pr_number: str) -> float | None:
        api = gh_api if gh_api is not None else _gh_api
        try:
            pr = api(f"repos/{repository}/pulls/{pr_number}")
            reviews = api(f"repos/{repository}/pulls/{pr_number}/reviews")
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        opened = pr.get("created_at")
        approvals = [
            review.get("submitted_at")
            for review in reviews
            if isinstance(review, dict) and review.get("state") == "APPROVED"
        ]
        if not opened or not approvals:
            return None
        from datetime import datetime

        try:
            opened_at = datetime.fromisoformat(str(opened).replace("Z", "+00:00"))
            approved_at = min(
                datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                for value in approvals
            )
        except ValueError:
            return None
        return (approved_at - opened_at).total_seconds()

    return _fetch


def _gh_api(url: str) -> dict:
    result = subprocess.run(
        ["gh", "api", url], check=True, capture_output=True, text=True, timeout=30
    )
    return json.loads(result.stdout)


def build_report(records: Sequence[Mapping], review_time_seconds: float | None) -> dict:
    """Assemble every derived metric from one loaded record set plus an
    already-resolved (or `None`, meaning unknown) review time."""
    groups = group_by_lineage(records)
    accepted = accepted_lineages(groups)
    return {
        "attempts": attempts(groups),
        "repair_cycles": repair_cycles(groups),
        "check_duration_seconds": duration_percentiles(records),
        "review_time_seconds": (
            review_time_seconds if review_time_seconds is not None else "unknown"
        ),
        "cost_per_accepted_change": {
            lineage: cost_per_accepted_change(lineage_records)
            for lineage, lineage_records in accepted.items()
        },
    }


def render(report: Mapping) -> str:
    """Human-readable rendering of `build_report`'s output. Every unknown
    value renders as the literal string `"unknown"` -- this function must
    never coerce an unknown into `0`/`$0.00`."""
    lines = ["Manifest measurement report", ""]
    lines.append(f"attempts (per lineage): {report['attempts']}")
    lines.append(f"repair cycles (per lineage): {report['repair_cycles']}")
    duration = report["check_duration_seconds"]
    lines.append(f"check duration p50: {duration['p50']}  p95: {duration['p95']}")
    lines.append(f"review time (PR opened -> approved): {report['review_time_seconds']}")
    lines.append("cost per accepted change:")
    for lineage, cost in report["cost_per_accepted_change"].items():
        lines.append(f"  {lineage}: {cost}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: read `--runs-file`, optionally resolve review time
    via `gh api` for `--repository`/`--review-pr`, print the report, and
    return 0. Read-only over the JSONL; the only network call is the
    injectable review-time lookup, never made unless both flags are given."""
    parser = argparse.ArgumentParser(
        description="Read-only derived metrics over the telemetry JSONL (5c)."
    )
    parser.add_argument(
        "--runs-file", type=Path, required=True, help="Path to runs.jsonl"
    )
    parser.add_argument("--repository", help="owner/repo, for --review-pr")
    parser.add_argument("--review-pr", help="PR number to look up review time for")
    parser.add_argument("--json", action="store_true", dest="as_json")
    arguments = parser.parse_args(argv)

    records = load_records(arguments.runs_file)
    review_time = None
    if arguments.repository and arguments.review_pr:
        review_time = make_fetch_review_time()(arguments.repository, arguments.review_pr)
    report = build_report(records, review_time)
    output = (
        json.dumps(report, sort_keys=True) + "\n" if arguments.as_json else render(report)
    )
    sys.stdout.write(output)
    return PASS


if __name__ == "__main__":
    raise SystemExit(main())
