#!/usr/bin/env python3
"""Project check bodies for the identity-based debt ratchet (Phase 3, C3).

Two checks share this body: ``debt.constitution`` (Code Constitution
findings) and ``debt.bundle-links`` (``check_bundle_link_references.py``
findings). Each compares the candidate tree against the protected base tree
(``git archive <base_sha>``, materialized to a sibling temp dir -- never
this worktree) using ``manifest_agent.checks.debt``'s five verdict rules.

``config/debt-baseline.json`` is read, never written, on a normal run.
``--propose-baseline --output PATH`` (PATH outside this repository) writes a
reviewable proposal instead -- a human workflow, not something this check,
or any automatic path, may apply to itself. See the module docstring of
``debt.py`` and ``docs/SHARED_CHECKS.md``.

Exit: 0 PASS, 2 FAIL, 3 BLOCKED (this repo's honors_status_contract).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from manifest_agent.checks import debt  # noqa: E402

PASS, FAIL, BLOCKED = 0, 2, 3
DEFAULT_BASELINE = "config/debt-baseline.json"
CONSTITUTION_SCRIPT = "configs/claude/scripts/constitution_check.py"
BUNDLE_LINK_SCRIPT = "tools/check_bundle_link_references.py"
DEFAULT_OWNER = "@unassigned"
DEFAULT_REASON = "needs human review before merge -- see docs/SHARED_CHECKS.md"


class Blocked(RuntimeError):
    """A required prerequisite (base tree, baseline, scanner) is unavailable."""


def _base_sha(root: Path, override: str | None) -> str:
    if override:
        return override
    sidecar = root / ".git" / "candidate-base-sha"
    try:
        return sidecar.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise Blocked(f"base sha unavailable: {error}") from error


def _run_scanner(argv: list[str], cwd: Path, label: str) -> str:
    try:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=120
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise Blocked(f"{label} scan failed: {error}") from error
    if result.returncode not in (0, 1):
        raise Blocked(f"{label} scan exited {result.returncode}: {result.stderr[:500]}")
    return result.stdout


def _tracked_paths(cand_root: Path, extensions: tuple[str, ...]) -> list[str]:
    """Git-tracked, repo-relative paths from the CANDIDATE's own history.

    Deliberately not a filesystem walk of whichever root is being scanned:
    this repo carries generated, gitignored mirrors (``.apm/skills`` is a
    generated copy of ``plugins/*/skills/*`` -- see ``.gitignore``) that
    exist on disk in a live worktree but were never committed, so a
    ``git archive`` of any commit never contains them. Walking the
    filesystem directly would find them in the candidate (real disk) but
    never in the base tree (archive-only), making every one of their
    findings look like "new debt" on every run -- not a real regression.
    Resolving the path set once, from git, and filtering it per root below
    keeps both scans looking at the same tracked source.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(cand_root), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise Blocked(f"cannot list tracked files: {error}") from error
    return sorted(
        line
        for line in result.stdout.splitlines()
        if line and Path(line).suffix in extensions
    )


def _existing(root: Path, paths: list[str]) -> list[str]:
    """``paths`` narrowed to the ones actually present under ``root`` --
    a file added or removed between the base and candidate tree is a
    legitimate asymmetry, not an error."""
    return [p for p in paths if (root / p).is_file()]


_ADVISORY_LINE = re.compile(r"^(C-[A-Z]+)\s+\S+\s+advisory\s")


def _advisory_check_ids(root: Path) -> set[str]:
    """Checks the ratchet does not gate on -- same scope as the old count
    baseline (``configs/claude/scripts/constitution/baseline.py``:
    ``if check is None or check.advisory: continue``)."""
    argv = [sys.executable, str(root / CONSTITUTION_SCRIPT), "--list"]
    stdout = _run_scanner(argv, root, "constitution --list")
    return {
        m.group(1) for line in stdout.splitlines() if (m := _ADVISORY_LINE.match(line))
    }


def _constitution_findings(
    scan_root: Path, tool_root: Path, tracked: list[str]
) -> list[debt.RawFinding]:
    files = _existing(scan_root, tracked)
    if not files:
        return []
    advisory = _advisory_check_ids(tool_root)
    argv = [
        sys.executable,
        str(tool_root / CONSTITUTION_SCRIPT),
        "--no-baseline",
        "--format",
        "json",
        *files,
    ]
    stdout = _run_scanner(argv, scan_root, "constitution")
    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError as error:
        raise Blocked(f"constitution scan produced non-JSON output: {error}") from error
    return [
        debt.RawFinding(
            check=item["check"],
            path=item["path"],
            anchor=item.get("anchor", ""),
            message=item["message"],
            line=item["line"],
        )
        for item in payload
        if item["check"] not in advisory
    ]


def _bundle_link_findings(scan_root: Path, tool_root: Path) -> list[debt.RawFinding]:
    argv = [
        sys.executable,
        str(tool_root / BUNDLE_LINK_SCRIPT),
        "--no-baseline",
        "--json",
        "--repo-root",
        str(scan_root),
    ]
    stdout = _run_scanner(argv, scan_root, "bundle-link")
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError as error:
        raise Blocked(f"bundle-link scan produced non-JSON output: {error}") from error
    return [
        debt.RawFinding(
            check="bundle-link",
            path=item["path"],
            anchor="",
            message=f"{item['kind']}: {item['message']}",
            line=item["line"],
        )
        for item in payload.get("violations", [])
    ]


def _collect(check_id: str, cand_root: Path, base_root: Path):
    """Scan both trees with the SAME (candidate) scanner version, over the
    SAME git-tracked path set (see ``_tracked_paths``).

    Identity depends on structural context (anchor) the scanner computes;
    scanning the base tree with an older copy of the scanner would make
    every finding whose anchor field the base's own copy cannot produce
    look like "new debt" the moment the scanner itself changes -- not an
    actual regression in the base tree's code. One scanner version, applied
    to both file sets, keeps identity comparable across a scanner upgrade.
    """
    if check_id == "debt.bundle-links":
        return (
            _bundle_link_findings(cand_root, cand_root),
            _bundle_link_findings(base_root, cand_root),
        )
    tracked = _tracked_paths(cand_root, (".py", ".sh"))
    return (
        _constitution_findings(cand_root, cand_root, tracked),
        _constitution_findings(base_root, cand_root, tracked),
    )


def _propose(check_id: str, cand_root: Path, base_root: Path, args) -> int:
    findings_cand, findings_base = _collect(check_id, cand_root, base_root)
    existing = debt.Baseline.load(cand_root / args.baseline)
    base_sha = _base_sha(cand_root, args.base_sha)
    inputs = debt.ProposalInputs(
        findings_cand=findings_cand,
        findings_base=findings_base,
        existing_cand=existing,
        repo_root=cand_root,
        base_sha=base_sha,
        today=date.today(),
        owner=DEFAULT_OWNER,
        reason=DEFAULT_REASON,
    )
    payload = debt.propose_baseline(inputs)
    try:
        debt.write_proposal(payload, Path(args.output), cand_root)
    except ValueError as error:
        raise Blocked(str(error)) from error
    print(
        f"debt_checks.py: proposal written to {args.output} ({len(payload['entries'])} entry(ies))"
    )
    return PASS


def _evaluate(check_id: str, cand_root: Path, base_root: Path, args) -> debt.DebtReport:
    findings_cand, findings_base = _collect(check_id, cand_root, base_root)
    baseline_cand = debt.Baseline.load(cand_root / args.baseline)
    baseline_base = debt.Baseline.load(base_root / args.baseline)
    inputs = debt.EvaluationInputs(
        findings_cand=findings_cand,
        findings_base=findings_base,
        baseline_cand=baseline_cand,
        baseline_base=baseline_base,
        today=date.today(),
        commit_date=lambda sha: debt.commit_date(cand_root, sha),
        baseline_from_base=args.baseline_from_base,
    )
    return debt.evaluate(inputs)


def _render(report: debt.DebtReport, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "status": report.status,
                    "fails": [
                        {
                            "identity": v.identity,
                            "check": v.check,
                            "path": v.path,
                            "anchor": v.anchor,
                            "message": v.message,
                            "line": v.line,
                            "reason": v.reason,
                        }
                        for v in report.fails
                    ],
                    "blocked_reasons": list(report.blocked_reasons),
                    "proposed_exceptions": [
                        {"identity": e.identity, "check": e.check, "path": e.path}
                        for e in report.proposed_exceptions
                    ],
                },
                indent=2,
            )
        )
        return
    for v in report.fails:
        print(
            f"FAIL: {v.path}:{v.line} [{v.check}] {v.reason}: {v.message}",
            file=sys.stderr,
        )
    for reason in report.blocked_reasons:
        print(f"BLOCKED: {reason}", file=sys.stderr)
    if report.proposed_exceptions:
        print(
            f"debt_checks.py: {len(report.proposed_exceptions)} baseline entry(ies) "
            "not yet present on the base tree -- proposals for human review, not applied.",
            file=sys.stderr,
        )


def _run(check_id: str, args) -> int:
    cand_root = args.root.expanduser().resolve()
    base_sha = _base_sha(cand_root, args.base_sha)
    try:
        base_root = debt.materialize_base_tree(cand_root, base_sha)
    except debt.BaseUnavailableError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED
    try:
        if args.propose_baseline:
            return _propose(check_id, cand_root, base_root, args)
        report = _evaluate(check_id, cand_root, base_root, args)
    except Blocked as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED
    finally:
        shutil.rmtree(base_root, ignore_errors=True)

    _render(report, args.json)
    return {"PASS": PASS, "FAIL": FAIL, "BLOCKED": BLOCKED}[report.status]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_id", choices=("debt.constitution", "debt.bundle-links"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--base-sha", default=None, help="override (tests only)")
    parser.add_argument("--baseline-from-base", action="store_true")
    parser.add_argument("--propose-baseline", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.propose_baseline and not args.output:
        print("BLOCKED: --propose-baseline requires --output", file=sys.stderr)
        return BLOCKED
    try:
        return _run(args.check_id, args)
    except Blocked as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
