"""Isolation tests for the installed manifest-security bundle."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from manifest_agent.contracts import CapabilityTier, load_contract


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture
def security_bundle(repo_root: Path) -> Path:
    return repo_root / "plugins/manifest-security"


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "HOME": str(home),
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
        "UV_NO_NETWORK": "1",
    }


def _run(
    script: Path, *args: str, env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("command", ["ci_platform.sh", "git_platform.sh"])
def test_security_runtime_commands_are_packaged_and_executable(
    security_bundle: Path, command: str
) -> None:
    path = security_bundle / "runtime/bin" / command
    assert path.is_file()
    assert os.access(path, os.X_OK)


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("github-actions", "github-actions"),
        ("gitlab-ci", "gitlab-ci"),
        ("none", "none"),
    ],
)
def test_security_ci_platform_preserves_the_shared_behavior_contract(
    security_bundle: Path,
    tmp_path: Path,
    override: str,
    expected: str,
) -> None:
    env = {**_isolated_env(tmp_path), "MANIFEST_CI_PLATFORM": override}

    result = _run(security_bundle / "runtime/bin/ci_platform.sh", env=env, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    ("override", "expected"),
    [("github", "github"), ("gitlab", "gitlab"), ("git", "git")],
)
def test_security_git_platform_preserves_the_shared_behavior_contract(
    security_bundle: Path,
    tmp_path: Path,
    override: str,
    expected: str,
) -> None:
    env = {**_isolated_env(tmp_path), "MANIFEST_GIT_PLATFORM": override}

    result = _run(
        security_bundle / "runtime/bin/git_platform.sh", env=env, cwd=tmp_path
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_security_bundle_runs_without_ops_or_a_deployed_home(
    security_bundle: Path, tmp_path: Path
) -> None:
    installed = tmp_path / "installed/manifest-security"
    shutil.copytree(security_bundle, installed)
    repo = tmp_path / "repo"
    workflow = repo / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: ci\n", encoding="utf-8")

    result = _run(
        installed / "runtime/bin/ci_platform.sh",
        env=_isolated_env(tmp_path),
        cwd=repo,
    )

    assert not (tmp_path / "installed/manifest-ops").exists()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "github-actions"
    assert ".claude" not in result.stderr


def test_security_references_and_skill_interfaces_are_bundle_local(
    security_bundle: Path,
) -> None:
    references = security_bundle / "runtime/references"
    assert (references / "ci/gitlab-ci-triggers.md").is_file()
    assert (references / "antipatterns.md").is_file()
    assert (references / "code-constitution.md").is_file()

    combined = ""
    forbidden = (
        "configs/claude",
        "~/.claude/scripts",
        "~/.claude/references",
        "manifest parallel-agent",
        "parallel_agent.py",
        "learning_capture.sh",
        "plugins/manifest-ops",
    )
    for skill in security_bundle.glob("skills/*/SKILL.md"):
        source = skill.read_text(encoding="utf-8")
        combined += source
        for marker in forbidden:
            assert marker not in source, f"{skill}: forbidden runtime marker {marker}"

    assert "../../runtime/bin/ci_platform.sh" in combined
    assert "../../runtime/references/ci/gitlab-ci-triggers.md" in combined
    assert "../../runtime/references/antipatterns.md" in combined
    assert "../../runtime/references/code-constitution.md" in combined
    # Qualified form since the `[[skill:]]` convention was retired
    # (2026-08-27, Phase 0 item 4 option (b)): nothing rendered the token,
    # so every one shipped literal to the model.
    assert "manifest-workspace:parallel-agent" in combined
    assert "manifest-workspace:learning-capture" in combined


def test_ci_audit_parallel_dispatch_requests_structured_json(
    security_bundle: Path,
) -> None:
    paths = (
        security_bundle / "skills/ci-audit-triggers/SKILL.md",
        security_bundle
        / "skills/ci-audit-triggers/references/ci-audit-triggers-dispatch.md",
    )

    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert (
            "manifest-workspace:parallel-agent --analyze <workflow> --validate --json"
            in source
        )


def test_semgrep_is_optional_and_only_selected_modes_require_it(
    security_bundle: Path,
) -> None:
    contract = load_contract(security_bundle / "manifest-capabilities.yml")
    code_audit = (security_bundle / "skills/code-audit/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert contract.capabilities.executables[CapabilityTier.OPTIONAL] == ("semgrep",)
    assert "Semgrep is optional" in code_audit
    assert "selected" in code_audit
    assert "requested audit mode" in code_audit
    assert "fail" in code_audit


def test_security_contract_declares_only_its_runtime(
    security_bundle: Path,
) -> None:
    contract = load_contract(security_bundle / "manifest-capabilities.yml")
    runtime_paths = {component.path for component in contract.components.runtime}

    assert runtime_paths == {"runtime/bin", "runtime/references"}
    assert not contract.components.hooks
    assert contract.capabilities.executables[CapabilityTier.REQUIRED] == (
        "bash",
        "git",
        "python3",
    )


def test_generated_security_views_represent_runtime_for_every_harness(
    security_bundle: Path,
) -> None:
    claude = json.loads(
        (security_bundle / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    gemini = json.loads(
        (security_bundle / "gemini-extension.json").read_text(encoding="utf-8")
    )
    generic = json.loads((security_bundle / "plugin.json").read_text(encoding="utf-8"))

    # Claude's native plugin.json nests compatibility evidence under
    # `metadata` (the documented free-form field); Gemini keeps it at the
    # top level.
    compatibility_by_view = {
        "claude": claude["metadata"]["compatibility"],
        "gemini": gemini["compatibility"],
    }
    for compatibility in compatibility_by_view.values():
        ids = {
            record["component_id"]
            for records in compatibility.values()
            for record in records
            if record["component_type"] == "runtime"
        }
        assert ids == {"security-bin", "security-references"}

    for harness in ("codex", "cursor", "antigravity", "devin"):
        components = generic["harnesses"][harness]["components"]["runtime"]
        assert {component["id"] for component in components} == {
            "security-bin",
            "security-references",
        }


def _activates_code_audit(policy: dict, signals: set[str]) -> bool:
    return bool(signals & set(policy["any_of"]))


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        ({"nonsecurity_cache_hash"}, False),
        ({"session_variable"}, False),
        ({"generic_input_or_pattern_token"}, False),
        ({"file_size_or_complexity"}, False),
        ({"explicit_security_review_request", "no_diff"}, True),
        ({"security_boundary_behavior_change"}, True),
    ],
)
def test_code_audit_activation_classifies_boundary_scenarios(
    repo_root: Path, signals: set[str], expected: bool
) -> None:
    policies = yaml.safe_load(
        (repo_root / "configs/claude/config/skill_policies.yml").read_text(
            encoding="utf-8"
        )
    )
    trigger = policies["implicit_invocation_triggers"]["manifest-security:code-audit"]

    assert _activates_code_audit(trigger, signals) is expected
    assert set(trigger["non_triggers"]).isdisjoint(trigger["any_of"])


def test_code_audit_policy_keeps_scanners_check_only(repo_root: Path) -> None:
    config = yaml.safe_load(
        (repo_root / "configs/claude/config/command_config.yml").read_text(
            encoding="utf-8"
        )
    )
    policy = config["tool_policies"]["code-audit"]

    assert policy["parallel_agents"] == "conditional"
    assert policy["bash_mode"] == "check-only"
    assert "Bash" in policy["allowed"]
    assert {"Write", "Edit"}.issubset(policy["forbidden"])
    assert "Bash" not in policy["forbidden"]
    assert config["review_escalation"]["verification"]["missing_tool_result"] == (
        "unavailable"
    )


def test_installed_code_audit_discloses_review_and_check_outcomes(
    security_bundle: Path, tmp_path: Path
) -> None:
    installed = tmp_path / "installed/manifest-security"
    shutil.copytree(security_bundle, installed)
    source = (installed / "skills/code-audit/SKILL.md").read_text(encoding="utf-8")

    template = (
        source.split("## Output Format", 1)[1].split("```", 1)[1].split("```", 1)[0]
    )
    assert "**review_mode**:" in template
    assert "**escalation_reason**:" in template
    assert "| Command | Result | unavailable_reason |" in template
    assert "`unavailable`" in template
    assert (
        "manifest-workspace:learning-capture query --language <detected-language>"
        in source
    )
    assert "if it fails or returns empty, continue" in source
    assert not re.search(r"(?:>=|≥)\s*3.*dispatch", source, flags=re.IGNORECASE)


def test_security_dispatch_links_are_skill_local(security_bundle: Path) -> None:
    for skill_name, reference_name in (
        ("code-audit", "code-audit-dispatch.md"),
        ("ci-audit-triggers", "ci-audit-triggers-dispatch.md"),
        ("security-refute-findings", "security-refute-findings-dispatch.md"),
        ("security-triage-findings", "security-triage-findings-dispatch.md"),
    ):
        skill = security_bundle / f"skills/{skill_name}/SKILL.md"
        source = skill.read_text(encoding="utf-8")
        link = re.search(r"\[[^]]*dispatch[^]]*\]\(([^)]+)\)", source, re.IGNORECASE)
        assert link is not None, f"{skill_name}: missing dispatch link"
        target = (skill.parent / link.group(1)).resolve()
        assert target.name == reference_name
        assert target.is_file()


def test_ci_audit_dispatch_uses_supported_analyze_contract(
    security_bundle: Path,
) -> None:
    skill = security_bundle / "skills/ci-audit-triggers/SKILL.md"
    source = skill.read_text(encoding="utf-8")
    reference = skill.parent / "references/ci-audit-triggers-dispatch.md"
    invocation = (
        "manifest-workspace:parallel-agent --analyze <workflow> --validate --json"
    )

    assert "configs%2Fclaude/references/sub-agent-dispatch.md" not in source
    assert f"`{invocation}`" in source
    assert f"`{invocation}`" in reference.read_text(encoding="utf-8")
    assert "--security-analysis" not in source
    assert "--security-analysis" not in reference.read_text(encoding="utf-8")
