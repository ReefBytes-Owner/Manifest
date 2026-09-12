"""Flat-mirror packaging tests for code-quality sidecar references."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REFACTOR_SKILLS = (
    "python-refactor",
    "node-refactor",
    "go-refactor",
    "shell-refactor",
    "terraform-refactor",
)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_review_contract_resolves_after_real_flat_mirror_generation(
    repo_root: Path, tmp_path: Path
) -> None:
    plugin = repo_root / "plugins/manifest-code-quality"
    staged_plugin = tmp_path / "plugins/manifest-code-quality"
    shutil.copytree(plugin, staged_plugin)
    script_source = repo_root / "configs/claude/scripts/generate_skill_mirror.sh"
    script = tmp_path / "configs/claude/scripts/generate_skill_mirror.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(script_source, script)

    result = subprocess.run(
        ["bash", str(script), "--root", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    canonical = plugin / "skills/refactor/references/review-escalation.md"
    for skill_name in REFACTOR_SKILLS:
        skill = tmp_path / f".apm/skills/{skill_name}/SKILL.md"
        source = skill.read_text(encoding="utf-8")
        link = re.search(
            r"\[review escalation contract\]\(([^)]+)\)", source, re.IGNORECASE
        )
        assert link is not None
        reference = (skill.parent / link.group(1)).resolve()
        assert reference.is_file()
        assert reference.read_bytes() == canonical.read_bytes()
