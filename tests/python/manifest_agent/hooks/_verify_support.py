"""Shared fixtures for the `manifest hook verify` tests: a fake client
rendered onto a real PATH, an isolated matrix, and an isolated fixture tree."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from manifest_agent.hooks import verify

REPO_SRC = Path(__file__).resolve().parents[4] / "src"
REPO_ROOT_MARKER = Path(__file__).resolve().parents[4]

_FAKE_CLIENT_TEMPLATE = Path(__file__).parent / "data" / "fake_client.py.tmpl"


def _write_fake_client(
    bin_dir: Path, *, version: str, shape: dict, name: str = "fake-client"
) -> Path:
    """Render the fake-client template -- a real, executable script placed on
    a real PATH; it is never invoked as anything but a subprocess."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    rendered = (
        _FAKE_CLIENT_TEMPLATE.read_text(encoding="utf-8")
        .replace("__PYTHON__", sys.executable)
        .replace("__VERSION__", version)
        .replace("__SHAPE_LITERAL__", repr(json.dumps(shape)))
    )
    script.write_text(rendered, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _write_matrix(
    path: Path, *, executable_name: str, protocol_probe: bool, fixture_dir: str
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_labels_status": "unresolved -- owner input required",
                "clients": {
                    "claude_code": {
                        "name": "claude_code",
                        "executable_candidates": [executable_name],
                        "version_argv": ["--version"],
                        "version_pattern": r"(\d+\.\d+\.\d+)",
                        "protocol_probe_argv": ["--emit-event"]
                        if protocol_probe
                        else None,
                        "verified_version": None,
                        "verified_at": None,
                        "fixture_dir": fixture_dir,
                        "model_labels": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _write_fixture(repo_root: Path, fixture_dir: str, shape: dict) -> None:
    directory = repo_root / fixture_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Sample.json").write_text(json.dumps(shape), encoding="utf-8")
    (directory / "SOURCE.md").write_text(
        "# fixture -- source: unverified\n", encoding="utf-8"
    )


SHAPE = {"session_id": "s", "tool_name": "Write", "tool_input": {"file_path": "a.txt"}}


@pytest.fixture
def verify_env(tmp_path: Path):
    """A real bin dir with a fake client on a real PATH, a real isolated
    repo_root, and a real matrix file -- everything `verify.VerifyConfig`
    needs, built fresh per test."""
    bin_dir = tmp_path / "bin"
    repo_root = tmp_path / "repo"
    matrix_path = tmp_path / "hook-clients.json"
    fixture_dir = "tests/fixtures/hooks/claude_code/unverified"
    return {
        "bin_dir": bin_dir,
        "repo_root": repo_root,
        "matrix_path": matrix_path,
        "fixture_dir": fixture_dir,
    }


def _config(env: dict, *, path_env: str | None = None) -> verify.VerifyConfig:
    return verify.VerifyConfig(
        repo_root=env["repo_root"],
        matrix_path=env["matrix_path"],
        resolver=verify.default_resolver(path_env or str(env["bin_dir"])),
        timeout_seconds=15.0,
    )
