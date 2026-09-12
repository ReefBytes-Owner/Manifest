"""Project check bodies for ``types.python`` (pyright) and ``security.semgrep``.

Both scan the candidate tree AND the protected base tree (``debt.
materialize_base_tree``), exactly like ``debt_checks.py``'s
``debt.constitution``/``debt.bundle-links`` (phase-3-5-decisions.md
Correction 10, rule 1). An earlier version of this module scanned the
candidate only and hard-coded ``findings_base=[]``, on the theory that a
brand-new control has no history worth diffing against -- that was wrong:
with no base findings, EVERY finding on the candidate looks like "new
debt", including pre-existing findings in files no chunk ever touched (a
whack-a-mole symptom: the "new debt" line kept relocating to a different
untouched file after each fix, because fixing the file pyright happened to
report first just exposed the next one). The base run resolves the SAME
scanner executable, from the SAME store, via ONE ``resolve_scanner()`` call
shared by both scans -- never re-resolved per tree -- so a candidate finding
is only "new" if the identical tool, run against the base tree's own copy
of the same file, does not already report it.

Both scanners are resolved from the hash-verified toolchain store
(``manifest_agent.checks.toolchain.resolve``), never ``PATH`` -- this check's
own argv must be the ``tools/project_checks/*.py`` status-contract wrapper
form (``registry.py::_validate_status_contract``), so the generic runner
preflight cannot rewrite a ``store:`` argv[0] for us the way it does for
``test.bats``; the wrapper performs the same verified resolution itself,
directly through the public ``toolchain`` API.

Exit: 0 PASS, 2 FAIL, 3 BLOCKED (this repo's ``honors_status_contract``).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from manifest_agent.checks import debt, toolchain  # noqa: E402

PASS, FAIL, BLOCKED = 0, 2, 3
DEFAULT_BASELINE = "config/debt-baseline.json"
DEFAULT_OWNER = "@unassigned"
DEFAULT_REASON = "needs human review before merge -- see docs/SHARED_CHECKS.md"

STORE_REFS = {
    "types.python": "store:node-env/bin/pyright",
    "security.semgrep": "store:python-env/bin/semgrep",
}
SEMGREP_CONFIG = "config/semgrep/manifest.yml"
CHECK_IDS = ("types.python", "security.semgrep")


class Blocked(RuntimeError):
    """A required prerequisite (store tool, config, scanner output) is unavailable."""


def _base_sha(root: Path, override: str | None) -> str:
    if override:
        return override
    sidecar = root / ".git" / "candidate-base-sha"
    try:
        return sidecar.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise Blocked(f"base sha unavailable: {error}") from error


def resolve_scanner(check_id: str, root: Path) -> tuple[Path, str]:
    """Hash-verified store resolution of the scanner named by ``STORE_REFS``.

    Returns ``(executable_path, child_path_env)``. Never touches ``PATH``:
    the child ``PATH`` is built exclusively from the resolved tool's own
    store bin directories plus the OS baseline PATH, Correction 17 (see
    ``toolchain.ResolvedTool.path_entries``).
    """
    lock_path = root / "config" / "toolchain.lock.json"
    try:
        lock = toolchain.load_lock_file(lock_path)
    except (OSError, ValueError) as error:
        raise Blocked(f"toolchain lock unavailable: {error}") from error
    try:
        store = toolchain.store_root(dict(os.environ), root)
    except toolchain.UnsafeStoreLocationError as error:
        raise Blocked(str(error)) from error
    outcome = toolchain.resolve(
        STORE_REFS[check_id],
        lock=lock,
        store=store,
        platform=toolchain.current_platform(),
    )
    if isinstance(outcome, toolchain.BlockedReason):
        raise Blocked(outcome.reason)
    path_env = os.pathsep.join(str(entry) for entry in outcome.path_entries)
    return outcome.executable, path_env


def _run_scanner(
    argv: list[str], cwd: Path, path_env: str, label: str
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": path_env, "LC_ALL": "C", "LANG": "C"}
    try:
        return subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=180
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise Blocked(f"{label} scan failed: {error}") from error


def _relative(root: Path, absolute: str) -> str:
    try:
        return Path(absolute).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(absolute).as_posix()


def python_anchor(path: Path, line: int) -> str:
    """Innermost enclosing function/class name for ``line``, or ``""``.

    A pure best-effort AST lookup -- unreadable or unparsable source (a
    syntax error pyright itself may be reporting) degrades to a file-level
    anchor rather than raising, since a missing anchor is still a legal
    identity component (see ``debt_identity.py``).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return ""
    best_name, best_span = "", None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            start = node.lineno
            end = getattr(node, "end_lineno", start) or start
            if start <= line <= end:
                span = end - start
                if best_span is None or span < best_span:
                    best_name, best_span = node.name, span
    return best_name


def _pyright_findings(root: Path, exe: Path, path_env: str) -> list[debt.RawFinding]:
    if not (root / "pyrightconfig.json").is_file():
        raise Blocked(f"pyrightconfig.json is missing in {root}")
    result = _run_scanner([str(exe), "--outputjson"], root, path_env, "pyright")
    if result.returncode not in (0, 1):
        raise Blocked(f"pyright exited {result.returncode}: {result.stderr[:500]}")
    try:
        payload = json.loads(result.stdout or "{}")
        diagnostics = payload["generalDiagnostics"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise Blocked(f"pyright produced unusable output: {error}") from error
    findings = []
    for item in diagnostics:
        if item.get("severity") != "error":
            continue
        path = _relative(root, item["file"])
        line = int(item["range"]["start"]["line"]) + 1
        anchor = python_anchor(root / path, line)
        findings.append(
            debt.RawFinding(
                check="types.python",
                path=path,
                anchor=anchor,
                message=item["message"],
                line=line,
            )
        )
    return findings


def _semgrep_argv(executable: str) -> list[str]:
    return [
        executable,
        "scan",
        "--config",
        SEMGREP_CONFIG,
        "--metrics=off",
        "--no-git-ignore",
        "--json",
        "--error",
        "--severity",
        "ERROR",
        ".",
    ]


def _semgrep_findings(root: Path, exe: Path, path_env: str) -> list[debt.RawFinding]:
    if not (root / SEMGREP_CONFIG).is_file():
        raise Blocked(f"{SEMGREP_CONFIG} is missing in {root}")
    argv = _semgrep_argv(str(exe))
    result = _run_scanner(argv, root, path_env, "semgrep")
    if result.returncode not in (0, 1):
        raise Blocked(f"semgrep exited {result.returncode}: {result.stderr[:500]}")
    try:
        payload = json.loads(result.stdout or "{}")
        results = payload["results"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise Blocked(f"semgrep produced unusable output: {error}") from error
    findings = []
    for item in results:
        path = item["path"]
        line = int(item["start"]["line"])
        message = item.get("extra", {}).get("message", "")
        anchor = python_anchor(root / path, line) if path.endswith(".py") else ""
        findings.append(
            debt.RawFinding(
                check="security.semgrep",
                path=path,
                anchor=anchor,
                message=f"{item['check_id']}: {message}",
                line=line,
            )
        )
    return findings


_COLLECT = {"types.python": _pyright_findings, "security.semgrep": _semgrep_findings}
_REQUIRED_CONFIG = {
    "types.python": "pyrightconfig.json",
    "security.semgrep": SEMGREP_CONFIG,
}


def _require_config(check_id: str, root: Path) -> None:
    """The candidate's own config precondition, checked BEFORE store
    resolution -- a candidate missing e.g. ``pyrightconfig.json`` must BLOCK
    with that reason, not a toolchain-lock lookup its scan never needed."""
    relative = _REQUIRED_CONFIG[check_id]
    if not (root / relative).is_file():
        raise Blocked(f"{relative} is missing")


def _load_baseline(path: Path) -> debt.Baseline:
    try:
        return debt.Baseline.load(path)
    except (ValueError, OSError) as error:
        raise Blocked(f"invalid baseline at {path}: {error}") from error


def _materialize_base(root: Path, base_sha_override: str | None) -> Path:
    base_sha = _base_sha(root, base_sha_override)
    try:
        return debt.materialize_base_tree(root, base_sha)
    except debt.BaseUnavailableError as error:
        raise Blocked(str(error)) from error


def _collect_pair(
    check_id: str, root: Path, args: argparse.Namespace
) -> tuple[list[debt.RawFinding], list[debt.RawFinding]]:
    """Scan the candidate, THEN the base tree, with the SAME resolved
    scanner (one ``resolve_scanner`` call, reused for both runs) -- see the
    module docstring: a base run resolved separately, or resolved against
    the base tree's own (possibly absent) toolchain lock, would make every
    candidate finding look new for reasons that have nothing to do with the
    candidate's own code. Candidate-side preconditions (e.g. a missing
    ``pyrightconfig.json``) surface before the base tree is ever
    materialized, so a candidate-only defect BLOCKs with its own reason,
    not a base-sha lookup the candidate scan never needed."""
    _require_config(check_id, root)
    exe, path_env = resolve_scanner(check_id, root)
    collect = _COLLECT[check_id]
    findings_cand = collect(root, exe, path_env)
    base_root = _materialize_base(root, args.base_sha)
    try:
        findings_base = collect(base_root, exe, path_env)
    finally:
        shutil.rmtree(base_root, ignore_errors=True)
    return findings_cand, findings_base


def _evaluate(check_id: str, root: Path, args: argparse.Namespace) -> debt.DebtReport:
    findings_cand, findings_base = _collect_pair(check_id, root, args)
    baseline_cand = _load_baseline(root / args.baseline)
    inputs = debt.EvaluationInputs(
        findings_cand=findings_cand,
        findings_base=findings_base,
        baseline_cand=baseline_cand,
        baseline_base=debt.Baseline(),
        today=date.today(),
        commit_date=lambda sha: debt.commit_date(root, sha),
        baseline_from_base=False,
    )
    return debt.evaluate(inputs)


def _propose(check_id: str, root: Path, args: argparse.Namespace) -> int:
    findings, findings_base = _collect_pair(check_id, root, args)
    base_sha = _base_sha(root, args.base_sha)
    existing = _load_baseline(root / args.baseline)
    inputs = debt.ProposalInputs(
        findings_cand=findings,
        findings_base=findings_base,
        existing_cand=existing,
        repo_root=root,
        base_sha=base_sha,
        today=date.today(),
        owner=DEFAULT_OWNER,
        reason=DEFAULT_REASON,
    )
    payload = debt.propose_baseline(inputs)
    try:
        debt.write_proposal(payload, Path(args.output), root)
    except ValueError as error:
        raise Blocked(str(error)) from error
    print(
        f"analysis_checks.py: proposal written to {args.output} "
        f"({len(payload['entries'])} entry(ies))"
    )
    return PASS


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_id", choices=CHECK_IDS)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--base-sha", default=None, help="override (tests only)")
    parser.add_argument("--propose-baseline", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.propose_baseline and not args.output:
        print("BLOCKED: --propose-baseline requires --output", file=sys.stderr)
        return BLOCKED
    root = args.root.expanduser().resolve()
    try:
        if args.propose_baseline:
            return _propose(args.check_id, root, args)
        report = _evaluate(args.check_id, root, args)
    except Blocked as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED
    _render(report, args.json)
    return {"PASS": PASS, "FAIL": FAIL, "BLOCKED": BLOCKED}[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
