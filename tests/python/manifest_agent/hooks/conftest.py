"""Fixture harness for `manifest hook`: a fake client piping recorded event
JSON into a real subprocess invocation, asserting exact stdout JSON plus
receipt side effects. Nothing here mocks `checks.process.run_argv`,
`fcntl`, or subprocess deadlines — every test drives the real mechanism."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[4] / "src"
REPO_ROOT = Path(__file__).resolve().parents[4]


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(root),
            *args,
        ],
        capture_output=True,
        check=True,
        env={
            "PATH": os.defpath,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        text=True,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class HookHarness:
    """A real git worktree + a real `config/project-checks.json` fixture +
    a real, isolated `XDG_STATE_HOME`. `invoke` execs the real
    `manifest hook <client> <event>` entry point as a subprocess."""

    root: Path
    project_config: Path
    state_home: Path
    marker: Path

    def env(self, **overrides: str) -> dict:
        base = {
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONPATH": os.pathsep.join((str(REPO_SRC), str(REPO_ROOT))),
            "PYTHONDONTWRITEBYTECODE": "1",
            "MANIFEST_HOOK_PROJECT_CONFIG": str(self.project_config),
            "XDG_STATE_HOME": str(self.state_home),
            "HOME": os.environ.get("HOME", str(self.state_home)),
        }
        base.update(overrides)
        return base

    def invoke(
        self, client: str, event: str, payload: dict | bytes | str, **env_overrides: str
    ) -> subprocess.CompletedProcess:
        data = (
            payload
            if isinstance(payload, (bytes, str))
            else json.dumps(payload)
        )
        return subprocess.run(
            [sys.executable, "-B", "-m", "manifest_agent", "hook", client, event],
            input=data if isinstance(data, bytes) else data.encode("utf-8"),
            capture_output=True,
            env=self.env(**env_overrides),
            timeout=60,
        )

    def receipts(self) -> list[dict]:
        directory = self.state_home / "manifest" / "hooks" / "receipts"
        if not directory.is_dir():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]


_TEMPLATE_PATH = Path(__file__).parent / "data" / "marker_project_checks.json"
_SINGLE_CHECK_TEMPLATE_PATH = (
    Path(__file__).parent / "data" / "single_check_project_checks.json"
)


def write_custom_check_project(config_path: Path, script_path: Path) -> None:
    """Render a one-check registry whose body is an arbitrary script --
    for tests that need a check body to do something specific (spawn a
    nested process, fail on a read-only path, ...) rather than the generic
    marker-append body `hook_harness` wires up by default."""
    rendered = (
        _SINGLE_CHECK_TEMPLATE_PATH.read_text(encoding="utf-8")
        .replace("__PYTHON__", json.dumps(sys.executable)[1:-1])
        .replace("__SCRIPT__", json.dumps(str(script_path))[1:-1])
        .replace("__PYVERSION__", platform.python_version())
    )
    config_path.write_text(rendered, encoding="utf-8")


def _write_project_checks(config_path: Path, marker: Path) -> None:
    """Load the marker-check registry template and splice in this
    interpreter and marker path — the structural document lives in
    data/, not as a source literal."""
    script = (
        f"import pathlib; p = pathlib.Path({str(marker)!r}); "
        "p.write_text((p.read_text() if p.exists() else '') + 'x')"
    )
    rendered = (
        _TEMPLATE_PATH.read_text(encoding="utf-8")
        .replace("__PYTHON__", json.dumps(sys.executable)[1:-1])
        .replace("__MARKER_SCRIPT__", json.dumps(script)[1:-1])
        .replace("__PYVERSION__", platform.python_version())
    )
    config_path.write_text(rendered, encoding="utf-8")


@pytest.fixture
def hook_harness(tmp_path: Path) -> HookHarness:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--quiet")
    (root / "a.txt").write_text("hi", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "base")

    marker = tmp_path / "check-marker.txt"
    config = tmp_path / "project-checks.json"
    _write_project_checks(config, marker)
    state_home = tmp_path / "xdg-state"
    state_home.mkdir()
    return HookHarness(root=root, project_config=config, state_home=state_home, marker=marker)
