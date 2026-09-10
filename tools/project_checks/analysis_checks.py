#!/usr/bin/env python3
"""Project check bodies for ``types.python`` (pyright) and ``security.semgrep``.

Both scan the CANDIDATE tree only (no base-tree double-scan): these are
brand-new controls introduced by this chunk, so there is no historical
``F_base`` worth diffing against -- every finding is evaluated against
``config/debt-baseline.json`` directly (``debt.py``'s five verdict rules
still apply; see the module docstring of ``debt.py``). Compare
``debt_checks.py``, which DOES need the base-tree diff because
``debt.constitution``/``debt.bundle-links`` replace an EXISTING count
baseline with real history behind it.

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
    store bin directories plus ``os.defpath`` (see
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


def _pyright_findings(root: Path) -> list[debt.RawFinding]:
    if not (root / "pyrightconfig.json").is_file():
        raise Blocked("pyrightconfig.json is missing")
    exe, path_env = resolve_scanner("types.python", root)
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


def _semgrep_findings(root: Path) -> list[debt.RawFinding]:
    if not (root / SEMGREP_CONFIG).is_file():
        raise Blocked(f"{SEMGREP_CONFIG} is missing")
    exe, path_env = resolve_scanner("security.semgrep", root)
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


def _load_baseline(path: Path) -> debt.Baseline:
    try:
        return debt.Baseline.load(path)
    except (ValueError, OSError) as error:
        raise Blocked(f"invalid baseline at {path}: {error}") from error


def _evaluate(check_id: str, root: Path, args: argparse.Namespace) -> debt.DebtReport:
    findings = _COLLECT[check_id](root)
    baseline_cand = _load_baseline(root / args.baseline)
    inputs = debt.EvaluationInputs(
        findings_cand=findings,
        findings_base=[],
        baseline_cand=baseline_cand,
        baseline_base=debt.Baseline(),
        today=date.today(),
        commit_date=lambda sha: debt.commit_date(root, sha),
        baseline_from_base=False,
    )
    return debt.evaluate(inputs)


def _propose(check_id: str, root: Path, args: argparse.Namespace) -> int:
    findings = _COLLECT[check_id](root)
    existing = _load_baseline(root / args.baseline)
    base_sha = _base_sha(root, args.base_sha)
    inputs = debt.ProposalInputs(
        findings_cand=findings,
        findings_base=[],
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
