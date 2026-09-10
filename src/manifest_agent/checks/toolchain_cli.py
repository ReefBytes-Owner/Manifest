"""Click adapter for `manifest provision` -- the only network-permitted path.

`manifest check` never imports this module; a registry-level test
(`test_toolchain_registry_guards.py::test_no_check_or_preparation_argv_invokes_provision`)
asserts no check or preparation argv contains the string "provision".
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click

from . import toolchain
from . import toolchain_provision as provision_mod


def _load_lock(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_imports(values: tuple[str, ...]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise click.BadParameter(
                f"expected NAME=PATH, got {value!r}", param_hint="--import"
            )
        result[name] = Path(path)
    return result


def _render(report: dict, as_json: bool) -> str:
    if as_json:
        return json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    lines = [f"provision: {report['status']}"]
    for outcome in report.get("outcomes", ()):
        suffix = f" ({outcome['reason']})" if outcome.get("reason") else ""
        lines.append(f"  {outcome['bundle']}: {outcome['status']}{suffix}")
    for problem in report.get("problems", ()):
        lines.append(f"  problem: {problem}")
    return "\n".join(lines) + "\n"


def _run_offline(lock: dict, store: Path, platform_id: str) -> tuple[dict, int]:
    complete, problems = provision_mod.validate_offline(lock, store, platform_id)
    status = "complete" if complete else "incomplete"
    return {"status": status, "problems": problems}, (0 if complete else 3)


def _run_imports(
    lock: dict, store: Path, platform_id: str, imports: dict[str, Path]
) -> list[provision_mod.ProvisionOutcome]:
    outcomes = []
    for name, path in imports.items():
        entry = lock.get("tools", {}).get(name)
        if entry is None:
            outcomes.append(
                provision_mod.ProvisionOutcome(
                    name, "blocked", f"toolchain: {name} unknown to lock"
                )
            )
            continue
        # import_binary() itself refuses non-binary kinds and hash mismatches.
        ctx = provision_mod.ProvisionContext(store, lock, platform_id)
        outcomes.append(provision_mod.import_binary(ctx, name, entry, path))
    return outcomes


def _resolve_safe_store(store_option: Path | None) -> Path:
    """Resolve the store location through the enforced safety check, whether
    it came from `--store` or the documented environment-variable precedence."""
    env = dict(os.environ)
    if store_option is not None:
        env["MANIFEST_TOOLCHAIN_STORE"] = str(store_option)
    return toolchain.store_root(env)


@click.command("provision")
@click.option(
    "--lock",
    required=True,
    type=click.Path(path_type=Path, dir_okay=False),
    help="Toolchain lock file.",
)
@click.option(
    "--store",
    type=click.Path(path_type=Path, file_okay=False),
    help="Override the toolchain store location.",
)
@click.option(
    "--platform", "platform_id", help="Target platform; defaults to running host."
)
@click.option("--only", "only", multiple=True, help="Limit to named bundle(s).")
@click.option(
    "--offline",
    is_flag=True,
    help="Validate the store against the lock; download nothing.",
)
@click.option(
    "--import",
    "imports",
    multiple=True,
    metavar="NAME=PATH",
    help="Adopt an existing binary if its sha256 matches the lock.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit stable JSON.")
@click.pass_context
def provision(context: click.Context, **options: Any) -> None:
    """Populate the content-addressed toolchain store from a reviewed lock."""
    platform_id = options["platform_id"] or toolchain.current_platform()
    as_json = options["as_json"]
    if options["imports"] and (options["offline"] or options["only"]):
        raise click.UsageError("--import cannot be combined with --offline or --only")
    try:
        store = _resolve_safe_store(options["store"])
    except toolchain.UnsafeStoreLocationError as error:
        report = {"status": "blocked", "problems": [str(error)]}
        click.echo(_render(report, as_json), nl=False)
        context.exit(3)
        return
    try:
        lock = _load_lock(options["lock"])
    except (OSError, ValueError) as error:
        report = {"status": "blocked", "problems": [str(error)]}
        click.echo(_render(report, as_json), nl=False)
        context.exit(3)
        return
    if options["offline"]:
        report, exit_code = _run_offline(lock, store, platform_id)
        click.echo(_render(report, as_json), nl=False)
        context.exit(exit_code)
        return
    imports = _parse_imports(options["imports"])
    if imports:
        outcomes = _run_imports(lock, store, platform_id, imports)
    else:
        only = frozenset(options["only"]) or None
        outcomes = provision_mod.provision(
            lock,
            store,
            platform=platform_id,
            only=only,
            repo_root=Path.cwd(),
            env=dict(os.environ),
        )
    blocked = any(outcome.status == "blocked" for outcome in outcomes)
    report = {
        "status": "blocked" if blocked else "complete",
        "outcomes": [asdict(outcome) for outcome in outcomes],
    }
    click.echo(_render(report, as_json), nl=False)
    context.exit(3 if blocked else 0)
