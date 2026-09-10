"""Click adapter for explicitly configured project checks."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import click

from . import telemetry
from .aggregate import aggregate_results
from .candidate import CandidateBlockedError, materialize_candidate
from .registry import (
    VALID_GROUPS,
    VALID_PROFILES,
    applicable_pending,
    load_registry,
    resolve_checks,
)
from .runner import run_profile

STATUS_EXITS = {"PASS": 0, "FAIL": 2, "BLOCKED": 3}
# MANIFEST_HOOK_ACTIVE is forwarded so a check body that itself shells out to
# a client CLI sees the same recursion marker `manifest hook` set on this
# process (hooks/runner.py::RECURSION_ENV_VAR) -- without it, the guard in
# hooks/core.py only ever protected the direct `manifest check` child, never
# a check body two levels down.
ENVIRONMENT_KEYS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "MANIFEST_HOOK_ACTIVE",
    "MANIFEST_TOOLCHAIN_STORE",
    "PATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONPATH",
    "TMPDIR",
    "XDG_CACHE_HOME",
)


class OutputBlockedError(RuntimeError):
    """The output path cannot be held safely for the complete operation."""


@dataclass(frozen=True)
class _OutputTarget:
    path: Path
    source: Path
    parent_fd: int
    parent_identity: tuple[int, int]


_OUTPUT_PRIMITIVES_SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)


def _list_report(registry: dict, profile: str, group: str | None) -> dict:
    checks = resolve_checks(registry, profile, group)
    pending = applicable_pending(registry, profile, group, checks)
    return {
        "schema_version": 1,
        "profile": profile,
        "group": group,
        "partial": group is not None,
        "coverage_pending": pending,
        "required_ids": [spec.id for spec in checks],
        "checks": [asdict(spec) for spec in checks],
        "status": "BLOCKED" if pending else "PASS",
    }


def _blocked_report(profile: str, group: str | None, error: Exception) -> dict:
    return {
        "schema_version": 1,
        "profile": profile,
        "group": group,
        "partial": group is not None,
        "status": "BLOCKED",
        "diagnostics": [str(error)],
    }


def _aggregate_blocked_report(profile: str, error: Exception) -> dict:
    return {
        "schema_version": 1,
        "profile": profile,
        "aggregate": True,
        "status": "BLOCKED",
        "diagnostics": [str(error)],
    }


def _load_json_object(path: Path, label: str) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"{label} is unavailable: {error}") from error
    try:
        value = json.loads(text)
    except ValueError as error:
        raise ValueError(f"{label} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_receipts(results_dir: Path) -> list[dict]:
    if not results_dir.is_dir():
        raise ValueError(f"results directory is unavailable: {results_dir}")
    entries = sorted(results_dir.glob("*.json"))
    if not entries:
        raise ValueError(f"no receipt files found under {results_dir}")
    return [_load_json_object(entry, f"receipt {entry.name}") for entry in entries]


def _require_output_primitives() -> None:
    if not _OUTPUT_PRIMITIVES_SUPPORTED:
        raise OutputBlockedError("secure output directory handles are unsupported")


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _directory_identity(descriptor: int) -> tuple[int, int]:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise OutputBlockedError("output parent is not a directory")
    return metadata.st_dev, metadata.st_ino


def _output_target(value: Path | None, source: Path) -> _OutputTarget | None:
    if value is None:
        return None
    _require_output_primitives()
    destination = Path(os.path.abspath(value))
    if destination.is_symlink():
        raise click.BadParameter("output must not be a symlink", param_hint="--output")
    try:
        resolved_parent = destination.parent.resolve(strict=True)
    except OSError as error:
        raise OSError(f"output parent is unavailable: {error}") from error
    if not resolved_parent.is_dir():
        raise click.BadParameter(
            "output parent must be a directory", param_hint="--output"
        )
    current = destination.parent
    while current != current.parent:
        if current.is_symlink():
            raise click.BadParameter(
                "output path must not traverse a symlink", param_hint="--output"
            )
        current = current.parent
    if (resolved_parent / destination.name).is_relative_to(source):
        raise click.BadParameter(
            "output must resolve strictly outside the source checkout",
            param_hint="--output",
        )
    if destination.exists() and not destination.is_file():
        raise click.BadParameter("output must be a regular file", param_hint="--output")
    expected = resolved_parent.stat()
    descriptor = _open_directory(destination.parent)
    try:
        identity = _directory_identity(descriptor)
        if identity != (expected.st_dev, expected.st_ino):
            raise OutputBlockedError("output parent changed during validation")
        try:
            metadata = os.stat(
                destination.name, dir_fd=descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            metadata = None
        if metadata is not None and not stat.S_ISREG(metadata.st_mode):
            raise OutputBlockedError("output target changed during validation")
        return _OutputTarget(destination, source, descriptor, identity)
    except (OSError, OutputBlockedError):
        os.close(descriptor)
        raise


def _verify_output_parent(target: _OutputTarget) -> None:
    try:
        resolved_parent = target.path.parent.resolve(strict=True)
        if (resolved_parent / target.path.name).is_relative_to(target.source):
            raise OutputBlockedError("output parent moved inside source checkout")
        descriptor = _open_directory(target.path.parent)
    except OSError as error:
        raise OutputBlockedError(
            "output parent is no longer safely reachable"
        ) from error
    try:
        if _directory_identity(descriptor) != target.parent_identity:
            raise OutputBlockedError("output parent identity changed")
    finally:
        os.close(descriptor)


def _temporary_output(target: _OutputTarget) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(10):
        name = f".manifest-check-{secrets.token_hex(12)}"
        try:
            return os.open(name, flags, 0o600, dir_fd=target.parent_fd), name
        except FileExistsError:
            continue
    raise OutputBlockedError("cannot create a unique output temporary file")


def _atomic_write(target: _OutputTarget, rendered: str) -> None:
    _verify_output_parent(target)
    descriptor, temporary = _temporary_output(target)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _verify_output_parent(target)
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        _verify_output_parent(target)
        try:
            metadata = os.stat(
                target.path.name,
                dir_fd=target.parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            metadata = None
        if metadata is not None and not stat.S_ISREG(metadata.st_mode):
            raise OutputBlockedError("output target changed before replacement")
        os.rename(
            temporary,
            target.path.name,
            src_dir_fd=target.parent_fd,
            dst_dir_fd=target.parent_fd,
        )
        os.fsync(target.parent_fd)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=target.parent_fd)


def _render(report: dict, as_json: bool) -> str:
    if as_json:
        return json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    lines = [f"check {report['profile']}: {report['status']}"]
    if report.get("partial"):
        lines.append(f"  group: {report['group']} (partial)")
    for check_id in report.get("required_ids", ()):
        lines.append(f"  required check: {check_id}")
    for obligation in report.get("coverage_pending", ()):
        lines.append(f"  pending coverage: {obligation}")
    for result in report.get("results", ()):
        lines.append(f"  {result['id']}: {result['status']}")
        if result["diagnostics"]:
            lines.append(f"    {result['diagnostics']}")
    for diagnostic in report.get("diagnostics", ()):
        lines.append(f"  error: {diagnostic}")
    return "\n".join(lines) + "\n"


def _emit(report: dict, as_json: bool, output: _OutputTarget | None) -> None:
    rendered = _render(report, as_json)
    if output is None:
        click.echo(rendered, nl=False)
    else:
        _atomic_write(output, rendered)


def _execution_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in ENVIRONMENT_KEYS if key in os.environ}


def _candidate_parent(source: Path) -> Path:
    try:
        parent = Path(tempfile.gettempdir()).resolve(strict=True)
    except OSError as error:
        raise CandidateBlockedError("temporary directory is unavailable") from error
    if parent == source or parent.is_relative_to(source):
        parent = source.parent
    if parent == source or parent.is_relative_to(source):
        raise CandidateBlockedError("no temporary directory outside source")
    return parent


def _execute(
    registry: dict, profile: str, group: str | None, source: Path, base: str
) -> dict:
    destination = Path(
        tempfile.mkdtemp(prefix="manifest-check-", dir=_candidate_parent(source))
    )
    try:
        candidate = materialize_candidate(source, base, destination)
        return run_profile(
            registry, profile, group, candidate, _execution_environment()
        )
    finally:
        # Cleanup cannot replace or conceal the already determined report/status.
        with suppress(OSError):
            shutil.rmtree(destination)


def _record_check_telemetry(report: dict, profile: str, source: Path, wall_seconds: float) -> None:
    """One append-only observer record per `manifest check` run (5c).
    `telemetry.record_run` is itself the safety boundary -- it never
    raises -- so this can sit unconditionally before `context.exit` without
    risking the check's own exit code."""
    duration = report.get("duration_seconds")
    request = telemetry.RecordRunRequest(
        profile=profile,
        status=str(report.get("status", "BLOCKED")),
        duration_seconds=duration if isinstance(duration, (int, float)) else wall_seconds,
        source_root=source,
        receipt_key=str(report.get("receipt_key") or ""),
        head_sha=str(report.get("head_sha") or ""),
    )
    telemetry.record_run(request)


@click.command("check")
@click.argument("profile", type=click.Choice(sorted(VALID_PROFILES)))
@click.option(
    "--project-config",
    required=True,
    type=click.Path(path_type=Path, dir_okay=False),
    help="Explicit trusted project-check registry.",
)
@click.option("--base", help="Git base revision used for changed-path selection.")
@click.option("--group", type=click.Choice(sorted(VALID_GROUPS)))
@click.option("--list", "list_only", is_flag=True, help="List without executing.")
@click.option("--json", "as_json", is_flag=True, help="Emit stable JSON.")
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False))
@click.pass_context
def check(context: click.Context, **options: Any) -> None:
    """Run or list one explicitly configured project-check PROFILE."""
    profile = options["profile"]
    project_config = options["project_config"]
    base = options["base"]
    group = options["group"]
    list_only = options["list_only"]
    as_json = options["as_json"]
    output = options["output"]
    if not list_only and base is None:
        raise click.UsageError("--base is required unless --list is used")
    source = Path.cwd().resolve()
    output_target = None
    start = time.monotonic()
    try:
        try:
            output_target = _output_target(output, source)
            registry = load_registry(project_config)
            report = (
                _list_report(registry, profile, group)
                if list_only
                else _execute(registry, profile, group, source, base)
            )
        except (CandidateBlockedError, OSError, RuntimeError, ValueError) as error:
            report = _blocked_report(profile, group, error)
        try:
            _emit(report, as_json, output_target)
        except (OSError, OutputBlockedError) as error:
            report = _blocked_report(
                profile, group, OutputBlockedError(f"output write blocked: {error}")
            )
            _emit(report, as_json, None)
        if not list_only:
            _record_check_telemetry(report, profile, source, time.monotonic() - start)
        context.exit(STATUS_EXITS[report["status"]])
    finally:
        if output_target is not None:
            with suppress(OSError):
                os.close(output_target.parent_fd)


@click.command("check-aggregate")
@click.argument("profile", type=click.Choice(sorted(VALID_PROFILES)))
@click.option(
    "--project-config",
    required=True,
    type=click.Path(path_type=Path, dir_okay=False),
    help="Explicit trusted project-check registry.",
)
@click.option(
    "--results-dir",
    required=True,
    type=click.Path(path_type=Path, file_okay=False),
    help="Directory of per-group CI producer `manifest check --json` receipts.",
)
@click.option(
    "--context",
    "context_path",
    required=True,
    type=click.Path(path_type=Path, dir_okay=False),
    help="Untrusted current-run identity assertion (see tools/project_checks/ci_context.py).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit stable JSON.")
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False))
@click.pass_context
def check_aggregate(context: click.Context, **options: Any) -> None:
    """Validate per-group CI producer receipts and emit one aggregate verdict."""
    profile = options["profile"]
    project_config = options["project_config"]
    results_dir = options["results_dir"]
    context_path = options["context_path"]
    as_json = options["as_json"]
    output = options["output"]
    source = Path.cwd().resolve()
    output_target = None
    try:
        try:
            output_target = _output_target(output, source)
            registry = load_registry(project_config)
            receipts = _load_receipts(results_dir)
            run_context = _load_json_object(context_path, "context")
            report = aggregate_results(registry, profile, receipts, run_context)
        except (OSError, RuntimeError, ValueError) as error:
            report = _aggregate_blocked_report(profile, error)
        try:
            _emit(report, as_json, output_target)
        except (OSError, OutputBlockedError) as error:
            report = _aggregate_blocked_report(
                profile, OutputBlockedError(f"output write blocked: {error}")
            )
            _emit(report, as_json, None)
        context.exit(STATUS_EXITS[report["status"]])
    finally:
        if output_target is not None:
            with suppress(OSError):
                os.close(output_target.parent_fd)
