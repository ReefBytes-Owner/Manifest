"""Behavior tests for deterministic project-check tool version probes.

Thin probes only (spec amendment 2026-09-09): distribution and allowlisted
command probes, plus a fixed sentinel for tools with no external
prerequisite. Process-family supervision and RECORD/console provenance
belong to Phase 4 and are not exercised here.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import subprocess
import sys
from pathlib import Path

from manifest_agent.checks.runner import _preflight_tool

ROOT = Path(__file__).resolve().parents[3]
VERSION_ADAPTER = ROOT / "tools/project_checks/tool_versions.py"


def _adapter(
    *argv: str, cwd: Path, path: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERSION_ADAPTER), *argv],
        cwd=cwd,
        env={
            "HOME": str(cwd),
            "PATH": path or os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_tool_version_adapter_probes_distribution_version(tmp_path: Path):
    expected = importlib.metadata.version("pytest")

    result = _adapter("distribution-version", "pytest", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"distribution:pytest={expected}"


def test_tool_version_adapter_rejects_missing_distribution(tmp_path: Path):
    result = _adapter("distribution-version", "not-a-real-distribution", cwd=tmp_path)

    assert result.returncode == 3
    assert "distribution is not installed" in result.stderr


def _bash_version() -> str:
    direct = subprocess.run(
        ["bash", "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    match = re.search(r"version\s+(\d+(?:\.\d+)+)", direct.stdout, re.IGNORECASE)
    assert match is not None
    return match.group(1)


def test_tool_version_adapter_probes_allowlisted_command(tmp_path: Path):
    version = _bash_version()

    result = _adapter("command-version", "bash", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"command:bash={version}"


def test_tool_version_adapter_rejects_unknown_command_and_missing_tool(tmp_path: Path):
    unknown_command = _adapter("command-version", "printf", cwd=tmp_path)
    missing_tool = _adapter("command-version", "bash", cwd=tmp_path, path=str(tmp_path))

    assert unknown_command.returncode != 0
    assert missing_tool.returncode == 3
    assert "command is not installed" in missing_tool.stderr


def test_python_wrapper_probe_covers_distribution_and_command(tmp_path: Path):
    distribution_version = importlib.metadata.version("pytest")
    version = _bash_version()

    result = _adapter(
        "python-wrapper",
        "--distribution",
        "pytest",
        "--command",
        "bash",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        f"distribution:pytest={distribution_version};command:bash={version}"
    )


def test_python_wrapper_with_no_prerequisites_returns_fixed_sentinel(tmp_path: Path):
    result = _adapter("python-wrapper", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_distribution_tokens_use_stable_canonical_names(tmp_path: Path):
    result = _adapter(
        "python-wrapper",
        "--distribution",
        "pytest_asyncio",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("distribution:pytest-asyncio=")


def _pytest_distribution_tool(expected_version: str) -> dict:
    return {
        "executable": "pytest",
        "version_argv": (
            sys.executable,
            str(VERSION_ADAPTER),
            "distribution-version",
            "pytest",
        ),
        "expected_version": expected_version,
        "required_modules": (),
    }


def test_preflight_tool_matches_expected_version_exactly(tmp_path: Path):
    distribution_version = importlib.metadata.version("pytest")
    tool = _pytest_distribution_tool(f"distribution:pytest={distribution_version}")

    outcome = _preflight_tool(tool, cwd=tmp_path, env={"PATH": os.environ["PATH"]})

    assert outcome[0].returncode == 0
    assert outcome[1] is True
    assert f"distribution:pytest={distribution_version}" in outcome[0].stdout


def test_preflight_tool_blocks_on_pin_drift(tmp_path: Path):
    distribution_version = importlib.metadata.version("pytest")
    tool = _pytest_distribution_tool(
        f"distribution:pytest={distribution_version}.drift"
    )

    outcome = _preflight_tool(tool, cwd=tmp_path, env={"PATH": os.environ["PATH"]})

    assert outcome[0].returncode == 0
    assert outcome[1] is False
