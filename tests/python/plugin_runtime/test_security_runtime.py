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
    verification = config["review_escalation"]["verification"]
    assert verification["checkout_trust"] == "untrusted"
    assert verification["allowed_without_isolation"] == [
        "trusted_preinstalled_static_tool_treating_checkout_as_data"
    ]
    assert set(verification["requires_verified_isolation"]) == {
        "project_controlled_tests_scripts_and_build_steps",
        "checkout_controlled_executable_config_plugins_hooks_imports_or_discovery",
    }
    assert set(verification["insufficient_isolation"]) == {
        "source_inspection",
        "check_only_flags",
        "changed_home",
        "temporary_directory",
        "read_only_checkout",
    }
    assert verification["unavailable_isolation_result"] == (
        "skip_and_report_unavailable"
    )


def _assert_code_audit_verification_safety(source: str) -> None:
    assert "Treat the checkout as\n   untrusted" in source
    assert "trusted preinstalled static\n   tools" in source
    assert "require enforced isolation" in source
    assert "read-only checkout are insufficient" in source
    assert "skip execution and report `unavailable`" in source


def test_installed_code_audit_prompt_declares_the_review_contract(
    security_bundle: Path, repo_root: Path, tmp_path: Path
) -> None:
    installed = tmp_path / "installed/manifest-security"
    shutil.copytree(security_bundle, installed)
    skill = installed / "skills/code-audit/SKILL.md"
    source = skill.read_text(encoding="utf-8")
    policies = yaml.safe_load(
        (repo_root / "configs/claude/config/skill_policies.yml").read_text(
            encoding="utf-8"
        )
    )
    trigger_policy = policies["implicit_invocation_triggers"][
        "manifest-security:code-audit"
    ]
    configuration = yaml.safe_load(
        source.split("## Configuration", 1)[1].split("```yaml", 1)[1].split("```", 1)[0]
    )
    escalation = source.split(
        "4. **Adds independent review only when at least one escalation condition is\n"
        "   present**:",
        1,
    )[1].split("5. **Reports", 1)[0]
    escalation_bullets: list[str] = []
    for line in escalation.splitlines():
        if line.startswith("   - "):
            escalation_bullets.append(line.removeprefix("   - "))
        elif line.startswith("     ") and escalation_bullets:
            escalation_bullets[-1] += f" {line.strip()}"

    assert configuration == {
        "any_of": trigger_policy["any_of"],
        "non_triggers": trigger_policy["non_triggers"],
    }
    assert {item.rstrip(";").rstrip(".") for item in escalation_bullets} == {
        "authentication, authorization, cryptography, secret handling, or another "
        "trust-boundary change",
        "destructive data or infrastructure behavior",
        "a public compatibility or deployment change with broad impact",
        "conflicting evidence or unresolved reviewer uncertainty",
        "a codebase-wide investigation with genuinely independent analysis tracks",
    }
    assert "Reviews inline by default** with one capable reviewing agent" in source
    assert "explicit request" in source and "even when no diff exists" in source
    assert all(field in source for field in ("review_mode", "escalation_reason"))
    assert "| Command | Result | Unavailable reason |" in source
    _assert_code_audit_verification_safety(source)
    assert all(
        restriction in source
        for restriction in (
            "--fix",
            "formatter that writes",
            "installation",
            "deployment",
            "remediation",
        )
    )
    assert "ALWAYS uses parallel agents" not in source
    assert "Invokes parallel agents" not in source
    assert not re.search(r"(?:>=|≥)\s*3.*dispatch", source, flags=re.IGNORECASE)


def test_installed_code_audit_dispatch_reference_resolves(
    security_bundle: Path, tmp_path: Path
) -> None:
    installed = tmp_path / "installed/manifest-security"
    shutil.copytree(security_bundle, installed)
    skill = installed / "skills/code-audit/SKILL.md"
    source = skill.read_text(encoding="utf-8")
    link = re.search(r"\[bundle-local dispatch selection rules\]\(([^)]+)\)", source)
    assert link is not None
    assert (skill.parent / link.group(1)).is_file()
