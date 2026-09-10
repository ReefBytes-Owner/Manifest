"""Bootstrap-free Manifest lifecycle command surface."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

import click

from manifest_agent.models import ResultState
from manifest_agent.service import HARNESS_ORDER, ManifestService, ServiceReport
from manifest_agent.skill_run import SkillRunExecutionError, execute_skill_command


class _LazyChecksCommand(click.Command):
    """A `checks.cli` command whose module loads only once selected.

    `manifest_agent.checks` pulls in the project-check subsystem, which
    lifecycle commands (install/migrate/...) never touch; deferring the
    import keeps their startup path free of it (see
    test_lifecycle_startup_does_not_import_project_check_subsystem).
    """

    def __init__(self, name: str, attribute: str, help: str) -> None:
        super().__init__(name, help=help)
        self._attribute = attribute

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: click.Context | None = None,
        **extra: Any,
    ) -> click.Context:
        import manifest_agent.checks.cli as checks_cli

        command = getattr(checks_cli, self._attribute)
        return command.make_context(info_name, args, parent=parent, **extra)


@click.group()
def cli() -> None:
    """Install and manage Manifest plugin bundles."""


def _lifecycle_options(command: Callable[..., Any]) -> Callable[..., Any]:
    options = (
        click.option(
            "--harness",
            "harnesses",
            multiple=True,
            type=click.Choice((*HARNESS_ORDER, "all"), case_sensitive=False),
            help="Target a harness; repeat or use 'all'.",
        ),
        click.option(
            "--source",
            type=click.Path(path_type=Path, exists=True, file_okay=False),
            help="Use a local verified checkout.",
        ),
        click.option("--release", help="Use an immutable published release."),
        click.option(
            "--with",
            "selected_optional",
            multiple=True,
            help="Select an optional capability; repeat as needed.",
        ),
        click.option(
            "--non-interactive",
            is_flag=True,
            help="Disable prompts and implicit optional capabilities.",
        ),
        click.option("--json", "as_json", is_flag=True, help="Emit stable JSON."),
    )
    for option in reversed(options):
        command = option(command)
    return command


def _service(**options: Any) -> ManifestService:
    source = options.get("source")
    release = options.get("release")
    if source is not None and release is not None:
        raise click.UsageError("--source and --release are mutually exclusive")
    try:
        return ManifestService(
            source=source,
            release=release,
            harnesses=options.get("harnesses", ()),
            selected_optional=options.get("selected_optional", ()),
            non_interactive=options.get("non_interactive", False),
        )
    except ValueError as error:
        raise click.UsageError(str(error)) from error


def _emit(context: click.Context, report: ServiceReport, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"{report.operation}: {report.state.value}")
        for name, result in report.harnesses.items():
            click.echo(f"  {name}: {result.state.value}")
            for error in result.errors:
                click.echo(f"    error: {error}")
            for warning in result.warnings:
                click.echo(f"    warning: {warning}")
        for note in report.notes:
            click.echo(f"  note: {note}")
        for error in report.errors:
            click.echo(f"  error: {error}")
    if report.state in {ResultState.BLOCKED, ResultState.DRIFTED}:
        context.exit(1)


@cli.command()
@_lifecycle_options
@click.pass_context
def install(context: click.Context, **options: Any) -> None:
    """Install the selected Manifest release."""
    service = _service(**options)
    _emit(context, service.install(), options["as_json"])


@cli.command("bootstrap-sync")
@_lifecycle_options
@click.pass_context
def bootstrap_sync(context: click.Context, **options: Any) -> None:
    """Converge native plugins and retire verified legacy skill sources."""
    service = _service(**options)
    _emit(context, service.bootstrap_sync(), options["as_json"])


@cli.command()
@_lifecycle_options
@click.pass_context
def migrate(context: click.Context, **options: Any) -> None:
    """Migrate a legacy Manifest installation."""
    service = _service(**options)
    _emit(context, service.migrate(), options["as_json"])


@cli.command()
@_lifecycle_options
@click.option("--apply", is_flag=True, help="Repair drift owned by Manifest.")
@click.pass_context
def reconcile(context: click.Context, apply: bool, **options: Any) -> None:
    """Inspect or repair the current installation."""
    service = _service(**options)
    _emit(context, service.reconcile(apply=apply), options["as_json"])


@cli.command()
@_lifecycle_options
@click.pass_context
def update(context: click.Context, **options: Any) -> None:
    """Update every installed harness to the selected release.

    A named alias for the repair path of `reconcile`, which already re-installs
    each harness against the newly selected release and records that version in
    the receipt. The capability existed but nothing was called "update", so the
    only way to upgrade was a flag on a command named for drift inspection.

    Harness-native auto-update stays off deliberately: six harnesses polling
    their own sources independently would land on different releases, which is
    the mixed-generation drift the release-identity rule exists to prevent. One
    coordinator-driven pass keeps every harness on a single pinned release.
    """
    service = _service(**options)
    _emit(context, service.reconcile(apply=True), options["as_json"])


@cli.command()
@_lifecycle_options
@click.pass_context
def uninstall(context: click.Context, **options: Any) -> None:
    """Remove coordinator-owned Manifest installation state."""
    service = _service(**options)
    _emit(context, service.uninstall(), options["as_json"])


@cli.command("skill-run")
@click.argument("skill_path")
@click.option("--harness", required=True, type=click.Choice((*HARNESS_ORDER, "agy")))
@click.option(
    "--task-file", type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
@click.option("--model")
@click.option("--model-chain")
@click.option("--model-fallback", type=click.Choice(("auto", "confirm")))
@click.option("--recovery-id")
@click.option("--expected-version", type=int)
@click.option("--fallback-decision", type=click.Choice(("approve", "reject", "auto")))
@click.option("--replacement-tier")
@click.option("--replacement-mode", type=click.Choice(("auto", "confirm")))
@click.option("--non-interactive", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def skill_run(
    context: click.Context,
    skill_path: str,
    harness: str,
    task_file: Path | None,
    model: str | None,
    model_chain: str | None,
    model_fallback: str | None,
    recovery_id: str | None,
    expected_version: int | None,
    fallback_decision: str | None,
    replacement_tier: str | None,
    replacement_mode: str | None,
    non_interactive: bool,
    as_json: bool,
) -> None:
    """Run one skill through an explicit model-aware native handoff."""
    try:
        config_path = files("manifest_agent.data").joinpath("parallel_agent.yml")
        if not config_path.is_file():
            config_path = (
                Path(__file__).parents[2] / "configs/claude/config/parallel_agent.yml"
            )
        outcome = execute_skill_command(
            skill=skill_path,
            harness=harness,
            task_stream=sys.stdin.buffer,
            config_path=config_path,
            task_file=task_file,
            model=model,
            model_chain=model_chain,
            model_fallback=model_fallback,
            recovery_id=recovery_id,
            expected_version=expected_version,
            fallback_decision=fallback_decision,
            replacement_tier=replacement_tier,
            replacement_mode=replacement_mode,
            non_interactive=non_interactive,
            as_json=as_json,
            confirm_callback=click.confirm,
        )
    except (OSError, SkillRunExecutionError, UnicodeError, ValueError) as error:
        raise click.UsageError(str(error)) from error
    click.echo(
        json.dumps(outcome.payload, sort_keys=True)
        if as_json
        else "\n".join(outcome.text)
    )
    if outcome.exit_code:
        context.exit(outcome.exit_code)


def main() -> None:
    """Run the console entry point."""
    cli()


cli.add_command(
    _LazyChecksCommand(
        "check",
        "check",
        help="Run or list one explicitly configured project-check PROFILE.",
    )
)
cli.add_command(
    _LazyChecksCommand(
        "check-aggregate",
        "check_aggregate",
        help="Validate per-group CI producer receipts and emit one aggregate verdict.",
    )
)


class _LazyProvisionCommand(click.Command):
    """`toolchain_cli.provision`, imported only once selected (same reasoning
    as `_LazyChecksCommand`: keep lifecycle commands free of the check
    subsystem's import weight)."""

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: click.Context | None = None,
        **extra: Any,
    ) -> click.Context:
        import manifest_agent.checks.toolchain_cli as toolchain_cli

        return toolchain_cli.provision.make_context(
            info_name, args, parent=parent, **extra
        )


cli.add_command(
    _LazyProvisionCommand(
        "provision",
        help="Populate the content-addressed toolchain store from a reviewed lock.",
    )
)


class _LazyHookCommand(click.Command):
    """`hooks.cli.hook`, imported only once selected (same reasoning as
    `_LazyChecksCommand`: keep lifecycle commands free of the check
    subsystem the hook adapters call into)."""

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: click.Context | None = None,
        **extra: Any,
    ) -> click.Context:
        import manifest_agent.hooks.cli as hooks_cli

        return hooks_cli.hook.make_context(info_name, args, parent=parent, **extra)


cli.add_command(
    _LazyHookCommand(
        "hook", help="Run a thin native hook adapter: manifest hook <client> <event>."
    )
)
