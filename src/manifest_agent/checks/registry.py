"""Load, validate, and resolve declarative project-check registries."""

from __future__ import annotations

import math
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .models import CheckSpec, PreparationSpec
from .path_filters import validate_path_filters
from .registry_schema import read_validated_document
from .registry_tools import normalized_tools, validate_tools
from .toolchain import lock_digest_for_registry

VALID_GROUPS = frozenset({"lint", "test", "structure", "security", "package"})
VALID_PROFILES = frozenset({"quick", "full", "security", "release"})
CHANGED_CATEGORIES = frozenset({"format", "lint", "syntax"})
POSIX_SHELL_EVALUATORS = frozenset({"bash", "dash", "ksh", "sh", "zsh"})
POWERSHELL_EVALUATORS = frozenset({"powershell", "pwsh"})
POSIX_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
POWERSHELL_BOOLEAN_OPTIONS = frozenset(
    {
        "?",
        "help",
        "login",
        "mta",
        "noexit",
        "nologo",
        "noninteractive",
        "noprofile",
        "noprofileloadtime",
        "servermode",
        "socketservermode",
        "sshservermode",
        "sta",
        "version",
    }
)
POWERSHELL_VALUE_OPTIONS = frozenset(
    {
        "configurationname",
        "custompipename",
        "executionpolicy",
        "inputformat",
        "outputformat",
        "remotingprotocolversion",
        "settingsfile",
        "windowstyle",
        "workingdirectory",
    }
)
POWERSHELL_FILE_OPTIONS = frozenset({"f", "file"})
UNSAFE_PREPARATION_EXECUTABLES = frozenset(
    {
        "apt",
        "apt-get",
        "brew",
        "bun",
        "cargo",
        "conda",
        "curl",
        "dnf",
        "gem",
        "git",
        "go",
        "mamba",
        "npm",
        "npx",
        "pacman",
        "pdm",
        "pip",
        "pip3",
        "pnpm",
        "poetry",
        "uv",
        "wget",
        "yum",
        "yarn",
    }
)


REPO_OWNED_CHECK_ARGV = re.compile(r"^tools/project_checks/[^/]+\.py$")
REPO_OWNED_INTERPRETER = re.compile(r"^python3?(\.\d+)?$")


def _validate_status_contract(check: dict[str, Any], label: str) -> None:
    # argv[0] or argv[0:2] only -- matching anywhere would let e.g.
    # ["markdownlint-cli2", "tools/project_checks/x.py"] falsely qualify.
    if not check.get("honors_status_contract", False):
        return
    argv = check["argv"]
    direct = bool(argv) and bool(REPO_OWNED_CHECK_ARGV.match(argv[0]))
    interp = len(argv) >= 2 and bool(REPO_OWNED_INTERPRETER.match(Path(argv[0]).name))
    if not (direct or (interp and REPO_OWNED_CHECK_ARGV.match(argv[1]))):
        raise ValueError(
            f"{label} honors_status_contract requires argv invoking "
            "tools/project_checks/*.py as argv[0], or argv[1] behind a "
            "python interpreter"
        )


def _validate_argv(argv: list[str], label: str) -> None:
    if any("\0" in argument for argument in argv):
        raise ValueError(f"{label} argv contains NUL")


def _executable_name(value: str) -> str:
    name = PureWindowsPath(value).name or PurePosixPath(value).name
    return name.casefold().removesuffix(".exe")


def _require_separate_option_value(
    argv: list[str], index: int, label: str, option: str
) -> int:
    value_index = index + 1
    if value_index >= len(argv) or argv[value_index].startswith(("-", "+")):
        raise ValueError(f"{label} option {option!r} requires a value")
    return value_index + 1


def _validate_posix_shell_options(argv: list[str], label: str) -> None:
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--" or not argument.startswith(("-", "+")):
            return
        if argument in POSIX_VALUE_OPTIONS:
            index = _require_separate_option_value(argv, index, label, argument)
            continue
        if not argument.startswith("--"):
            cluster = argument[1:]
            if "c" in cluster:
                raise ValueError(f"{label} cannot use shell string evaluation")
            if "o" in cluster or "O" in cluster:
                raise ValueError(
                    f"{label} uses ambiguous value-taking shell option {argument!r}"
                )
        index += 1


def _powershell_option(argument: str) -> tuple[str, str | None]:
    option, separator, attached_value = argument.lstrip("-/").partition(":")
    return option.casefold(), attached_value if separator else None


def _validate_powershell_options(argv: list[str], label: str) -> None:
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--" or not argument.startswith(("-", "/")):
            return
        option, attached_value = _powershell_option(argument)
        if option in POWERSHELL_FILE_OPTIONS:
            if attached_value is not None:
                if not attached_value:
                    raise ValueError(f"{label} option {argument!r} requires a value")
                return
            _require_separate_option_value(argv, index, label, argument)
            return
        if option in {"c", "command", "commandwithargs"} or (
            option and "encodedcommand".startswith(option)
        ):
            raise ValueError(f"{label} cannot use PowerShell string evaluation")
        if option in POWERSHELL_BOOLEAN_OPTIONS and attached_value is None:
            index += 1
            continue
        if option in POWERSHELL_VALUE_OPTIONS:
            if attached_value is not None:
                if not attached_value:
                    raise ValueError(f"{label} option {argument!r} requires a value")
                index += 1
            else:
                index = _require_separate_option_value(argv, index, label, argument)
            continue
        raise ValueError(f"{label} uses unsupported PowerShell option {argument!r}")


def _validate_direct_invocation(argv: list[str], label: str) -> None:
    executable = _executable_name(argv[0])
    if executable == "env":
        raise ValueError(f"{label} cannot use a direct env wrapper")
    if executable in POSIX_SHELL_EVALUATORS:
        _validate_posix_shell_options(argv, label)
    if executable == "cmd" and any(
        argument.casefold().startswith(("/c", "/k")) for argument in argv[1:]
    ):
        raise ValueError(f"{label} cannot use cmd string evaluation")
    if executable in POWERSHELL_EVALUATORS:
        _validate_powershell_options(argv, label)


def _validate_candidate_path(value: str, label: str) -> None:
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.anchor
        or windows_path.drive
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    ):
        raise ValueError(f"{label} must be candidate-relative and cannot escape")


def _validate_timeout(value: float | int, label: str) -> None:
    if not math.isfinite(float(value)) or value <= 0:
        raise ValueError(f"{label} timeout_seconds must be positive and finite")


def _validate_unique_ids(records: list[dict[str, Any]], label: str) -> None:
    seen: set[str] = set()
    for record in records:
        record_id = record["id"]
        if record_id in seen:
            raise ValueError(f"duplicate {label} id: {record_id}")
        seen.add(record_id)


def _validate_tool_reference(
    record: dict[str, Any], tools: dict[str, dict[str, Any]], label: str
) -> None:
    tool_name = record["tool"]
    if tool_name not in tools:
        raise ValueError(f"{label} references unknown tool: {tool_name}")
    expected_version = tools[tool_name]["expected_version"]
    if record["version"] != expected_version:
        raise ValueError(
            f"{label} version {record['version']!r} does not exactly match "
            f"tool {tool_name!r} expected version {expected_version!r}"
        )
    if record["argv"][0] != tools[tool_name]["executable"]:
        message = f"{label} argv executable must exactly match tool {tool_name!r}"
        raise ValueError(message)


def _validate_checks(
    checks: list[dict[str, Any]], tools: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    check_by_id = {check["id"]: check for check in checks}
    for check in checks:
        label = f"check {check['id']!r}"
        _validate_argv(check["argv"], label)
        _validate_direct_invocation(check["argv"], label)
        _validate_timeout(check["timeout_seconds"], label)
        _validate_candidate_path(check["cwd"], f"{label} cwd")
        for input_path in check["inputs"]:
            _validate_candidate_path(input_path, f"{label} input")
        validate_path_filters(check, label)
        _validate_tool_reference(check, tools, label)
        _validate_status_contract(check, label)
        if check["selection"] == "changed" and not (
            check["group"] == "lint" and check["category"] in CHANGED_CATEGORIES
        ):
            raise ValueError(f"{label} requires project selection")
        for dependency_id in check["dependencies"]:
            dependency = check_by_id.get(dependency_id)
            if dependency is None:
                raise ValueError(
                    f"{label} references unknown dependency: {dependency_id}"
                )
            if dependency["group"] != check["group"]:
                raise ValueError(
                    f"{label} has cross-group dependency on {dependency_id!r}"
                )
    return check_by_id


def _validate_preparations(
    preparations: list[dict[str, Any]], tools: dict[str, dict[str, Any]]
) -> None:
    for preparation in preparations:
        label = f"preparation {preparation['id']!r}"
        _validate_argv(preparation["argv"], label)
        _validate_direct_invocation(preparation["argv"], label)
        _validate_timeout(preparation["timeout_seconds"], label)
        _validate_candidate_path(preparation["cwd"], f"{label} cwd")
        for input_path in preparation["inputs"]:
            _validate_candidate_path(input_path, f"{label} input")
        for output_path in preparation["outputs"]:
            _validate_candidate_path(output_path, f"{label} output")
        unknown_groups = set(preparation["groups"]) - VALID_GROUPS
        if not preparation["groups"] or unknown_groups:
            raise ValueError(f"{label} has empty or unknown groups")
        _validate_tool_reference(preparation, tools, label)
        executable_names = {
            _executable_name(preparation["argv"][0]),
            _executable_name(tools[preparation["tool"]]["executable"]),
        }
        if executable_names & UNSAFE_PREPARATION_EXECUTABLES:
            raise ValueError(
                f"{label} uses a forbidden network or package-manager executable"
            )


def _validate_semantics(document: dict[str, Any]) -> None:
    tools = document["tools"]
    checks = document["checks"]
    preparations = document["candidate_preparations"]
    _validate_unique_ids(checks, "check")
    _validate_unique_ids(preparations, "preparation")
    validate_tools(tools)
    check_by_id = _validate_checks(checks, tools)
    _validate_preparations(preparations, tools)
    for check in checks:
        _visit_dependencies(check["id"], check_by_id, set(), set())

    all_check_ids = set(check_by_id)
    for profile, check_ids in document["profiles"].items():
        unknown_ids = set(check_ids) - all_check_ids
        if unknown_ids:
            raise ValueError(
                f"{profile!r} profile unknown checks: {sorted(unknown_ids)}"
            )

    normalized = _normalize(document)
    full_ids = {check.id for check in resolve_checks(normalized, "full", None)}
    security_ids = {check.id for check in resolve_checks(normalized, "security", None)}
    release_ids = {check.id for check in resolve_checks(normalized, "release", None)}
    package_ids = {
        check.id for check in normalized["checks"] if check.group == "package"
    }
    if not (full_ids | security_ids | package_ids) <= release_ids:
        raise ValueError(
            "release profile must include full, security, and every package-group check"
        )


def _visit_dependencies(
    check_id: str,
    checks: dict[str, dict[str, Any]],
    visiting: set[str],
    visited: set[str],
) -> None:
    if check_id in visiting:
        raise ValueError(f"dependency cycle includes {check_id!r}")
    if check_id in visited:
        return
    visiting.add(check_id)
    for dependency_id in checks[check_id]["dependencies"]:
        _visit_dependencies(dependency_id, checks, visiting, visited)
    visiting.remove(check_id)
    visited.add(check_id)


def _normalize(document: dict[str, Any]) -> dict[str, Any]:
    checks = tuple(
        CheckSpec(
            id=check["id"],
            category=check["category"],
            group=check["group"],
            argv=tuple(check["argv"]),
            cwd=check["cwd"],
            inputs=tuple(check["inputs"]),
            dependencies=tuple(check["dependencies"]),
            timeout_seconds=float(check["timeout_seconds"]),
            selection=check["selection"],
            tool=check["tool"],
            version=check["version"],
            include_regex=check.get("include_regex", ""),
            exclude_regex=check.get("exclude_regex", r"$^"),
            types=tuple(check.get("types", ())),
            types_or=tuple(check.get("types_or", ())),
            pass_filenames=check.get("pass_filenames", check["selection"] == "changed"),
            honors_status_contract=check.get("honors_status_contract", False),
        )
        for check in document["checks"]
    )
    preparations = tuple(
        PreparationSpec(
            id=preparation["id"],
            argv=tuple(preparation["argv"]),
            cwd=preparation["cwd"],
            inputs=tuple(preparation["inputs"]),
            outputs=tuple(preparation["outputs"]),
            groups=tuple(preparation["groups"]),
            timeout_seconds=float(preparation["timeout_seconds"]),
            tool=preparation["tool"],
            version=preparation["version"],
        )
        for preparation in document["candidate_preparations"]
    )
    return {
        "schema_version": document["schema_version"],
        "tools": normalized_tools(document),
        "checks": checks,
        "candidate_preparations": preparations,
        "profiles": {
            profile: tuple(check_ids)
            for profile, check_ids in document["profiles"].items()
        },
        "coverage_pending": {
            profile: tuple(obligations)
            for profile, obligations in document["coverage_pending"].items()
        },
    }


def load_registry(path: Path) -> dict[str, Any]:
    """Load a registry after structural and semantic validation."""
    document = read_validated_document(path)
    _validate_semantics(document)
    return _normalize(document) | lock_digest_for_registry(document, path)


def resolve_checks(
    registry: dict[str, Any], profile: str, group: str | None
) -> tuple[CheckSpec, ...]:
    """Resolve a profile dependency closure in stable declaration order."""
    if profile not in VALID_PROFILES or profile not in registry["profiles"]:
        raise ValueError(f"unknown profile: {profile}")
    if group is not None and group not in VALID_GROUPS:
        raise ValueError(f"unknown group: {group}")

    checks: tuple[CheckSpec, ...] = registry["checks"]
    check_by_id = {check.id: check for check in checks}
    selected_ids: set[str] = set()

    def include(check_id: str) -> None:
        if check_id in selected_ids:
            return
        check = check_by_id[check_id]
        for dependency_id in check.dependencies:
            include(dependency_id)
        selected_ids.add(check_id)

    for check_id in registry["profiles"][profile]:
        include(check_id)
    if group is not None:
        selected_ids = {
            check_id
            for check_id in selected_ids
            if check_by_id[check_id].group == group
        }

    order = {check.id: index for index, check in enumerate(checks)}
    indegree = {
        check_id: sum(
            dependency_id in selected_ids
            for dependency_id in check_by_id[check_id].dependencies
        )
        for check_id in selected_ids
    }
    ready = sorted(
        (check_id for check_id, degree in indegree.items() if degree == 0),
        key=order.__getitem__,
    )
    resolved: list[CheckSpec] = []
    while ready:
        check_id = ready.pop(0)
        resolved.append(check_by_id[check_id])
        for dependent_id in selected_ids:
            if check_id not in check_by_id[dependent_id].dependencies:
                continue
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                ready.append(dependent_id)
                ready.sort(key=order.__getitem__)
    return tuple(resolved)


def applicable_pending(
    registry: dict[str, Any],
    profile: str,
    group: str | None,
    checks: tuple[CheckSpec, ...],
) -> list[str]:
    """Return profile obligations owned by the exact selected check IDs."""
    obligations = registry["coverage_pending"][profile]
    if group is None:
        return list(obligations)
    all_ids = {check.id for check in registry["checks"]}
    selected_ids = {check.id for check in checks}

    def owner(obligation: str) -> str | None:
        matches = [
            check_id for check_id in all_ids if obligation.startswith(check_id + ":")
        ]
        return max(matches, key=len, default=None)

    return [item for item in obligations if owner(item) in selected_ids]
