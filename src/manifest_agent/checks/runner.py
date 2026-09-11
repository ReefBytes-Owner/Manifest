"""Execute validated project checks and report honest aggregate outcomes."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path

from manifest_agent.process import redact_text

from . import receipt as _receipt
from . import toolchain, toolchain_pythonpath
from .bats_trailer import with_bats_trailer
from .candidate import CandidateBlockedError, _safe_path, _walk, git_dir_snapshot
from .candidate_integrity import identity_error, post_run_diagnostic
from .group_selection import guard_nonempty_group
from .models import (
    Candidate,
    CheckResult,
    CheckSpec,
    ProfileSelector,
    RunContext,
    ToolKey,
    ToolOutcome,
)
from .path_filters import filter_inputs, forwarded_paths, has_path_filters, matches
from .preparation import _prepare_candidate_guarded
from .process import ProcessResult, run_argv
from .registry import applicable_pending, resolve_checks
from .status import blocked as _blocked
from .status import bounded_text as _bounded_text
from .status import diagnostics as _diagnostics
from .status import executed_status as _executed_status

VERSION_PREFLIGHT_TIMEOUT_SECONDS = 10.0


def execute_check(
    check: CheckSpec,
    candidate: Candidate,
    env: dict[str, str],
    resolved: toolchain.ResolvedTool | None = None,
) -> CheckResult:
    """Execute one check without treating infrastructure failure as a finding."""
    initial_error = identity_error(candidate)
    if initial_error:
        return CheckResult(check.id, "BLOCKED", None, 0.0, initial_error, ())
    try:
        cwd, before, git_before = _execution_context(candidate, check)
    except (CandidateBlockedError, OSError) as error:
        return CheckResult(check.id, "BLOCKED", None, 0.0, redact_text(str(error)), ())
    names = tuple(before)
    selected, unavailable = _selection_outcome(check, candidate, names)
    if unavailable:
        return unavailable
    execution_paths = forwarded_paths(candidate.root, cwd, selected)
    argv = check.argv + execution_paths if check.pass_filenames else check.argv
    argv = toolchain.resolve_interpreter_argv(argv)
    argv = toolchain.rewrite_argv(argv, resolved)
    env = toolchain.resolved_env(env, resolved) if resolved is not None else env
    env, path_error = toolchain_pythonpath.resolved_env_with_path_dependencies(
        candidate.root, candidate.source_root, resolved, env
    )
    if path_error:
        return CheckResult(check.id, "BLOCKED", None, 0.0, path_error, ())
    store_before = toolchain.fingerprint_for(resolved, env)
    result = run_argv(argv, cwd=cwd, env=env, timeout_seconds=check.timeout_seconds)
    result = with_bats_trailer(check, result)
    diagnostic = post_run_diagnostic(
        candidate, (before, git_before), resolved, store_before, env
    )
    if diagnostic:
        return CheckResult(
            check.id,
            "BLOCKED",
            result.returncode,
            result.duration_seconds,
            diagnostic + _diagnostics(result),
            selected,
        )
    if result.error or result.timed_out:
        diagnostic = result.error or "check timeout"
        return _blocked(check, diagnostic + _diagnostics(result), result)
    status, contract_note = _executed_status(check, result)
    diagnostic = _diagnostics(result)
    if contract_note:
        diagnostic = f"{contract_note}\n{diagnostic}" if diagnostic else contract_note
    return CheckResult(
        check.id,
        status,
        result.returncode,
        result.duration_seconds,
        diagnostic,
        selected,
    )


def _execution_context(
    candidate: Candidate, check: CheckSpec
) -> tuple[Path, dict, dict]:
    cwd = _effective_cwd(candidate, check.cwd)
    return (
        cwd,
        _walk(candidate.root, exclude=(".git",)),
        git_dir_snapshot(candidate.root),
    )


def _effective_cwd(candidate: Candidate, relative: str) -> Path:
    cwd = _safe_path(candidate.root, relative)
    if not cwd.is_dir() or cwd.is_symlink():
        raise CandidateBlockedError("missing or unsafe execution cwd")
    return cwd


def _selection_outcome(
    check: CheckSpec, candidate: Candidate, names: tuple[str, ...]
) -> tuple[tuple[str, ...], CheckResult | None]:
    selected = _selected_inputs(names, check.inputs, check.selection, candidate)
    if check.selection == "project" and any(
        not matches(names, pattern) for pattern in check.inputs
    ):
        return selected, CheckResult(
            check.id, "BLOCKED", None, 0.0, "missing required input", selected
        )
    selected = filter_inputs(check, candidate.root, selected)
    if check.selection == "changed" and not selected:
        return (), CheckResult(
            check.id,
            "NOT_APPLICABLE",
            None,
            0.0,
            "zero applicable changed paths for declared input selector",
            (),
        )
    if check.selection == "project" and has_path_filters(check) and not selected:
        return (), CheckResult(
            check.id,
            "NOT_APPLICABLE",
            None,
            0.0,
            "zero applicable project paths for declared path filters",
            (),
        )
    return selected, None


def _selected_inputs(
    names: tuple[str, ...],
    patterns: tuple[str, ...],
    selection: str,
    candidate: Candidate,
) -> tuple[str, ...]:
    available = candidate.changed_paths if selection == "changed" else names
    return tuple(
        name
        for name in available
        if any(name in matches(names, pattern) for pattern in patterns)
    )


def _module_result(tool: dict, cwd: Path, env: dict[str, str]) -> ProcessResult | None:
    modules = tool["required_modules"]
    if not modules:
        return None
    program = (
        "import importlib.util,sys;"
        "missing=[m for m in sys.argv[1:] if importlib.util.find_spec(m) is None];"
        "print('missing required Python module: '+','.join(missing) if missing else 'modules available');"
        "raise SystemExit(bool(missing))"
    )
    executable = toolchain.resolve_interpreter_argv((tool["executable"],))[0]
    return run_argv(
        (executable, "-c", program, *modules),
        cwd=cwd,
        env=env,
        timeout_seconds=10.0,
    )


def _preflight_tool(
    tool: dict,
    cwd: Path,
    env: dict[str, str],
    lock: dict | None = None,
    candidate_root: Path | None = None,
) -> ToolOutcome:
    lock = lock or {}
    resolved, version_argv, env, blocked_reason = toolchain.resolve_for_preflight(
        tool, env, lock, candidate_root or cwd
    )
    if blocked_reason is not None:
        blocked = ProcessResult(None, "", "", 0.0, error=blocked_reason)
        return blocked, False, None, None
    version_result = run_argv(
        version_argv,
        cwd=cwd,
        env=env,
        timeout_seconds=VERSION_PREFLIGHT_TIMEOUT_SECONDS,
    )
    combined = version_result.stdout + version_result.stderr
    version_matches = tool["expected_version"] in combined.split()
    module_result = (
        _module_result(tool, cwd, env)
        if not version_result.error
        and not version_result.timed_out
        and version_result.returncode == 0
        and version_matches
        else None
    )
    return version_result, version_matches, module_result, resolved


def _preflight_error(
    outcome: ToolOutcome,
) -> str:
    version_result, version_matches, module_result, _resolved = outcome
    if version_result.error:
        return version_result.error
    if version_result.timed_out or version_result.returncode != 0:
        return "tool version probe failed"
    if not version_matches:
        return "tool version mismatch"
    if module_result is not None and (
        module_result.error or module_result.timed_out or module_result.returncode != 0
    ):
        return "required Python module unavailable: " + _diagnostics(module_result)
    return ""


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _config_digest(registry: dict) -> str:
    serialized = json.dumps(
        _jsonable(registry), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def _result_dict(result: CheckResult) -> dict:
    value = asdict(result)
    value["diagnostics"] = _bounded_text(result.diagnostics)
    value["selected_inputs"] = list(result.selected_inputs)
    return value


def run_profile(
    registry: dict,
    profile: str,
    group: str | None,
    candidate: Candidate,
    env: dict[str, str],
) -> dict:
    """Run a resolved profile once; Phase 2 deliberately has no success cache.

    Every body/probe child env has its caches redirected to one per-run temp
    dir outside the candidate (Correction 4), so no body can pass the strict
    identity check below by writing `__pycache__` into the candidate.
    """
    start = time.monotonic()
    selector = ProfileSelector(profile, group)
    checks = resolve_checks(registry, profile, group)
    guard_nonempty_group(profile, group, checks)
    with toolchain.run_cache_directory() as run_tmp:
        env = toolchain.cache_environment(env, run_tmp)
        results: list[CheckResult] = []
        initial_identity_error = identity_error(candidate)
        if initial_identity_error:
            results = [
                CheckResult(check.id, "BLOCKED", None, 0.0, initial_identity_error, ())
                for check in checks
            ]
            context = RunContext(registry, candidate, env, {}, {})
            return _report(context, selector, checks, results, start)
        tool_results, failed_preparations = _prepare_for_checks(
            registry, checks, candidate, env
        )
        context = RunContext(
            registry, candidate, env, tool_results, failed_preparations
        )
        results = _run_checks(context, checks)
        final_identity_error = identity_error(candidate)
        if final_identity_error:
            results = [
                _blocked_after_identity(result, final_identity_error)
                if result.status in {"PASS", "NOT_APPLICABLE"}
                else result
                for result in results
            ]
        return _report(context, selector, checks, results, start)


def _prepare_for_checks(
    registry: dict,
    checks: tuple[CheckSpec, ...],
    candidate: Candidate,
    env: dict[str, str],
) -> tuple[dict[ToolKey, ToolOutcome], dict[str, str]]:
    selected_groups = {check.group for check in checks}
    preparations = tuple(
        preparation
        for preparation in registry["candidate_preparations"]
        if selected_groups.intersection(preparation.groups)
    )
    tool_results: dict[ToolKey, ToolOutcome] = {}
    failed_preparation_groups: dict[str, str] = {}

    def preflight(preparation) -> CheckResult | None:
        try:
            cwd = _effective_cwd(candidate, preparation.cwd)
        except (CandidateBlockedError, OSError) as error:
            return CheckResult(
                preparation.id, "BLOCKED", None, 0.0, redact_text(str(error)), ()
            )
        tool_key = (preparation.tool, cwd)
        if tool_key not in tool_results:
            tool_results[tool_key] = _preflight_tool(
                registry["tools"][preparation.tool],
                cwd,
                env,
                registry.get("toolchain_lock_document", {}),
                candidate.root,
            )
        failure = _preflight_error(tool_results[tool_key])
        if failure:
            outcome = tool_results[tool_key]
            process_result = outcome[2] if outcome[2] is not None else outcome[0]
            return _blocked(preparation, failure, process_result)
        return None

    preparation_results = _prepare_candidate_guarded(
        candidate, preparations, env, preflight
    )
    for preparation, result in zip(preparations, preparation_results, strict=True):
        if result.status != "PASS":
            for check_group in preparation.groups:
                failed_preparation_groups.setdefault(check_group, result.diagnostics)
    return tool_results, failed_preparation_groups


def _run_checks(
    context: RunContext, checks: tuple[CheckSpec, ...]
) -> list[CheckResult]:
    results: list[CheckResult] = []
    result_by_id: dict[str, CheckResult] = {}
    for check in checks:
        result = _run_check(context, check, result_by_id)
        results.append(result)
        result_by_id[check.id] = result
    return results


def _run_check(
    context: RunContext, check: CheckSpec, prior: dict[str, CheckResult]
) -> CheckResult:
    candidate, env = context.candidate, context.env
    try:
        cwd = _effective_cwd(candidate, check.cwd)
        names = tuple(_walk(candidate.root, exclude=(".git",)))
    except (CandidateBlockedError, OSError) as error:
        return CheckResult(check.id, "BLOCKED", None, 0.0, redact_text(str(error)), ())
    _, unavailable = _selection_outcome(check, candidate, names)
    if check.selection == "changed" and unavailable:
        return unavailable
    if check.group in context.failed_preparations:
        return CheckResult(
            check.id,
            "BLOCKED",
            None,
            0.0,
            "required candidate preparation failed: "
            + context.failed_preparations[check.group],
            (),
        )
    failed_dependencies = tuple(
        dependency
        for dependency in check.dependencies
        if dependency in prior and prior[dependency].status != "PASS"
    )
    if failed_dependencies:
        return CheckResult(
            check.id,
            "BLOCKED",
            None,
            0.0,
            "prerequisite did not pass: " + ", ".join(failed_dependencies),
            (),
        )
    if unavailable:
        return unavailable
    tool_key = (check.tool, cwd)
    lock = context.registry.get("toolchain_lock_document", {})
    if tool_key not in context.tool_results:
        context.tool_results[tool_key] = _preflight_tool(
            context.registry["tools"][check.tool], cwd, env, lock, candidate.root
        )
    outcome = context.tool_results[tool_key]
    failure = _preflight_error(outcome)
    if failure:
        process_result = outcome[2] if outcome[2] is not None else outcome[0]
        return _blocked(check, failure, process_result)
    if check.scratch_home:
        env = toolchain.scratch_home_environment(env, check.id)
    return execute_check(check, candidate, env, outcome[3])


def _blocked_after_identity(result: CheckResult, diagnostic: str) -> CheckResult:
    return CheckResult(
        result.id,
        "BLOCKED",
        result.returncode,
        result.duration_seconds,
        diagnostic + result.diagnostics,
        result.selected_inputs,
    )


def _report(
    context: RunContext,
    selector: ProfileSelector,
    checks: tuple[CheckSpec, ...],
    results: list[CheckResult],
    start: float,
) -> dict:
    registry = context.registry
    statuses = {result.status for result in results}
    pending = applicable_pending(registry, selector.profile, selector.group, checks)
    status = (
        "FAIL"
        if "FAIL" in statuses
        else "BLOCKED"
        if "BLOCKED" in statuses or pending
        else "PASS"
    )
    inputs = _receipt.ReportInputs(
        context, selector, checks, [_result_dict(result) for result in results], start
    )
    return _receipt.build_report(inputs, pending, status, _config_digest(registry))
