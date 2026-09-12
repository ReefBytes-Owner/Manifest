"""Consistency checks for risk-based review guidance and activation policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_validation_configs_do_not_duplicate_code_audit_activation(
    repo_root: Path,
) -> None:
    yaml_config = yaml.safe_load(
        (repo_root / "configs/claude/config/validation_criteria.yml").read_text(
            encoding="utf-8"
        )
    )
    json_config = json.loads(
        (
            repo_root
            / "plugins/manifest-workspace/skills/parallel-agent/config/validation_criteria.json"
        ).read_text(encoding="utf-8")
    )

    for policy in (
        yaml_config["command_overrides"]["code-audit"],
        json_config["command_overrides"]["code-audit"],
    ):
        assert "auto_trigger" not in policy
        assert "trigger_patterns" not in policy


@pytest.mark.parametrize(
    "relative_path",
    (
        "AGENTS.md",
        "configs/claude/CLAUDE.md",
        "configs/gemini/GEMINI.md",
        "configs/cursor/rules/orchestration.mdc",
        "docs/commands/builtin.md",
    ),
)
def test_active_guides_use_semantic_risk_routing(
    repo_root: Path, relative_path: str
) -> None:
    source = (repo_root / relative_path).read_text(encoding="utf-8")

    assert "confirmed" in source or "risk-based" in source
    assert "ALWAYS Use Parallel Agents For" not in source
    assert "code-audit` auto-triggers on security-sensitive patterns" not in source
