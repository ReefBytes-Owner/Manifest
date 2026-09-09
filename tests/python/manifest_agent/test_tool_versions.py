"""Behavior tests for deterministic project-check tool version probes."""

from __future__ import annotations

import hashlib
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


def _python_token() -> str:
    return f"python={sys.version_info.major}.{sys.version_info.minor}"


def test_tool_version_adapter_probes_wrapper_runtime_and_file_sha(tmp_path: Path):
    target = tmp_path / "reviewed.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    expected_sha = hashlib.sha256(target.read_bytes()).hexdigest()

    result = _adapter("file-sha", "reviewed.py", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{_python_token()};file-sha={expected_sha}"


def test_tool_version_adapter_probes_console_distribution(tmp_path: Path):
    expected = importlib.metadata.version("pytest")

    result = _adapter("console-distribution", "pytest", "pytest", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        f"{_python_token()};distribution:pytest={expected}"
    )


def test_tool_version_adapter_rejects_shadow_console(tmp_path: Path):
    shadow = tmp_path / "pytest"
    shadow.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shadow.chmod(0o755)

    result = _adapter(
        "console-distribution",
        "pytest",
        "pytest",
        cwd=tmp_path,
        path=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
    )

    assert result.returncode != 0


def test_tool_version_adapter_probes_python_runtime(tmp_path: Path):
    result = _adapter("python-runtime", "python3", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.strip()
        == f"{_python_token()};runtime:python={sys.version_info.major}.{sys.version_info.minor}"
    )


def test_tool_version_adapter_probes_allowlisted_command(tmp_path: Path):
    direct = subprocess.run(
        ["bash", "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    match = re.search(r"version\s+(\d+(?:\.\d+)+)", direct.stdout, re.IGNORECASE)
    assert match is not None

    result = _adapter("command-version", "bash", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{_python_token()};command:bash={match.group(1)}"


def test_python_wrapper_probe_covers_file_distribution_and_command(tmp_path: Path):
    target = tmp_path / "reviewed.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    expected_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    distribution_version = importlib.metadata.version("pytest")
    direct = subprocess.run(
        ["bash", "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    match = re.search(r"version\s+(\d+(?:\.\d+)+)", direct.stdout, re.IGNORECASE)
    assert match is not None

    result = _adapter(
        "python-wrapper",
        "--file",
        "reviewed.py",
        "--distribution",
        "pytest",
        "--command",
        "bash",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        f"{_python_token()};file:reviewed.py={expected_sha};"
        f"distribution:pytest={distribution_version};command:bash={match.group(1)}"
    )


def test_distribution_tokens_use_stable_canonical_names(tmp_path: Path):
    target = tmp_path / "reviewed.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")

    result = _adapter(
        "python-wrapper",
        "--file",
        "reviewed.py",
        "--distribution",
        "pytest_asyncio",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert ";distribution:pytest-asyncio=" in result.stdout


def test_tool_version_adapter_rejects_unsafe_files_and_fake_tokens(tmp_path: Path):
    outside = tmp_path.parent / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")

    traversal = _adapter("file-sha", "../outside.py", cwd=tmp_path)
    fake = _adapter("file-sha", "missing.py", "9.9.9", cwd=tmp_path)
    unknown_command = _adapter("command-version", "printf", cwd=tmp_path)

    assert traversal.returncode != 0
    assert fake.returncode != 0
    assert unknown_command.returncode != 0
    assert "9.9.9" not in fake.stdout


def test_composite_probe_blocks_wrong_wrapper_runtime_with_correct_inner_pin(
    tmp_path: Path,
):
    distribution_version = importlib.metadata.version("pytest")
    tool = {
        "executable": "pytest",
        "version_argv": (
            sys.executable,
            str(VERSION_ADAPTER),
            "console-distribution",
            "pytest",
            "pytest",
        ),
        "expected_version": (f"python=0.0;distribution:pytest={distribution_version}"),
        "required_modules": (),
    }

    outcome = _preflight_tool(tool, cwd=tmp_path, env={"PATH": os.environ["PATH"]})

    assert outcome[0].returncode == 0
    assert outcome[1] is False
    assert f"distribution:pytest={distribution_version}" in outcome[0].stdout
    assert _python_token() in outcome[0].stdout
