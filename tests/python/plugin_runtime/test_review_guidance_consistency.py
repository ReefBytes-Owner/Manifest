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
        "docs/configuration/commands.md",
        "docs/getting-started/using-commands.md",
        "docs/diagrams/validation.md",
    ),
)
def test_active_guides_use_semantic_risk_routing(
    repo_root: Path, relative_path: str
) -> None:
    source = (repo_root / relative_path).read_text(encoding="utf-8")

    assert "confirmed" in source or "risk-based" in source
    assert "ALWAYS Use Parallel Agents For" not in source
    assert "code-audit` auto-triggers on security-sensitive patterns" not in source
    assert "auto-triggers on security-sensitive code" not in source
    assert "Code quality skill auto-triggers" not in source
    assert "The `code-audit` skill auto-triggers" not in source
    assert "skill_file_lines" not in source or "advisory" in source
    assert ">200 lines modified" not in source


def test_shared_dispatch_guidance_has_only_the_risk_gate(repo_root: Path) -> None:
    source = (repo_root / "configs/claude/references/sub-agent-dispatch.md").read_text(
        encoding="utf-8"
    )

    assert "independent_units >= 3" not in source
    assert "≥3 independent units" not in source
    assert "trust-boundary change" in source
    assert "Counts of files" in source


def test_shared_dispatch_scopes_risk_gate_to_review(
    repo_root: Path,
) -> None:
    source = (repo_root / "configs/claude/references/sub-agent-dispatch.md").read_text(
        encoding="utf-8"
    )

    assert "review and cross-verification" in source
    assert "workload decomposition" in source
    for skill_name in ("docs-all", "docs-improve", "issue-prioritize"):
        assert skill_name in source


def test_refactor_dispatches_every_detected_ecosystem_sequentially(
    repo_root: Path,
) -> None:
    source = (
        repo_root / "plugins/manifest-code-quality/skills/refactor/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "every matching engine sequentially" in source
    assert "aggregate" in source


@pytest.mark.parametrize("command", ("python-refactor", "shell-refactor"))
def test_refactor_cross_verification_is_conditional_on_escalation(
    repo_root: Path, command: str
) -> None:
    config = yaml.safe_load(
        (repo_root / "configs/claude/config/validation_criteria.yml").read_text(
            encoding="utf-8"
        )
    )
    policy = config["command_overrides"][command]
    assert "cross_verification" not in policy["tier1_checks"]
    assert policy["conditional_tier1_checks"]["review_mode"]["escalated"] == [
        "cross_verification"
    ]
    assert "consensus_threshold" not in policy
    assert policy["conditional_consensus"]["review_mode"]["escalated"] == {
        "threshold": 0.80 if command == "python-refactor" else 0.75,
        "action": {
            "high": "auto_proceed",
            "medium": "show_disagreements",
            "low": "block_and_escalate",
        },
    }


def test_shell_refactor_review_never_installs_or_executes_checkout_code(
    repo_root: Path,
) -> None:
    source = (
        repo_root / "plugins/manifest-code-quality/skills/shell-refactor/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "npm install -g bats" not in source
    assert 'docker run --rm -v "$PWD:/work"' not in source
    assert "unavailable" in source
