"""Parity and negative tests for the retained project-check registry."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from manifest_agent.checks.registry import load_registry, resolve_checks
from tools.project_checks.generated import TASK7_DISPOSITIONS as GENERATED
from tools.project_checks.hooks import TASK7_DISPOSITIONS as HOOKS
from tools.project_checks.packages import TASK7_DISPOSITIONS as PACKAGES
from tools.project_checks.structure import TASK7_DISPOSITIONS as STRUCTURE

ROOT = Path(__file__).resolve().parents[3]
VERSION_ADAPTER = ROOT / "tools/project_checks/tool_versions.py"
REGISTRY_PATH = ROOT / "config/project-checks.json"
PRESERVATION_PATH = ROOT / "config/check-preservation.json"
DISPOSITIONS = STRUCTURE | GENERATED | HOOKS | PACKAGES
SECURITY_IDS = frozenset(
    {
        "hook.check-credentials",
        "hook.detect-private-key",
        "hook.gitleaks",
        "hook.terraform_trivy",
    }
)
RELEASE_ONLY_IDS = frozenset({"package.release-archive", "package.release-manifest"})
FILENAMELESS_HOOK_IDS = frozenset(
    {
        "hook.cargo-clippy",
        "hook.cargo-fmt-check",
        "hook.check-credentials",
        "hook.check-cursor-rules-drift",
        "hook.gitleaks",
        "hook.pyright",
    }
)
QUICK_IDS = frozenset(
    {
        check_id
        for check_id, (_, selection) in HOOKS.items()
        if selection == "changed" and check_id not in SECURITY_IDS
    }
    | {"structure.case-collision", "structure.symlinks"}
)
RETAINED_IDS = frozenset(DISPOSITIONS)
EXPECTED_PROFILES = {
    "quick": QUICK_IDS,
    "full": RETAINED_IDS - SECURITY_IDS - RELEASE_ONLY_IDS,
    "security": SECURITY_IDS,
    "release": RETAINED_IDS,
}
GRAPH_CATEGORIES = frozenset(
    {"type", "dead-code", "test", "security", "generated", "dependency", "package"}
)


def _raw_documents() -> tuple[dict, dict]:
    return (
        json.loads(PRESERVATION_PATH.read_text(encoding="utf-8")),
        json.loads(REGISTRY_PATH.read_text(encoding="utf-8")),
    )


def _check_by_id(registry: dict) -> dict[str, dict]:
    return {check["id"]: check for check in registry["checks"]}


def _expected_group(check_id: str) -> str:
    if check_id.startswith(("hook.", "lint.", "syntax.")):
        return "lint"
    if check_id.startswith("test."):
        return "test"
    if check_id.startswith(("package.", "dependency.")):
        return "package"
    return "structure"


def _expected_pass_filenames(check_id: str) -> bool:
    return check_id.startswith("hook.") and check_id not in FILENAMELESS_HOOK_IDS


def _hook_sources(preservation: dict) -> dict[str, dict]:
    sources = {}
    for entry in preservation["source_entries"]:
        if entry["kind"] != "hook":
            continue
        check_id = entry["component_ids"][0]
        assert check_id not in sources
        sources[check_id] = entry["value"]
    return sources


def _expected_exclude(source: dict) -> str:
    patterns = []
    for boundary in (source["global"]["exclude"], source["hook"]["exclude"]):
        if boundary["present"]:
            patterns.append(boundary["value"])
    if not patterns:
        return r"$^"
    return "|".join(f"(?:{pattern})" for pattern in patterns)


def _assert_retained_contract(preservation: dict, registry: dict) -> None:
    checks = registry["checks"]
    declared = [check["id"] for check in checks]
    retained = {
        check_id
        for control in preservation["controls"]
        if control["disposition"] == "retained"
        for check_id in control["check_ids"]
    }
    assert len(declared) == len(set(declared)) == 71
    assert set(declared) == retained == RETAINED_IDS
    assert all("pass_filenames" in check for check in checks)
    by_id = _check_by_id(registry)
    for check_id, (argv, selection) in DISPOSITIONS.items():
        assert tuple(by_id[check_id]["argv"]) == tuple(argv)
        assert by_id[check_id]["selection"] == selection
        assert by_id[check_id]["group"] == _expected_group(check_id)
        assert by_id[check_id]["pass_filenames"] is _expected_pass_filenames(check_id)


def _assert_hook_contract(preservation: dict, registry: dict) -> None:
    by_id = _check_by_id(registry)
    for check_id, source in _hook_sources(preservation).items():
        hook = source["hook"]
        check = by_id[check_id]
        expected_files = hook["files"]["value"] if hook["files"]["present"] else ""
        expected_types = hook["types"]["value"] if hook["types"]["present"] else []
        expected_types_or = (
            hook["types_or"]["value"] if hook["types_or"]["present"] else []
        )
        assert check["inputs"] == ["."]
        assert check.get("include_regex", "") == expected_files
        assert check.get("exclude_regex", r"$^") == _expected_exclude(source)
        assert check.get("types", []) == expected_types
        assert check.get("types_or", []) == expected_types_or
        assert tuple(check["argv"]) == tuple(DISPOSITIONS[check_id][0])


def _assert_profile_contract(registry: dict) -> None:
    by_id = _check_by_id(registry)
    assert {profile: set(ids) for profile, ids in registry["profiles"].items()} == {
        profile: set(ids) for profile, ids in EXPECTED_PROFILES.items()
    }
    assert {profile: len(ids) for profile, ids in EXPECTED_PROFILES.items()} == {
        "quick": 31,
        "full": 65,
        "security": 4,
        "release": 71,
    }
    assert all(
        by_id[check_id]["selection"] == "project"
        for check_id in EXPECTED_PROFILES["full"] | EXPECTED_PROFILES["security"]
        if by_id[check_id]["category"] in GRAPH_CATEGORIES
    )
    assert all(
        by_id[check_id]["selection"] == "changed"
        or check_id in {"structure.case-collision", "structure.symlinks"}
        for check_id in EXPECTED_PROFILES["quick"]
    )
    assert all(
        by_id[check_id]["selection"] == DISPOSITIONS[check_id][1]
        for check_ids in EXPECTED_PROFILES.values()
        for check_id in check_ids
    )


def _assert_setup_and_publication_contract(
    preservation: dict, registry: dict, provisioning_ids: set[str]
) -> None:
    preparation_ids = {
        preparation["id"] for preparation in registry["candidate_preparations"]
    }
    profile_ids = {
        check_id
        for check_ids in registry["profiles"].values()
        for check_id in check_ids
    }
    for control in preservation["controls"]:
        if control["disposition"] == "setup":
            assert control["id"] in provisioning_ids | preparation_ids
        elif control["disposition"] == "publication":
            assert control["id"] not in profile_ids
            assert set(control["workflow_control_ids"]).isdisjoint(profile_ids)


def _assert_version_contract(preservation: dict, registry: dict) -> None:
    by_id = _check_by_id(registry)
    tools = registry["tools"]
    retained_controls = [
        control
        for control in preservation["controls"]
        if control["disposition"] == "retained"
    ]
    for check in registry["checks"]:
        tool = tools[check["tool"]]
        assert check["argv"][0] == tool["executable"]
        assert check["version"] == tool["expected_version"]
        assert tool["expected_version"] not in tool["version_argv"]
        assert "-c" not in tool["version_argv"]
        assert all("print(" not in argument for argument in tool["version_argv"])
        if tool["version_argv"][0] == "python3":
            assert tool["version_argv"][1:3] == [
                "-I",
                "tools/project_checks/tool_versions.py",
            ]
            assert tool["expected_version"].startswith("python=3.14;")
        probe_argv = tool["version_argv"]
        if "--file" in probe_argv:
            relative = probe_argv[probe_argv.index("--file") + 1]
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            assert f"file:{relative}={digest}" in tool["expected_version"]
        if "file-sha" in probe_argv:
            relative = probe_argv[probe_argv.index("file-sha") + 1]
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            assert f"file-sha={digest}" in tool["expected_version"]
    for control in retained_controls:
        if not control["tool_pin"]["present"]:
            continue
        for check_id in control["check_ids"]:
            version = by_id[check_id]["version"]
            pin = control["tool_pin"]["value"][0]
            if "==" in pin:
                pin_version = pin.rsplit("==", 1)[1]
            elif pin.startswith("python-version="):
                pin_version = pin.partition("=")[2]
            else:
                pin_version = pin.rsplit("@", 1)[-1].removeprefix("v")
            if pin_version not in version:
                affected_profiles = [
                    profile
                    for profile, ids in registry["profiles"].items()
                    if check_id in ids
                ]
                assert affected_profiles
                assert all(
                    any(
                        check_id in obligation and pin in obligation
                        for obligation in registry["coverage_pending"][profile]
                    )
                    for profile in affected_profiles
                )


def test_registry_schema_loads_and_exactly_closes_retained_profiles():
    preservation, raw_registry = _raw_documents()

    registry = load_registry(REGISTRY_PATH)

    _assert_retained_contract(preservation, raw_registry)
    _assert_profile_contract(raw_registry)
    assert {check.id for check in registry["checks"]} == RETAINED_IDS
    for profile, expected_ids in EXPECTED_PROFILES.items():
        assert {check.id for check in resolve_checks(registry, profile, None)} == set(
            expected_ids
        )
        for group in {"lint", "test", "structure", "package"}:
            assert {check.id for check in resolve_checks(registry, profile, group)} == {
                check_id
                for check_id in expected_ids
                if _expected_group(check_id) == group
            }


def test_hook_args_and_path_filters_exactly_match_frozen_oracle():
    preservation, registry = _raw_documents()

    _assert_hook_contract(preservation, registry)


def test_tools_use_real_composite_probes_and_reviewed_pins():
    preservation, registry = _raw_documents()

    _assert_version_contract(preservation, registry)


def test_shellcheck_and_yamllint_probes_bind_invoked_inner_tools():
    _, registry = _raw_documents()
    expected = {
        "lint.shell.scripts": (
            "shellcheck-py",
            "0.11.0.1",
            "shellcheck",
            "0.11.0",
        ),
        "lint.shell.bootstrap": (
            "shellcheck-py",
            "0.11.0.1",
            "shellcheck",
            "0.11.0",
        ),
        "hook.shellcheck": (
            "shellcheck-py",
            "0.11.0.1",
            "shellcheck",
            "0.11.0",
        ),
        "lint.yaml.config": ("yamllint", "1.38.0", "yamllint", "1.38.0"),
        "hook.yamllint": ("yamllint", "1.38.0", "yamllint", "1.38.0"),
    }
    for check_id, (
        distribution,
        distribution_pin,
        command,
        command_pin,
    ) in expected.items():
        tool = registry["tools"][check_id]
        marker = tool["version_argv"].index("--console-command")
        assert tool["version_argv"][marker + 1 : marker + 4] == [
            distribution,
            command,
            command,
        ]
        assert (
            f"distribution:{distribution}={distribution_pin}"
            in tool["expected_version"]
        )
        assert f"command:{command}={command_pin}" in tool["expected_version"]


def test_setup_is_mapped_and_publication_is_outside_profiles():
    preservation, registry = _raw_documents()
    provisioning_ids = {
        control["id"]
        for control in preservation["controls"]
        if control["disposition"] == "setup"
        and control["source_key"].startswith("workflow:")
    }

    _assert_setup_and_publication_contract(preservation, registry, provisioning_ids)


def test_timeouts_are_finite_and_fit_existing_group_ceilings():
    _, registry = _raw_documents()
    totals = {"lint": 0.0, "test": 0.0, "structure": 0.0}
    for check in registry["checks"]:
        timeout = float(check["timeout_seconds"])
        assert timeout > 0 and timeout != float("inf")
        if check["group"] in totals:
            totals[check["group"]] += timeout
    assert totals["lint"] <= 1200
    assert totals["test"] <= 1800
    assert totals["structure"] <= 900


def test_pending_obligations_are_specific_and_block_affected_profiles():
    _, registry = _raw_documents()
    for profile, obligations in registry["coverage_pending"].items():
        assert obligations, f"{profile} must remain blocked until Phase 3 provisioning"
        assert all(
            ":" in obligation and "pending Phase 3" in obligation
            for obligation in obligations
        )


@pytest.mark.parametrize(
    "mutation,assertion",
    [
        (
            lambda document: document["checks"].pop(),
            _assert_retained_contract,
        ),
        (
            lambda document: next(
                check
                for check in document["checks"]
                if check["id"] == "hook.trailing-whitespace"
            ).update(inputs=["plugins"]),
            _assert_hook_contract,
        ),
        (
            lambda document: next(
                check
                for check in document["checks"]
                if check["id"] == "hook.trailing-whitespace"
            ).update(exclude_regex=r"^(\.Jules/)"),
            _assert_hook_contract,
        ),
        (
            lambda document: next(
                check
                for check in document["checks"]
                if check["id"] == "hook.cargo-clippy"
            ).update(pass_filenames=True),
            _assert_retained_contract,
        ),
        (
            lambda document: next(
                check
                for check in document["checks"]
                if check["id"] == "package.coordinator"
            ).update(pass_filenames=True),
            _assert_retained_contract,
        ),
        (
            lambda document: next(
                check for check in document["checks"] if check["id"] == "test.python"
            ).update(selection="changed", category="lint", group="lint"),
            lambda _preservation, document: _assert_profile_contract(document),
        ),
    ],
    ids=[
        "dropped-hook",
        "narrowed-input",
        "narrowed-exclusion",
        "filename-forwarding",
        "non-hook-filename-forwarding",
        "changed-graph",
    ],
)
def test_mutated_registry_cannot_weaken_parity(mutation, assertion):
    preservation, registry = _raw_documents()
    mutated = deepcopy(registry)
    mutation(mutated)

    with pytest.raises(AssertionError):
        assertion(preservation, mutated)


def test_mutated_registry_cannot_drift_pin_or_fake_version_probe():
    preservation, registry = _raw_documents()
    pin_drift = deepcopy(registry)
    check = next(check for check in pin_drift["checks"] if check["id"] == "hook.ruff")
    tool = pin_drift["tools"][check["tool"]]
    tool["expected_version"] = check["version"] = "python=3.14;command:ruff=0.0.0"
    fake_probe = deepcopy(registry)
    check = next(check for check in fake_probe["checks"] if check["id"] == "hook.ruff")
    tool = fake_probe["tools"][check["tool"]]
    tool["version_argv"] = ["python3", "-c", f"print('{tool['expected_version']}')"]

    with pytest.raises(AssertionError):
        _assert_version_contract(preservation, pin_drift)
    with pytest.raises(AssertionError):
        _assert_version_contract(preservation, fake_probe)


def test_mutated_registry_cannot_drop_setup_mapping_or_leak_publication():
    preservation, registry = _raw_documents()
    setup_ids = {
        control["id"]
        for control in preservation["controls"]
        if control["disposition"] == "setup"
    }
    missing = set(setup_ids)
    missing.pop()
    leaked = deepcopy(registry)
    publication = next(
        control
        for control in preservation["controls"]
        if control["disposition"] == "publication"
    )
    leaked["profiles"]["release"].append(publication["workflow_control_ids"][0])

    with pytest.raises(AssertionError):
        _assert_setup_and_publication_contract(preservation, registry, missing)
    with pytest.raises(AssertionError):
        _assert_setup_and_publication_contract(preservation, leaked, setup_ids)
