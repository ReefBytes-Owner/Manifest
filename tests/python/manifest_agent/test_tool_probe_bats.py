"""Fail-closed contracts for the unpinned npm Bats launcher."""

from pathlib import Path

import pytest

from tests.python.manifest_agent.test_tool_version_security import _adapter


def _npm_bats(tmp_path: Path, target: str = "../bats/bin/bats") -> Path:
    binary = tmp_path / "node_modules/bats/bin/bats"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho 'Bats 1.11.1'\n", encoding="utf-8")
    binary.chmod(0o755)
    link = tmp_path / "node_modules/.bin/bats"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    return binary


def test_command_probe_blocks_unpinned_npm_bats_launcher(tmp_path: Path):
    _npm_bats(tmp_path)
    result = _adapter(
        "command-version",
        "bats",
        "--executable",
        "./node_modules/.bin/bats",
        cwd=tmp_path,
    )
    assert result.returncode == 3
    assert "Phase 3" in result.stderr


@pytest.mark.parametrize("replacement", ("target", "parent"))
def test_npm_bats_execution_is_bound_across_replacement(
    tmp_path: Path, monkeypatch, replacement: str
):
    from tools.project_checks import tool_versions

    binary = _npm_bats(tmp_path)
    marker = tmp_path / "attacker-executed"
    real_run = tool_versions._run_probe

    def replace_then_run(argv, *args, **kwargs):
        if replacement == "target":
            binary.rename(binary.with_name("reviewed-bats"))
            binary.write_text(
                f"#!/bin/sh\ntouch {marker}\necho 'Bats 1.11.1'\n", encoding="utf-8"
            )
            binary.chmod(0o755)
        else:
            modules = tmp_path / "node_modules"
            modules.rename(tmp_path / "reviewed-node-modules")
            attacker = tmp_path / "node_modules/bats/bin/bats"
            attacker.parent.mkdir(parents=True)
            attacker.write_text(
                f"#!/bin/sh\ntouch {marker}\necho 'Bats 1.11.1'\n", encoding="utf-8"
            )
            attacker.chmod(0o755)
        return real_run(argv, *args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tool_versions, "_run_probe", replace_then_run)
    with pytest.raises(tool_versions.ProbeError, match="Phase 3"):
        tool_versions._command_component("bats", "./node_modules/.bin/bats")
    assert not marker.exists()


@pytest.mark.parametrize(
    "content",
    (
        "",
        "#!/bin/ba",
        '#!/usr/bin/env bash\nroot=$(cd "$(dirname "$0")/../.." && pwd)\n'
        "touch attacker-executed\necho 'Bats 1.11.1'\n",
    ),
)
def test_npm_bats_launcher_forms_block_typed_without_fd_leak(
    tmp_path: Path, content: str
):
    binary = _npm_bats(tmp_path)
    binary.write_text(content, encoding="utf-8")
    binary.chmod(0o755)
    descriptor_root = Path("/dev/fd")
    before = len(tuple(descriptor_root.iterdir()))
    result = _adapter(
        "command-version",
        "bats",
        "--executable",
        "./node_modules/.bin/bats",
        cwd=tmp_path,
    )
    assert result.returncode == 3
    assert "BLOCKED:" in result.stderr
    assert not (tmp_path / "attacker-executed").exists()
    assert len(tuple(descriptor_root.iterdir())) == before


@pytest.mark.parametrize("failure", ("escape", "mismatch", "non-executable"))
def test_command_probe_rejects_invalid_npm_bats_target(tmp_path: Path, failure: str):
    if failure == "escape":
        outside = tmp_path.parent / f"outside-bats-{tmp_path.name}"
        outside.write_text("#!/bin/sh\necho 'Bats 1.11.1'\n", encoding="utf-8")
        outside.chmod(0o755)
        link = tmp_path / "node_modules/.bin/bats"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
    elif failure == "mismatch":
        wrong = tmp_path / "node_modules/not-bats/bin/bats"
        wrong.parent.mkdir(parents=True)
        wrong.write_text("#!/bin/sh\necho 'Bats 1.11.1'\n", encoding="utf-8")
        wrong.chmod(0o755)
        link = tmp_path / "node_modules/.bin/bats"
        link.parent.mkdir(parents=True)
        link.symlink_to("../not-bats/bin/bats")
    else:
        binary = _npm_bats(tmp_path)
        binary.chmod(0o644)
    result = _adapter(
        "command-version",
        "bats",
        "--executable",
        "./node_modules/.bin/bats",
        cwd=tmp_path,
    )
    assert result.returncode == 3
