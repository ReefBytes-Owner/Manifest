"""Isolation tests for the installed manifest-code-quality bundle."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

import pytest
import yaml

from manifest_agent.contracts import CapabilityTier, load_contract


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture
def code_quality_bundle(repo_root: Path) -> Path:
    return repo_root / "plugins" / "manifest-code-quality"


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    state = tmp_path / "state"
    data = tmp_path / "data"
    config = tmp_path / "config"
    for path in (home, state, data, config):
        path.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "HOME": str(home),
        "XDG_STATE_HOME": str(state),
        "XDG_DATA_HOME": str(data),
        "XDG_CONFIG_HOME": str(config),
        "UV_NO_NETWORK": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
    }


def _run(
    script: Path, *args: str, env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", "-B", str(script), *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_constitution_cli_loads_only_adjacent_json_policy(
    code_quality_bundle: Path, tmp_path: Path
) -> None:
    skill = code_quality_bundle / "skills/code-audit-constitution"
    script = skill / "scripts/constitution_check.py"

    help_result = _run(script, "--help", env=_isolated_env(tmp_path), cwd=tmp_path)
    list_result = _run(script, "--list", env=_isolated_env(tmp_path), cwd=tmp_path)

    assert help_result.returncode == 0, help_result.stderr
    assert list_result.returncode == 0, list_result.stderr
    assert "CON-013" in list_result.stdout
    assert (skill / "config/code_constitution.json").is_file()
    assert (skill / "config/constitution_baseline.json").is_file()
    registry = skill / "scripts/constitution/registry.py"
    source = registry.read_text(encoding="utf-8")
    assert "import yaml" not in source
    assert "code_constitution.yml" not in source
    assert "configs/claude" not in source


def test_smoke_cli_uses_adjacent_vendored_yaml_offline(
    code_quality_bundle: Path, tmp_path: Path
) -> None:
    skill = code_quality_bundle / "skills/smoke-manage"
    script = skill / "scripts/smoke.py"
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    (catalog_dir / "demo.yaml").write_text(
        "version: 1\napp: demo\ntests: []\n", encoding="utf-8"
    )

    help_result = _run(script, "--help", env=_isolated_env(tmp_path), cwd=tmp_path)
    list_result = _run(
        script,
        "list",
        "--app",
        "demo",
        "--json",
        "--catalog-dir",
        str(catalog_dir),
        env=_isolated_env(tmp_path),
        cwd=tmp_path,
    )

    assert help_result.returncode == 0, help_result.stderr
    assert list_result.returncode == 0, list_result.stderr
    assert json.loads(list_result.stdout) == {"demo": []}
    assert "manifest_cli" not in help_result.stderr
    assert (skill / "vendor/yaml/__init__.py").is_file()
    assert (skill / "vendor/LICENSE.PyYAML").is_file()


def test_smoke_append_rejects_malformed_stdin_as_invalid_input(
    code_quality_bundle: Path, tmp_path: Path
) -> None:
    script = code_quality_bundle / "skills/smoke-manage/scripts/smoke.py"

    result = subprocess.run(
        [sys.executable, "-S", "-B", str(script), "append", "--stdin"],
        cwd=tmp_path,
        env=_isolated_env(tmp_path),
        input="{not-json\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid workflow description JSON" in result.stderr


def test_vendored_yaml_provenance_matches_lock_and_committed_hashes(
    repo_root: Path, code_quality_bundle: Path, tmp_path: Path
) -> None:
    vendor = code_quality_bundle / "skills/smoke-manage/vendor"
    metadata = json.loads((vendor / "VENDOR.json").read_text(encoding="utf-8"))

    assert metadata["name"] == "PyYAML"
    assert metadata["version"] == "6.0.3"
    assert metadata["source"] == "https://pypi.org/project/PyYAML/6.0.3/"
    assert metadata["sdist_sha256"] == (
        "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
    )
    assert metadata["license"] == "MIT"
    assert metadata["files"]

    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-B",
            str(repo_root / "tools/vendor_bundle_dependencies.py"),
            "--check",
        ],
        cwd=tmp_path,
        env=_isolated_env(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_precommit_preserves_only_immutable_upstream_vendor_bytes(
    repo_root: Path,
) -> None:
    import yaml

    config = yaml.safe_load(
        (repo_root / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    )
    hooks = {
        hook["id"]: hook
        for repository in config["repos"]
        for hook in repository["hooks"]
    }
    upstream_python = (
        "plugins/manifest-code-quality/skills/smoke-manage/vendor/yaml/parser.py"
    )
    upstream_license = (
        "plugins/manifest-code-quality/skills/smoke-manage/vendor/LICENSE.PyYAML"
    )
    owned_runtime = "plugins/manifest-code-quality/skills/smoke-manage/scripts/smoke.py"
    owned_metadata = (
        "plugins/manifest-code-quality/skills/smoke-manage/vendor/VENDOR.json"
    )

    for hook_id in ("trailing-whitespace", "end-of-file-fixer", "mixed-line-ending"):
        exclude = hooks[hook_id]["exclude"]
        assert re.search(exclude, upstream_python)
        assert re.search(exclude, upstream_license)
        assert not re.search(exclude, owned_runtime)
        assert not re.search(exclude, owned_metadata)

    for hook_id in ("ruff", "ruff-format", "constitution-check"):
        exclude = hooks[hook_id]["exclude"]
        assert re.search(exclude, upstream_python)
        assert not re.search(exclude, owned_runtime)
        assert not re.search(exclude, owned_metadata)

    scaffold_js = (
        "plugins/manifest-code-quality/skills/project-scaffold/"
        "templates/node/eslint.config.js"
    )
    eslint_exclude = hooks["eslint"]["exclude"]
    assert re.search(eslint_exclude, scaffold_js)
    assert not re.search(eslint_exclude, owned_runtime)


def test_relocated_smoke_debt_keeps_the_existing_constitution_ratchet(
    repo_root: Path, code_quality_bundle: Path
) -> None:
    global_baseline = json.loads(
        (repo_root / "configs/claude/config/constitution_baseline.json").read_text(
            encoding="utf-8"
        )
    )["files"]
    bundle_baseline = json.loads(
        (
            code_quality_bundle
            / "skills/code-audit-constitution/config/constitution_baseline.json"
        ).read_text(encoding="utf-8")
    )["files"]
    legacy_prefix = "configs/claude/scripts/smoke_orchestrator/"
    plugin_prefix = (
        "plugins/manifest-code-quality/skills/smoke-manage/scripts/smoke_orchestrator/"
    )

    for relative in (
        "cli.py",
        "executor.py",
        "state.py",
        "steps/agent.py",
        "steps/api.py",
        "steps/ui.py",
        "validation.py",
    ):
        expected = global_baseline[f"{legacy_prefix}{relative}"]
        assert global_baseline[f"{plugin_prefix}{relative}"] == expected
        assert bundle_baseline[f"{plugin_prefix}{relative}"] == expected


@pytest.mark.parametrize(
    ("bad_member", "message"),
    [
        ("lib/yaml/_yaml.cpython-313-darwin.so", "native"),
        ("lib/yaml/unexpected.py", "unexpected"),
    ],
)
def test_vendor_tool_rejects_native_and_unexpected_archive_members(
    repo_root: Path, bad_member: str, message: str
) -> None:
    tool = repo_root / "tools/vendor_bundle_dependencies.py"
    spec = importlib.util.spec_from_file_location("vendor_bundle_dependencies", tool)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    archive = BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as handle:
        for name in (
            f"pyyaml-6.0.3/{bad_member}",
            "pyyaml-6.0.3/LICENSE",
        ):
            info = tarfile.TarInfo(name)
            info.size = 1
            handle.addfile(info, BytesIO(b"x"))
    archive.seek(0)

    with (
        tarfile.open(fileobj=archive, mode="r:gz") as handle,
        pytest.raises(module.VendorError, match=message),
    ):
        module._validated_members(handle, "pyyaml-6.0.3")


def test_scaffold_and_audit_assets_are_bundle_local(
    repo_root: Path, code_quality_bundle: Path
) -> None:
    packaged = code_quality_bundle / "skills/project-scaffold/templates"
    assert not (repo_root / "templates").exists()
    assert {
        path.relative_to(packaged) for path in packaged.rglob("*") if path.is_file()
    } == {
        Path("go/.golangci.yml"),
        Path("go/Makefile"),
        Path("go/go.mod.tmpl"),
        Path("node/eslint.config.js"),
        Path("node/package.json.tmpl"),
        Path("node/tsconfig.json"),
        Path("python/.pre-commit-config.yaml"),
        Path("python/pyproject.toml"),
        Path("terraform/.tflint.hcl"),
        Path("terraform/main.tf.tmpl"),
        Path("terraform/versions.tf.tmpl"),
    }
    assert (
        code_quality_bundle / "skills/code-audit/references/antipatterns.md"
    ).read_bytes() == (
        repo_root / "configs/claude/references/antipatterns.md"
    ).read_bytes()


def test_code_quality_contract_declares_every_runtime_asset(
    code_quality_bundle: Path,
) -> None:
    contract = load_contract(code_quality_bundle / "manifest-capabilities.yml")
    runtime_paths = {component.path for component in contract.components.runtime}

    assert runtime_paths == {
        "skills/code-audit-constitution/scripts",
        "skills/code-audit-constitution/config",
        "skills/code-audit-constitution/references",
        "skills/smoke-manage/scripts",
        "skills/smoke-manage/vendor",
        "skills/project-scaffold/templates",
        "skills/code-audit/references",
        "skills/refactor/references/review-escalation.md",
    }
    assert set(contract.capabilities.executables[CapabilityTier.OPTIONAL]) == {
        "browser-use",
        "playwright",
        "semgrep",
    }
    assert contract.capabilities.executables[CapabilityTier.REQUIRED] == (
        "git",
        "python3",
    )


def test_code_quality_skills_do_not_call_legacy_shared_runtimes(
    code_quality_bundle: Path,
) -> None:
    forbidden = (
        "configs/claude/scripts",
        "~/.claude/scripts",
        "manifest smoke",
        "parallel_agent.py",
        "learning_capture.sh",
    )

    for skill in code_quality_bundle.glob("skills/*/SKILL.md"):
        source = skill.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in source, f"{skill}: forbidden runtime marker {marker}"


REFACTOR_SKILLS = (
    "python-refactor",
    "node-refactor",
    "go-refactor",
    "shell-refactor",
    "terraform-refactor",
)

RISK_CONCEPTS = (
    "authentication, authorization, cryptography, secret handling",
    "destructive data or infrastructure behavior",
    "public compatibility or deployment change with broad impact",
    "conflicting evidence or unresolved reviewer uncertainty",
    "codebase-wide investigation with genuinely independent analysis tracks",
)


def _markdown_section(source: str, heading: str) -> str:
    match = re.search(rf"(?ms)^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)", source)
    assert match is not None, f"missing Markdown section: {heading}"
    return match.group(1)


def _requires_independent_review(policy: dict, signals: set[str]) -> bool:
    return bool(signals & set(policy["conditions"]))


def _review_config(repo_root: Path) -> dict:
    path = repo_root / "configs/claude/config/command_config.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        ({"file_size", "language", "generic_keyword"}, False),
        ({"trust_boundary_change"}, True),
        ({"destructive_behavior"}, True),
        ({"broad_compatibility_or_deployment_change"}, True),
        ({"conflicting_evidence_or_unresolved_uncertainty"}, True),
        ({"codebase_wide_independent_tracks"}, True),
    ],
)
def test_refactor_review_escalation_classifies_risk_scenarios(
    repo_root: Path, signals: set[str], expected: bool
) -> None:
    config = _review_config(repo_root)
    escalation = config["review_escalation"]

    assert _requires_independent_review(escalation, signals) is expected
    assert set(escalation["non_triggers"]).isdisjoint(escalation["conditions"])


def test_refactor_policies_are_conditional_and_check_only(repo_root: Path) -> None:
    config = _review_config(repo_root)
    expected_trigger = " OR ".join(config["review_escalation"]["conditions"])

    for skill_name in REFACTOR_SKILLS:
        policy = config["tool_policies"][skill_name]
        assert policy["parallel_agents"] == "conditional"
        assert policy["trigger_condition"] == expected_trigger
        assert policy["subagent_trigger"] == expected_trigger
        assert policy["bash_mode"] == "check-only"
        assert "Bash" in policy["allowed"]
        assert {"Write", "Edit"}.issubset(policy["forbidden"])
        assert "Bash" not in policy["forbidden"]

    check_policy = config["review_escalation"]["verification"]
    assert check_policy["missing_tool_result"] == "unavailable"
    assert set(check_policy["forbidden_operations"]) == {
        "automatic_fixes",
        "format_writes",
        "installations",
        "deployments",
        "remediation",
    }
    assert config["review_escalation"]["report_fields"] == [
        "review_mode",
        "escalation_reason",
        "checks",
    ]


def test_refactor_shared_reference_resolves_in_isolated_bundle(
    code_quality_bundle: Path, tmp_path: Path
) -> None:
    installed = tmp_path / "installed/manifest-code-quality"
    shutil.copytree(code_quality_bundle, installed)
    contract = load_contract(installed / "manifest-capabilities.yml")
    runtime_paths = {component.path for component in contract.components.runtime}
    reference = installed / "skills/refactor/references/review-escalation.md"

    assert "skills/refactor/references/review-escalation.md" in runtime_paths
    assert reference.is_file()
    for skill_name in REFACTOR_SKILLS:
        skill = installed / f"skills/{skill_name}/SKILL.md"
        source = skill.read_text(encoding="utf-8")
        link = re.search(
            r"\[review escalation contract\]\(([^)]+)\)", source, re.IGNORECASE
        )
        assert link is not None, f"{skill_name}: missing review escalation link"
        linked = (skill.parent / link.group(1)).resolve()
        assert linked == reference.resolve()


def test_installed_refactor_skills_enforce_the_review_contract(
    code_quality_bundle: Path, tmp_path: Path
) -> None:
    installed = tmp_path / "installed/manifest-code-quality"
    shutil.copytree(code_quality_bundle, installed)
    reference = (
        installed / "skills/refactor/references/review-escalation.md"
    ).read_text(encoding="utf-8")
    normalized_reference = " ".join(reference.split())
    conditions = reference.split(
        "Add independent review when at least one of these conditions is present:\n",
        maxsplit=1,
    )[1].split("\n\nFile size", maxsplit=1)[0]

    assert "Use one capable reviewing agent by default." in reference
    assert len(re.findall(r"(?m)^- ", conditions)) == 5
    for concept in RISK_CONCEPTS:
        assert concept in conditions
    for field in ("`review_mode`", "`escalation_reason`", "`checks`"):
        assert field in reference
    for restriction in (
        "automatic fixes",
        "write formatting changes",
        "install packages or tools",
        "deploy",
        "remediate findings",
    ):
        assert restriction in normalized_reference
    assert "`unavailable`, never a passing check" in normalized_reference

    for skill_name in REFACTOR_SKILLS:
        skill = installed / f"skills/{skill_name}/SKILL.md"
        source = skill.read_text(encoding="utf-8")
        dispatch = _markdown_section(source, "Sub-agent dispatch")
        normalized_dispatch = " ".join(dispatch.split())
        assert "ALWAYS uses parallel agents" not in source
        assert (
            "only when at least one of that contract's five risk conditions"
            in normalized_dispatch
        )
        assert "overrides any count or size threshold" in normalized_dispatch
        assert not re.search(r"(?:>=|≥)\s*\d+|independent_units", dispatch)
