#!/usr/bin/env python3
"""Emit deterministic composite tokens from actual local tool prerequisites."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import os
import re
import shutil
import stat
import sys
import sysconfig
from pathlib import Path, PurePath

_DISTRIBUTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_CONSOLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*\Z")
_EXECUTABLE_ALIASES = {"python": frozenset(("python", "python3"))}
_DESCRIPTOR_OPEN_SUPPORTED = (
    os.open in os.supports_dir_fd
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)
_COMMAND_PROBES = {
    "bash": (("--version",), re.compile(r"version\s+(\d+(?:\.\d+)+)", re.I)),
    "bats": (("--version",), re.compile(r"Bats\s+(\d+(?:\.\d+)+)", re.I)),
    "cargo": (("--version",), re.compile(r"cargo\s+(\d+(?:\.\d+)+)", re.I)),
    "eslint": (("--version",), re.compile(r"v(\d+(?:\.\d+)+)")),
    "gitleaks": (("version",), re.compile(r"(?:^|\s)v?(\d+(?:\.\d+)+)")),
    "golangci-lint": (
        ("version",),
        re.compile(r"version\s+(\d+(?:\.\d+)+)", re.I),
    ),
    "markdownlint-cli2": (
        ("--version",),
        re.compile(r"v(\d+(?:\.\d+)+)", re.I),
    ),
    "pyright": (("--version",), re.compile(r"pyright\s+(\d+(?:\.\d+)+)", re.I)),
    "ruff": (("--version",), re.compile(r"ruff\s+(\d+(?:\.\d+)+)", re.I)),
    "shellcheck": (("--version",), re.compile(r"version:\s*(\d+(?:\.\d+)+)", re.I)),
    "shfmt": (("--version",), re.compile(r"v?(\d+(?:\.\d+)+)")),
    "terraform": (("version",), re.compile(r"Terraform\s+v(\d+(?:\.\d+)+)")),
    "tflint": (
        ("--version",),
        re.compile(r"TFLint\s+version\s+(\d+(?:\.\d+)+)", re.I),
    ),
    "trivy": (("--version",), re.compile(r"Version:\s+(\d+(?:\.\d+)+)", re.I)),
    "uv": (("--version",), re.compile(r"uv\s+(\d+(?:\.\d+)+)", re.I)),
    "yamllint": (("--version",), re.compile(r"yamllint\s+(\d+(?:\.\d+)+)", re.I)),
}


def _load_helper(name: str):
    path = Path(__file__).resolve().with_name(f"{name}.py")
    specification = importlib.util.spec_from_file_location(f"_manifest_{name}", path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"trusted helper unavailable: {name}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


_PROCESS = _load_helper("tool_probe_process")
_METADATA = _load_helper("tool_probe_metadata")
_PROVENANCE = _load_helper("tool_probe_provenance")


class ProbeError(ValueError):
    """A local prerequisite could not be safely or conclusively identified."""


def _python_token() -> str:
    return f"python={sys.version_info.major}.{sys.version_info.minor}"


def _canonical_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _trusted_package_roots() -> tuple[Path, ...]:
    prefix = Path(sys.prefix).resolve(strict=True)
    roots = []
    for key in ("purelib", "platlib"):
        value = sysconfig.get_path(key)
        if value is None:
            continue
        root = Path(value).resolve(strict=True)
        if root.is_relative_to(prefix) and root not in roots:
            roots.append(root)
    if not roots:
        raise ProbeError("interpreter has no trusted package roots")
    return tuple(roots)


def _trusted_distribution(
    distribution_name: str,
) -> _METADATA.VerifiedDistribution:
    wanted = _canonical_distribution(distribution_name)
    roots = _trusted_package_roots()
    try:
        verified = [
            _METADATA.snapshot_distribution(candidate, roots)
            for candidate in importlib.metadata.distributions(
                path=[str(root) for root in roots]
            )
        ]
    except _METADATA.MetadataError as error:
        raise ProbeError(str(error)) from error
    matches = [
        item for item in verified if _canonical_distribution(item.name) == wanted
    ]
    if len(matches) != 1:
        raise ProbeError("distribution is not installed in the interpreter environment")
    return matches[0]


def _open_contained_file(relative: str) -> int:
    candidate = PurePath(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ProbeError("file-sha target must be a contained relative path")
    if not _DESCRIPTOR_OPEN_SUPPORTED:
        raise ProbeError("descriptor-contained file hashing is unsupported")
    common_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(".", common_flags | os.O_DIRECTORY)
    try:
        for part in candidate.parts[:-1]:
            child = os.open(part, common_flags | os.O_DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        target = os.open(candidate.parts[-1], common_flags, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(os.fstat(target).st_mode):
        os.close(target)
        raise ProbeError("file-sha target must be a regular file")
    return target


def _file_digest(relative: str) -> str:
    descriptor = _open_contained_file(relative)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    finally:
        os.close(descriptor)
    return digest


def _file_sha(relative: str) -> str:
    return f"{_python_token()};file-sha={_file_digest(relative)}"


def _distribution_component(distribution_name: str) -> str:
    if not _DISTRIBUTION.fullmatch(distribution_name):
        raise ProbeError("invalid distribution name")
    verified = _trusted_distribution(distribution_name)
    normalized = _canonical_distribution(verified.name)
    return f"distribution:{normalized}={verified.version}"


def _distribution_consoles(
    distribution_name: str, consoles: list[str]
) -> tuple[str, dict[str, Path]]:
    if not consoles or any(not _CONSOLE.fullmatch(console) for console in consoles):
        raise ProbeError("invalid console name")
    verified = _trusted_distribution(distribution_name)
    prefix = Path(sys.prefix).resolve(strict=True)
    scripts = Path(sysconfig.get_path("scripts")).resolve(strict=True)
    if not scripts.is_relative_to(prefix):
        raise ProbeError("interpreter scripts directory escapes its environment")
    try:
        resolved_consoles = _PROVENANCE.verify_consoles(
            verified, consoles, _trusted_package_roots()
        )
    except (_PROVENANCE.ProvenanceError, KeyError, TypeError, ValueError) as error:
        raise ProbeError(str(error)) from error
    if any(not path.is_relative_to(scripts) for path in resolved_consoles.values()):
        raise ProbeError("console escapes interpreter environment")
    for console, resolved in resolved_consoles.items():
        selected = shutil.which(console)
        if selected is None or Path(selected).resolve(strict=True) != resolved:
            raise ProbeError(
                f"console selection is not supplied by distribution: {console}"
            )
    normalized = _canonical_distribution(verified.name)
    return f"distribution:{normalized}={verified.version}", resolved_consoles


def _console_distribution(distribution_name: str, consoles: list[str]) -> str:
    component, _ = _distribution_consoles(distribution_name, consoles)
    return f"{_python_token()};{component}"


def _resolved_executable(probe: str, executable: str | None) -> str:
    value = executable or probe
    candidate = PurePath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ProbeError("command executable must be a name or contained relative path")
    if len(candidate.parts) > 1:
        raise ProbeError("command executable path does not match probe")
    allowed = _EXECUTABLE_ALIASES.get(probe, frozenset((probe,)))
    if value not in allowed:
        raise ProbeError("command executable name does not match probe")
    resolved = shutil.which(value)
    if resolved is None:
        raise ProbeError(f"command is not installed: {probe}")
    return resolved


def _run_probe(
    argv: list[str],
    timeout_seconds: float = _PROCESS.INTERNAL_TIMEOUT_SECONDS,
    *,
    executable: str | None = None,
    pass_fds: tuple[int, ...] = (),
) -> str:
    try:
        return _PROCESS.run_probe(
            argv, timeout_seconds, executable=executable, pass_fds=pass_fds
        )
    except _PROCESS.ProbeProcessError as error:
        raise ProbeError(str(error)) from error


def _python_runtime(executable: str) -> str:
    resolved = _resolved_executable("python", executable)
    output = _run_probe(
        [
            resolved,
            "-I",
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ]
    ).strip()
    if not re.fullmatch(r"\d+\.\d+", output):
        raise ProbeError("Python runtime returned an invalid version")
    return f"{_python_token()};runtime:python={output}"


def _command_version(probe: str, executable: str | None) -> str:
    return f"{_python_token()};{_command_component(probe, executable)}"


def _parsed_command_component(probe: str, resolved: str) -> str:
    configuration = _COMMAND_PROBES.get(probe)
    if configuration is None:
        raise ProbeError("command probe is not allowlisted")
    version_argv, pattern = configuration
    if Path(resolved).name != probe:
        raise ProbeError("resolved command name does not match probe")
    match = pattern.search(_run_probe([resolved, *version_argv]))
    if match is None:
        raise ProbeError("command returned an unrecognized version")
    return f"command:{probe}={match.group(1)}"


def _command_component(probe: str, executable: str | None = None) -> str:
    if executable == "./node_modules/.bin/bats" and probe == "bats":
        raise ProbeError("npm bats launcher unsupported pending Phase 3")
    resolved = _resolved_executable(probe, executable)
    return _parsed_command_component(probe, resolved)


def _console_command_components(specification: list[str]) -> tuple[str, str]:
    distribution, console, probe = specification
    if console != probe:
        raise ProbeError("console command name does not match probe")
    distribution_component, consoles = _distribution_consoles(distribution, [console])
    command_component = _parsed_command_component(probe, str(consoles[console]))
    return distribution_component, command_component


def _python_wrapper(
    files: list[str],
    distributions: list[str],
    commands: list[str],
    console_commands: list[list[str]],
) -> str:
    if not files and not distributions and not commands and not console_commands:
        raise ProbeError("python-wrapper requires at least one prerequisite")
    console_components = [
        component
        for specification in console_commands
        for component in _console_command_components(specification)
    ]
    components = [
        *(f"file:{relative}={_file_digest(relative)}" for relative in files),
        *(_distribution_component(name) for name in distributions),
        *console_components,
        *(_command_component(probe) for probe in commands),
    ]
    return ";".join((_python_token(), *components))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)
    file_sha = subparsers.add_parser("file-sha")
    file_sha.add_argument("relative_file")
    distribution = subparsers.add_parser("console-distribution")
    distribution.add_argument("distribution")
    distribution.add_argument("consoles", nargs="+")
    python_runtime = subparsers.add_parser("python-runtime")
    python_runtime.add_argument("executable")
    command = subparsers.add_parser("command-version")
    command.add_argument("probe", choices=sorted(_COMMAND_PROBES))
    command.add_argument("--executable")
    wrapper = subparsers.add_parser("python-wrapper")
    wrapper.add_argument("--file", action="append", default=[])
    wrapper.add_argument("--distribution", action="append", default=[])
    wrapper.add_argument(
        "--command", action="append", choices=sorted(_COMMAND_PROBES), default=[]
    )
    wrapper.add_argument("--console-command", action="append", nargs=3, default=[])
    return parser


def _probe(arguments: argparse.Namespace) -> str:
    if arguments.mode == "file-sha":
        return _file_sha(arguments.relative_file)
    if arguments.mode == "console-distribution":
        return _console_distribution(arguments.distribution, arguments.consoles)
    if arguments.mode == "python-runtime":
        return _python_runtime(arguments.executable)
    if arguments.mode == "command-version":
        return _command_version(arguments.probe, arguments.executable)
    return _python_wrapper(
        arguments.file,
        arguments.distribution,
        arguments.command,
        arguments.console_command,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        token = _probe(arguments)
    except (OSError, ProbeError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 3
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
