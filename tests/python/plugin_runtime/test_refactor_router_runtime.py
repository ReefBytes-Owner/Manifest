"""Installed /refactor routing regression coverage."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture
def code_quality_bundle(repo_root: Path) -> Path:
    return repo_root / "plugins" / "manifest-code-quality"


def _review_config(repo_root: Path) -> dict:
    return yaml.safe_load(
        (repo_root / "configs/claude/config/command_config.yml").read_text(
            encoding="utf-8"
        )
    )


def _resolved_local_routing_sources(skill: Path) -> tuple[str, ...]:
    """Load the complete router and every skill-local routing reference it uses."""
    source = skill.read_text(encoding="utf-8")
    references = re.findall(r"\]\((references/[^)#]+)\)", source)
    assert references, f"{skill}: no skill-local routing references"
    return (
        source,
        *(
            (skill.parent / ref).resolve().read_text(encoding="utf-8")
            for ref in references
        ),
    )


def _assert_no_count_based_router_activation(sources: tuple[str, ...]) -> None:
    assert not re.search(
        r"sub-agent-dispatch\.md|architectural|>200-line|≥3 independent units|three or more language|independent_units >=",
        "\n".join(sources).lower(),
    )


def test_refactor_router_guidance_defaults_single_agent_and_escalates_risk(
    code_quality_bundle: Path, repo_root: Path, tmp_path: Path
) -> None:
    installed = tmp_path / "installed/manifest-code-quality"
    shutil.copytree(code_quality_bundle, installed)
    skill = installed / "skills/refactor/SKILL.md"
    source, *references = _resolved_local_routing_sources(skill)
    policy = _review_config(repo_root)["tool_policies"]["refactor"]
    expected_trigger = " OR ".join(
        _review_config(repo_root)["review_escalation"]["conditions"]
    )
    routing = re.search(r"(?ms)^## Review routing\s*$\n(.*?)(?=^## |\Z)", source)
    dispatch = re.search(r"(?ms)^## Sub-agent dispatch\s*$\n(.*?)(?=^## |\Z)", source)
    assert routing is not None and dispatch is not None
    review_link = re.search(
        r"\[review escalation contract\]\(([^)]+)\)", routing.group(1)
    )
    dispatch_link = re.search(
        r"\[dispatch mechanics\]\((references/[A-Za-z0-9_-]+-dispatch\.md)\)",
        dispatch.group(1),
    )

    assert "## Routing outcomes" not in source
    assert policy["parallel_agents"] == "conditional"
    assert policy["trigger_condition"] == expected_trigger
    assert policy["subagent_trigger"] == expected_trigger
    assert review_link is not None
    assert dispatch_link is not None
    assert "Python, Go,\nand Shell therefore remains single-agent" in routing.group(1)
    assert "multi-language target remains single-agent" in references[0]
    assert "trust-boundary change" in references[0]
    assert "obtain an independent review" in dispatch.group(1)
    assert (
        "only when the investigation has genuinely independent analysis tracks"
        in " ".join(dispatch.group(1).split())
    )
    assert "pinned `sonnet` model" in dispatch.group(1)
    assert "does not re-dispatch" in " ".join(references[1].split())
    _assert_no_count_based_router_activation((source, *references))

    with pytest.raises(AssertionError):
        _assert_no_count_based_router_activation(
            (
                source
                + "\n## Historical notes\nThree or more languages require dispatch.\n",
                *references,
            )
        )
