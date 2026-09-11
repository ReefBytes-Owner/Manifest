"""CLI entry — `append` (US1) and `run` (US2) implemented.

`list`/`prune` (US4) are wired in the lifecycle phase. Heavy deps (executor →
Playwright) are imported lazily inside each handler so `--help` succeeds before
any dependency/config lookup (repo convention; cli-audit-help).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# NOTE: heavy/runtime imports (appender→catalog→yaml, executor→Playwright) are
# deferred into each handler so `--help` and arg parsing never require them
# (cli-audit-help). build_parser()/main() stay stdlib-only.


def _err(msg: str) -> None:
    print(f"smoke_test: {msg}", file=sys.stderr)


def _discover_apps(catalog_dir: str) -> list[str]:
    d = Path(catalog_dir)
    return sorted(p.stem for p in d.glob("*.yaml")) if d.is_dir() else []


def _load_workflow(args: argparse.Namespace) -> dict:
    raw = (
        sys.stdin.read()
        if args.stdin
        else Path(args.from_file).read_text(encoding="utf-8")
    )
    return json.loads(raw)


def _default_report_path() -> str:
    """The `--junit`/`--report` default when the flag is not given explicitly.

    A bare relative filename resolves against the process's cwd -- fine for
    a human running this directly, but exactly the write-into-the-candidate
    bug when a `manifest check` body runs it: the candidate identity check
    then (correctly) reports "candidate identity changed" for a report file
    the check itself produced. `MANIFEST_RUN_TMP` is the runner's per-run
    temp directory, set the same place `PYTHONPYCACHEPREFIX` etc. are
    (`manifest_agent.checks.toolchain_cache.cache_environment`); when it is
    present the report lands there instead, outside the candidate.
    """
    run_tmp = os.environ.get("MANIFEST_RUN_TMP", "")
    return str(Path(run_tmp) / "smoke-report.xml") if run_tmp else "smoke-report.xml"


def _cmd_append(args: argparse.Namespace) -> int:
    from .appender import SmokeTestAppender
    from .validation import ValidationError

    try:
        workflow = _load_workflow(args)
    except (OSError, json.JSONDecodeError) as exc:
        _err(f"could not read workflow description: {exc}")
        return 1
    appender = SmokeTestAppender(catalog_dir=args.catalog_dir)
    try:
        result = appender.append(workflow, dry_run=args.dry_run)
    except ValidationError as exc:
        _err("invalid workflow description (catalog unchanged):")
        for e in exc.errors:
            _err(f"  - {e}")
        return 2
    except OSError as exc:
        _err(f"I/O error writing catalog: {exc}")
        return 1
    verb = (
        "would update"
        if args.dry_run and result.updated
        else "would add"
        if args.dry_run
        else "updated"
        if result.updated
        else "added"
    )
    print(f"{verb} test '{result.id}' in {result.path}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from . import report as report_mod
    from .executor import SmokeTestExecutor
    from .models import tier_rank
    from .redact import Redactor
    from .validation import ValidationError

    try:
        tier_rank(args.tier)  # validate selection early → usage error (exit 2)
    except ValueError as exc:
        _err(str(exc))
        return 2
    apps = [args.app] if args.app else _discover_apps(args.catalog_dir)
    if not apps:
        _err(f"no catalogs found under {args.catalog_dir!r}")
        return 2

    execu = SmokeTestExecutor(
        catalog_dir=args.catalog_dir, persist_state=args.persist_state
    )
    redactor = Redactor()
    reports = []
    try:
        for app in apps:
            reports.append(
                execu.run(
                    app, tier=args.tier, base_url=args.base_url, redactor=redactor
                )
            )
    except ValidationError as exc:
        _err("invalid catalog (not run):")
        for e in exc.errors:
            _err(f"  - {e}")
        return 2
    except RuntimeError as exc:  # e.g. Playwright missing for ui/api steps
        _err(str(exc))
        return 2

    junit_path = args.junit if args.junit is not None else _default_report_path()
    if junit_path:
        try:
            report_mod.write_junit(reports, junit_path, redactor)
        except OSError as exc:
            _err(f"could not write JUnit XML: {exc}")
            return 1
    report_mod.print_summary(reports, redactor)
    return report_mod.aggregate_exit(reports)


def _cmd_list(args: argparse.Namespace) -> int:
    from .appender import SmokeTestAppender

    appender = SmokeTestAppender(catalog_dir=args.catalog_dir)
    apps = [args.app] if args.app else _discover_apps(args.catalog_dir)
    coverage = {app: appender.list_coverage(app) for app in apps}
    if args.json:
        print(json.dumps(coverage, indent=2))
    else:
        for app, records in coverage.items():
            print(f"{app}:")
            if not records:
                print("  (no tests)")
            for r in records:
                print(f"  {r['id']}  [{r['tier']}]  {r['steps']} step(s)")
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    from .appender import SmokeTestAppender

    appender = SmokeTestAppender(catalog_dir=args.catalog_dir)
    try:
        removed = appender.prune(args.app, args.id)
    except OSError as exc:
        _err(f"I/O error pruning catalog: {exc}")
        return 1
    print(f"{'removed' if removed else 'already absent'}: '{args.id}' in {args.app}")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    # Delegate to the migrate module's own CLI — one argv source of truth (it
    # stays independently runnable as `python3 -m smoke_orchestrator.migrate`).
    from .migrate import main as migrate_main

    argv = [args.src_dir, "--app", args.app]
    if args.out:
        argv += ["--out", args.out]
    return migrate_main(argv)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="smoke_test.py",
        description="Declarative tiered E2E smoke tests: append/run/list/prune.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    ap = sub.add_parser(
        "append", help="Add or update one test from a workflow description"
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--from",
        dest="from_file",
        metavar="FILE",
        help="workflow description JSON file",
    )
    src.add_argument(
        "--stdin", action="store_true", help="read workflow description JSON from stdin"
    )
    ap.add_argument(
        "--catalog-dir",
        default="smoke-catalog",
        help="catalog root (default: smoke-catalog)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="validate and report; write nothing"
    )
    ap.set_defaults(func=_cmd_append)

    rp = sub.add_parser("run", help="Run the catalog filtered by tier (the gate)")
    rp.add_argument("--app", help="run one app's catalog; omit to run all")
    rp.add_argument(
        "--tier",
        default="Lite",
        help="cumulative tier Lite|Full|Full+Extra (default: Lite)",
    )
    rp.add_argument(
        "--junit",
        "--report",
        dest="junit",
        metavar="PATH",
        default=None,
        help=(
            "write JUnit XML here (default: smoke-report.xml, or "
            "$MANIFEST_RUN_TMP/smoke-report.xml when MANIFEST_RUN_TMP is set "
            "-- e.g. running under `manifest check`; empty string to skip)"
        ),
    )
    rp.add_argument("--base-url", dest="base_url", help="override the catalog base_url")
    rp.add_argument(
        "--catalog-dir",
        default="smoke-catalog",
        help="catalog root (default: smoke-catalog)",
    )
    rp.add_argument(
        "--persist-state",
        dest="persist_state",
        action="store_true",
        help="enable cross-run persisted (non-secret) state",
    )
    rp.set_defaults(func=_cmd_run)

    lp = sub.add_parser(
        "list", help="Report coverage (id, tier, step count) without running"
    )
    lp.add_argument("--app", help="limit to one app")
    lp.add_argument("--json", action="store_true", help="machine-readable output")
    lp.add_argument(
        "--catalog-dir",
        default="smoke-catalog",
        help="catalog root (default: smoke-catalog)",
    )
    lp.set_defaults(func=_cmd_list)

    pp = sub.add_parser("prune", help="Remove a test from a catalog by id (idempotent)")
    pp.add_argument("--app", required=True, help="catalog file to edit")
    pp.add_argument("--id", required=True, help="test identifier to remove")
    pp.add_argument(
        "--catalog-dir",
        default="smoke-catalog",
        help="catalog root (default: smoke-catalog)",
    )
    pp.set_defaults(func=_cmd_prune)

    mp = sub.add_parser(
        "migrate",
        help="Migrate legacy browser-use YAML prompts into a smoke catalog",
    )
    mp.add_argument(
        "src_dir",
        help="directory of legacy browser-use *.yaml files (e.g. tests/browser)",
    )
    mp.add_argument(
        "--app", required=True, help="catalog app slug (^[a-z0-9][a-z0-9-]*$)"
    )
    mp.add_argument(
        "--out", help="output catalog path (default: smoke-catalog/<app>.yaml)"
    )
    mp.set_defaults(func=_cmd_migrate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
