"""Trusted runtime selection for env-based Node command launchers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tools.project_checks import tool_versions


def _real_node() -> Path:
    selected = shutil.which("node", path=os.environ.get("PATH", os.defpath))
    if selected is None:
        pytest.skip("supported Node runtime is unavailable")
    return Path(selected)


def _write_js_launcher(path: Path, output: str, marker: Path) -> None:
    path.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('node:fs');\n"
        "const blocked = ['PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', "
        "'PYTHONINSPECT', 'NODE_PATH', 'NODE_OPTIONS'];\n"
        f"if (blocked.some((name) => name in process.env)) "
        f"fs.writeFileSync({str(marker)!r}, 'bad');\n"
        f"console.log({output!r});\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("probe", "version_output", "expected"),
    (
        ("markdownlint-cli2", "markdownlint-cli2 v0.23.0", "0.23.0"),
        ("eslint", "v9.18.0", "9.18.0"),
        ("pyright", "pyright 1.1.405", "1.1.405"),
    ),
)
def test_env_node_launcher_binds_real_runtime_and_strips_injection(
    tmp_path: Path,
    monkeypatch,
    probe: str,
    version_output: str,
    expected: str,
):
    node = _real_node()
    candidate = tmp_path / "candidate"
    launcher_bin = tmp_path / "provisioned" / "bin"
    candidate.mkdir()
    launcher_bin.mkdir(parents=True)
    injection_marker = tmp_path / "injection-survived"
    _write_js_launcher(launcher_bin / probe, version_output, injection_marker)
    monkeypatch.chdir(candidate)
    monkeypatch.setenv(
        "PATH", os.pathsep.join((str(launcher_bin), str(node.parent), os.defpath))
    )
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "NODE_PATH",
        "NODE_OPTIONS",
    ):
        monkeypatch.setenv(name, "attacker-value")

    component = tool_versions._command_component(probe)

    assert component == f"command:{probe}={expected}"
    assert not injection_marker.exists()


@pytest.mark.parametrize(
    "winner_shape", ("empty", "relative", "candidate", "symlink-to-candidate")
)
def test_env_node_launcher_blocks_untrusted_first_runtime_winner(
    tmp_path: Path, monkeypatch, winner_shape: str
):
    node = _real_node()
    candidate = tmp_path / "candidate"
    launcher_bin = tmp_path / "provisioned" / "bin"
    candidate.mkdir()
    launcher_bin.mkdir(parents=True)
    marker = tmp_path / "launcher-ran"
    _write_js_launcher(launcher_bin / "eslint", "v9.18.0", marker)
    if winner_shape == "empty":
        winner_entry = ""
        winner = candidate / "node"
    elif winner_shape == "relative":
        winner_entry = "relative-bin"
        winner = candidate / winner_entry / "node"
        winner.parent.mkdir()
    else:
        candidate_bin = candidate / "bin"
        candidate_bin.mkdir()
        winner = candidate_bin / "node"
        if winner_shape == "candidate":
            winner_entry = str(candidate_bin)
        else:
            linked_bin = tmp_path / "linked-bin"
            linked_bin.symlink_to(candidate_bin, target_is_directory=True)
            winner_entry = str(linked_bin)
    winner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    winner.chmod(0o755)
    monkeypatch.chdir(candidate)
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(
            (str(launcher_bin), winner_entry, str(node.parent), os.defpath)
        ),
    )

    with pytest.raises(tool_versions.ProbeError, match="node runtime"):
        tool_versions._command_component("eslint")

    assert not marker.exists()


def test_env_node_launcher_blocks_when_runtime_is_absent(tmp_path: Path, monkeypatch):
    candidate = tmp_path / "candidate"
    launcher_bin = tmp_path / "provisioned" / "bin"
    empty_bin = tmp_path / "empty-bin"
    candidate.mkdir()
    launcher_bin.mkdir(parents=True)
    empty_bin.mkdir()
    marker = tmp_path / "launcher-ran"
    _write_js_launcher(launcher_bin / "eslint", "v9.18.0", marker)
    monkeypatch.chdir(candidate)
    monkeypatch.setenv("PATH", os.pathsep.join((str(launcher_bin), str(empty_bin))))

    with pytest.raises(tool_versions.ProbeError, match="node runtime"):
        tool_versions._command_component("eslint")

    assert not marker.exists()
