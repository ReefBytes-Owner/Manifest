#!/usr/bin/env python3
"""Emit deterministic composite tokens from actual local tool prerequisites.

Thin probes only (spec amendment 2026-09-09): this module verifies installed
distribution versions and allowlisted command versions. It does not attempt
process-family supervision, RECORD/tamper detection, or console-provenance
verification; that hardened supervision layer moves to Phase 4. Pins come
from reviewed config (pyproject.toml, .pre-commit-config.yaml), never from
whatever happens to be installed locally.

Trust boundary: the registry's `version_argv` entries are candidate-relative
and run with the candidate as cwd, so this probe is itself candidate-controlled
— the same trust class as the check bodies and the workflow file (see the
threat-model note in `ci_context.py`). A version pin verified here is
therefore a drift control (catching accidental skew from the reviewed
config), not a security control: a compromised candidate can supply a probe
that reports whatever version it likes.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import re
import shutil
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import PurePath

_DISTRIBUTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_EXECUTABLE_ALIASES = {"python": frozenset(("python", "python3"))}
_PROBE_TIMEOUT_SECONDS = 10.0
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
_NO_PREREQUISITES_TOKEN = "ok"


class ProbeError(ValueError):
    """A local prerequisite could not be safely or conclusively identified."""


def _canonical_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _distribution_component(distribution_name: str) -> str:
    if not _DISTRIBUTION.fullmatch(distribution_name):
        raise ProbeError("invalid distribution name")
    try:
        version = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError as error:
        raise ProbeError(
            f"distribution is not installed: {distribution_name}"
        ) from error
    normalized = _canonical_distribution(distribution_name)
    return f"distribution:{normalized}={version}"


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


def _probe_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONNOUSERSITE": "1",
    }


def _run_probe(argv: list[str], timeout_seconds: float = _PROBE_TIMEOUT_SECONDS) -> str:
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=_probe_environment(),
            text=True,
        )
    except OSError as error:
        raise ProbeError(f"version probe could not start: {error}") from error
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise ProbeError(f"version probe timed out after {timeout_seconds}s") from error
    if process.returncode != 0:
        detail = stderr.strip() or stdout.strip()
        raise ProbeError(f"version probe exited {process.returncode}: {detail}")
    return stdout


def _parsed_command_component(probe: str, resolved: str) -> str:
    configuration = _COMMAND_PROBES.get(probe)
    if configuration is None:
        raise ProbeError("command probe is not allowlisted")
    version_argv, pattern = configuration
    if PurePath(resolved).name != probe:
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


def _python_wrapper(distributions: list[str], commands: list[str]) -> str:
    components = [
        *(_distribution_component(name) for name in distributions),
        *(_command_component(probe) for probe in commands),
    ]
    if not components:
        return _NO_PREREQUISITES_TOKEN
    return ";".join(components)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)
    distribution = subparsers.add_parser("distribution-version")
    distribution.add_argument("distribution")
    command = subparsers.add_parser("command-version")
    command.add_argument("probe", choices=sorted(_COMMAND_PROBES))
    command.add_argument("--executable")
    wrapper = subparsers.add_parser("python-wrapper")
    wrapper.add_argument("--distribution", action="append", default=[])
    wrapper.add_argument(
        "--command", action="append", choices=sorted(_COMMAND_PROBES), default=[]
    )
    return parser


def _probe(arguments: argparse.Namespace) -> str:
    if arguments.mode == "distribution-version":
        return _distribution_component(arguments.distribution)
    if arguments.mode == "command-version":
        return _command_component(arguments.probe, arguments.executable)
    return _python_wrapper(arguments.distribution, arguments.command)


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
