"""`manifest branch-protection` -- dry-run by default.

Computes the branch-protection target from `config/branch-protection.json`
and diffs it against the live GitHub setting. Never mutates anything unless
`--apply` is given *and* every precondition in `protection.check_preconditions`
holds; activation is the repository owner's act, never this command's default.

Exit codes: 0 live already matches proposed; 1 drift found (dry-run, nothing
changed); 2 `--apply` issued a write but the re-read did not match (residual
drift, the live state is no longer trustworthy without investigation); 3
BLOCKED (`gh` unavailable, an API error, a missing job, or an unmet
precondition on `--apply`).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from . import protection

_EXIT_OK = 0
_EXIT_DRIFT = 1
_EXIT_RESIDUAL_DRIFT = 2
_EXIT_BLOCKED = 3

_DEFAULT_REPO_PLACEHOLDER = "{owner}/{repo}"


def default_gh_runner(
    executable: str, *, timeout_seconds: float = 30.0
) -> protection.GhRunner:
    """A real subprocess-backed `GhRunner` bound to one resolved executable."""

    def run(args: tuple[str, ...], stdin_data: bytes) -> tuple[int, str, str]:
        """Run `executable` with `args`, feeding `stdin_data` on stdin."""
        try:
            result = subprocess.run(
                [executable, *args],
                input=stdin_data,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return (-1, "", str(error))
        return (
            result.returncode,
            result.stdout.decode("utf-8", "replace"),
            result.stderr.decode("utf-8", "replace"),
        )

    return run


def _resolve_gh() -> str | None:
    override = os.environ.get("MANIFEST_GH")
    if override:
        return override
    resolver = protection.default_resolver(os.environ.get("PATH"))
    return resolver("gh")


def _render(report: dict[str, Any], as_json: bool) -> str:
    if as_json:
        return json.dumps(report, sort_keys=True, separators=(",", ":"))
    lines: list[str] = []
    if report.get("dry_run"):
        lines.append("DRY RUN -- nothing was changed.")
    lines.append(f"branch-protection: {report['status']}")
    for item in report.get("preconditions", ()):
        marker = "ok" if item["met"] else "UNMET"
        lines.append(f"  precondition [{marker}]: {item['name']}")
        if item["reason"]:
            lines.append(f"    {item['reason']}")
    for item in report.get("diff", ()):
        lines.append(
            f"  diff {item['setting']}: live={item['live']!r} proposed={item['proposed']!r}"
        )
    if report.get("reason"):
        lines.append(f"  reason: {report['reason']}")
    return "\n".join(lines)


def _blocked(reason: str, *, dry_run: bool) -> dict[str, Any]:
    return {"status": "blocked", "reason": reason, "dry_run": dry_run}


def _diff_payload(diffs: list[protection.SettingDiff]) -> list[dict[str, Any]]:
    return [
        {"setting": d.setting, "live": d.live, "proposed": d.proposed} for d in diffs
    ]


@dataclass(frozen=True)
class _GhTarget:
    """The three values every live/apply call needs, bundled so helper
    functions stay under the parameter ceiling instead of threading them
    through individually."""

    gh_runner: protection.GhRunner
    repo: str
    branch: str


@dataclass(frozen=True)
class _Prepared:
    config: dict[str, Any]
    workflow_jobs: dict[str, protection.JobInfo]
    preconditions: tuple[protection.Precondition, ...]
    preconditions_payload: list[dict[str, Any]]
    contexts: tuple[str, ...]


def _prepare(config_path: Path, workflow_path: Path | None) -> _Prepared:
    """Load config + workflow, compute preconditions and required contexts.

    Raises `OSError`/`ValueError` (unreadable/malformed config or workflow)
    or `protection.BlockedError` (a required job id missing from the
    workflow) -- the caller turns any of these into one BLOCKED report.
    """
    config = protection.load_config(config_path)
    resolved_workflow = workflow_path or (protection.REPO_ROOT / config["workflow"])
    workflow_jobs = protection.parse_workflow(resolved_workflow)
    preconditions = protection.check_preconditions(config, workflow_jobs)
    preconditions_payload = [
        {"name": item.name, "met": item.met, "reason": item.reason}
        for item in preconditions
    ]
    contexts = protection.resolve_contexts(config, workflow_jobs)
    return _Prepared(
        config, workflow_jobs, preconditions, preconditions_payload, contexts
    )


def _dry_run_report(
    preconditions_payload: list[dict[str, Any]], diff_payload: list[dict[str, Any]]
) -> tuple[dict[str, Any], int]:
    status = "in_sync" if not diff_payload else "drift"
    report = {
        "status": status,
        "dry_run": True,
        "preconditions": preconditions_payload,
        "diff": diff_payload,
    }
    return report, (_EXIT_OK if not diff_payload else _EXIT_DRIFT)


def _post_apply_report(
    target: _GhTarget, payload: dict[str, Any], prepared: _Prepared
) -> tuple[dict[str, Any], int]:
    """Re-read live state after a write and confirm it matches; residual
    drift after a write is reported, never trusted away."""
    post_live = protection.get_live(target.gh_runner, target.repo, target.branch)
    if post_live.status != "ok":
        report = {
            "status": "blocked",
            "dry_run": False,
            "reason": f"post-apply re-read failed: {post_live.reason or post_live.status}",
            "preconditions": prepared.preconditions_payload,
        }
        return report, _EXIT_BLOCKED
    residual_payload = _diff_payload(
        protection.diff_settings(post_live.live or {}, payload)
    )
    if residual_payload:
        report = {
            "status": "residual_drift",
            "dry_run": False,
            "preconditions": prepared.preconditions_payload,
            "diff": residual_payload,
        }
        return report, _EXIT_RESIDUAL_DRIFT
    report = {
        "status": "applied",
        "dry_run": False,
        "preconditions": prepared.preconditions_payload,
        "diff": [],
    }
    return report, _EXIT_OK


def _apply_report(
    target: _GhTarget,
    payload: dict[str, Any],
    prepared: _Prepared,
    diff_payload: list[dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    """Gate the write on preconditions, issue it, then delegate to
    `_post_apply_report` for the confirming re-read."""
    if not protection.preconditions_met(prepared.preconditions):
        report = {
            "status": "blocked",
            "dry_run": False,
            "reason": "preconditions unmet -- refusing to apply",
            "preconditions": prepared.preconditions_payload,
            "diff": diff_payload,
        }
        return report, _EXIT_BLOCKED
    if not diff_payload:
        report = {
            "status": "in_sync",
            "dry_run": False,
            "preconditions": prepared.preconditions_payload,
            "diff": [],
        }
        return report, _EXIT_OK
    apply_result = protection.apply_protection(
        target.gh_runner, target.repo, target.branch, payload
    )
    if apply_result.status != "applied":
        report = {
            "status": "blocked",
            "dry_run": False,
            "reason": apply_result.reason,
            "preconditions": prepared.preconditions_payload,
            "diff": diff_payload,
        }
        return report, _EXIT_BLOCKED
    return _post_apply_report(target, payload, prepared)


def _reconcile(
    *, config_path: Path, workflow_path: Path | None, repo: str, apply: bool
) -> tuple[dict[str, Any], int]:
    dry_run = not apply
    try:
        prepared = _prepare(config_path, workflow_path)
    except (OSError, ValueError, protection.BlockedError) as error:
        return _blocked(str(error), dry_run=dry_run), _EXIT_BLOCKED

    gh_executable = _resolve_gh()
    if gh_executable is None:
        report = _blocked("gh not found on PATH", dry_run=dry_run)
        report["preconditions"] = prepared.preconditions_payload
        return report, _EXIT_BLOCKED
    target = _GhTarget(
        gh_runner=default_gh_runner(gh_executable),
        repo=repo,
        branch=prepared.config["branch"],
    )
    payload = protection.build_payload(prepared.config, prepared.contexts)

    live_result = protection.get_live(target.gh_runner, target.repo, target.branch)
    if live_result.status == "blocked":
        report = _blocked(live_result.reason, dry_run=dry_run)
        report["preconditions"] = prepared.preconditions_payload
        return report, _EXIT_BLOCKED

    diff_payload = _diff_payload(
        protection.diff_settings(live_result.live or {}, payload)
    )

    if not apply:
        return _dry_run_report(prepared.preconditions_payload, diff_payload)
    return _apply_report(target, payload, prepared, diff_payload)


@click.command("branch-protection")
@click.option(
    "--apply/--dry-run",
    "apply",
    default=False,
    help="Reconcile live branch-protection to the declared target. Dry-run by "
    "default -- activation is the repository owner's act.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit stable JSON.")
@click.option(
    "--repo",
    default=_DEFAULT_REPO_PLACEHOLDER,
    help="OWNER/NAME; defaults to gh's own {owner}/{repo} resolution from cwd.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=protection.DEFAULT_CONFIG_PATH,
    help="Declared branch-protection target.",
)
@click.option(
    "--workflow",
    "workflow_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Override the workflow file named by the config's 'workflow' field.",
)
@click.pass_context
def branch_protection(context: click.Context, **options: Any) -> None:
    """Reconcile GitHub branch protection on --repo to config/branch-protection.json.

    Dry-run by default: reports drift and changes nothing. --apply issues the
    write only when every precondition holds, then re-reads to confirm.
    """
    report, exit_code = _reconcile(
        config_path=options["config_path"],
        workflow_path=options["workflow_path"],
        repo=options["repo"],
        apply=options["apply"],
    )
    click.echo(_render(report, options["as_json"]))
    if exit_code:
        context.exit(exit_code)
