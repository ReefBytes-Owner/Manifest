"""Shared registry fixtures for focused schema and semantic tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest


def _registry_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "tools": {
            "python": {
                "executable": "python",
                "version_argv": ["python", "--version"],
                "expected_version": "3.14.0",
                "required_modules": [],
            }
        },
        "checks": [
            {
                "id": "lint.a",
                "category": "lint",
                "group": "lint",
                "argv": ["python", "-m", "example_a"],
                "cwd": ".",
                "inputs": ["src/**/*.py"],
                "dependencies": [],
                "timeout_seconds": 10,
                "selection": "changed",
                "pass_filenames": False,
                "tool": "python",
                "version": "3.14.0",
            },
            {
                "id": "lint.b",
                "category": "lint",
                "group": "lint",
                "argv": ["python", "-m", "example_b"],
                "cwd": ".",
                "inputs": ["src"],
                "dependencies": ["lint.a"],
                "timeout_seconds": 20.5,
                "selection": "project",
                "pass_filenames": False,
                "tool": "python",
                "version": "3.14.0",
            },
        ],
        "candidate_preparations": [],
        "profiles": {
            "quick": ["lint.a"],
            "full": ["lint.b"],
            "security": ["lint.a"],
            "release": ["lint.b"],
        },
        "coverage_pending": {
            "quick": [],
            "full": [],
            "security": [],
            "release": [],
        },
    }


@pytest.fixture(name="registry_file")
def _registry_file(tmp_path: Path) -> Callable[..., Path]:
    counter = 0

    def write_registry(**overrides: object) -> Path:
        nonlocal counter
        counter += 1
        registry = _registry_document()
        registry.update(overrides)
        path = tmp_path / f"registry-{counter}.json"
        path.write_text(json.dumps(registry), encoding="utf-8")
        return path

    return write_registry


def _check(
    check_id: str,
    *,
    group: str = "lint",
    dependencies: list[str] | None = None,
    category: str = "lint",
    selection: str = "project",
    tool: str = "python",
    version: str = "3.14.0",
) -> dict[str, object]:
    return {
        "id": check_id,
        "category": category,
        "group": group,
        "argv": ["python", "-m", check_id],
        "cwd": ".",
        "inputs": ["src"],
        "dependencies": dependencies or [],
        "timeout_seconds": 10,
        "selection": selection,
        "pass_filenames": False,
        "tool": tool,
        "version": version,
    }


def _preparation(**overrides: object) -> dict[str, object]:
    preparation: dict[str, object] = {
        "id": "prepare.skills",
        "argv": ["python", "tools/generate.py", ".apm/skills"],
        "cwd": ".",
        "inputs": ["plugins"],
        "outputs": [".apm/skills"],
        "groups": ["lint", "structure"],
        "timeout_seconds": 30,
        "tool": "python",
        "version": "3.14.0",
        "network": False,
        "installs_dependencies": False,
    }
    preparation.update(overrides)
    return preparation


def _python_and_tool(executable: str) -> dict[str, dict[str, object]]:
    return {
        "python": {
            "executable": "python",
            "version_argv": ["python", "--version"],
            "expected_version": "3.14.0",
            "required_modules": [],
        },
        "reviewed": {
            "executable": executable,
            "version_argv": [executable, "--version"],
            "expected_version": "1.0.0",
            "required_modules": [],
        },
    }


def _argv_entry_kwargs(
    target: str, executable: str, argv: list[str]
) -> dict[str, object]:
    tools = _python_and_tool(executable)
    if target == "version":
        tools["reviewed"]["version_argv"] = argv
        return {"tools": tools}
    if target == "check":
        check = _check("lint.a", tool="reviewed", version="1.0.0")
        check["argv"] = argv
        return {
            "tools": tools,
            "checks": [check, _check("lint.b", dependencies=["lint.a"])],
        }
    return {
        "tools": tools,
        "candidate_preparations": [
            _preparation(argv=argv, tool="reviewed", version="1.0.0")
        ],
    }
