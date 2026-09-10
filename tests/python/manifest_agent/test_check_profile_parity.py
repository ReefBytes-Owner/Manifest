"""Parity and negative tests for the retained project-check registry."""

from __future__ import annotations

import itertools
from copy import deepcopy

import pytest

from manifest_agent.checks.registry import load_registry, resolve_checks
from tests.python.manifest_agent import _c5_ids
from tests.python.manifest_agent._check_profile_oracle import (
    DISPOSITIONS,
    REGISTRY_PATH,
    ROOT,
    SUPERSEDED,
    _check_by_id,
    _raw_documents,
)
from tests.python.manifest_agent.test_check_profile_hook_contract import (
    _assert_hook_contract,
)
from tools.project_checks.hook_lint import TASK7_DISPOSITIONS as HOOK_LINT
from tools.project_checks.hooks import TASK7_DISPOSITIONS as HOOKS

VERSION_ADAPTER = ROOT / "tools/project_checks/tool_versions.py"
SECURITY_IDS = _c5_ids.SECURITY_IDS
RELEASE_ONLY_IDS = frozenset({"package.release-archive", "package.release-manifest"})
FILENAMELESS_HOOK_IDS = frozenset(
    {
        "hook.cargo-clippy",
        "hook.cargo-fmt-check",
        "hook.check-credentials",
        "hook.check-cursor-rules-drift",
        "hook.gitleaks",
    }
)
QUICK_IDS = frozenset(
    {
        check_id
        for check_id, (_, selection) in (HOOKS | HOOK_LINT).items()
        if selection == "changed" and check_id not in SECURITY_IDS
    }
    | {"structure.case-collision", "structure.symlinks"}
)
RETAINED_IDS = frozenset(DISPOSITIONS)
# C3 (identity-based debt ratchet): new controls, not part of the frozen
# shadow-CI migration DISPOSITIONS above -- they have no legacy pre-commit/CI
# job to preserve 1:1, so they are additive to the registry rather than drawn
# from `config/check-preservation.json`. `full`/`security` get the
# candidate-baseline variant; `release` additionally requires the
# `--baseline-from-base` variant so a candidate can never ship its own
# exceptions (docs/SHARED_CHECKS.md "debt.constitution" / "debt.bundle-links").
DEBT_IDS = frozenset({"debt.constitution", "debt.bundle-links"})
DEBT_RELEASE_IDS = frozenset({"debt.constitution.release", "debt.bundle-links.release"})
# C5_* (types/security/dependency-integrity new checks): see _c5_ids.py.
C5_FULL = _c5_ids.C5_FULL_RELEASE_IDS
C5_SEC = _c5_ids.C5_SECURITY_RELEASE_IDS
C5_DECLARED_ONLY_IDS = _c5_ids.C5_DECLARED_ONLY_IDS
LIVE_RETAINED_IDS = RETAINED_IDS - SUPERSEDED
EXPECTED_PROFILES = {
    "quick": QUICK_IDS,
    # C6b: `full` folds in SECURITY_IDS/C5_SEC -- one CI aggregate, `security`
    # is a named subset with no aggregate of its own (phase-3-5-decisions.md).
    "full": LIVE_RETAINED_IDS - RELEASE_ONLY_IDS
    | DEBT_IDS
    | C5_FULL
    | SECURITY_IDS
    | C5_SEC,
    "security": SECURITY_IDS | DEBT_IDS | C5_SEC,
    "release": LIVE_RETAINED_IDS | DEBT_IDS | DEBT_RELEASE_IDS | C5_FULL | C5_SEC,
}
GRAPH_CATEGORIES = frozenset(
    {"type", "dead-code", "test", "security", "generated", "dependency", "package"}
)


def _expected_group(check_id: str) -> str:
    if check_id in _c5_ids.GROUP_OVERRIDES:
        return _c5_ids.GROUP_OVERRIDES[check_id]
    if check_id.startswith(("hook.", "lint.", "syntax.")):
        return "lint"
    if check_id.startswith("test."):
        return "test"
    if check_id.startswith(("package.", "dependency.")):
        return "package"
    return "structure"


def _expected_pass_filenames(check_id: str) -> bool:
    return check_id.startswith("hook.") and check_id not in FILENAMELESS_HOOK_IDS


def _assert_retained_contract(preservation: dict, registry: dict) -> None:
    checks = registry["checks"]
    declared = [check["id"] for check in checks]
    retained = {
        check_id
        for control in preservation["controls"]
        if control["disposition"] == "retained"
        for check_id in control["check_ids"]
    }
    # The registry carries the frozen shadow-CI migration set (`retained`,
    # cross-checked against `RETAINED_IDS`) plus C3's additive debt.* ids,
    # which have no legacy pre-commit/CI job to preserve 1:1. Exact equality
    # (not a subset check) still catches an accidental drop of ANY check,
    # migrated or new.
    assert len(declared) == len(set(declared))
    assert retained == RETAINED_IDS
    c5_ids = C5_FULL | C5_SEC | C5_DECLARED_ONLY_IDS
    assert set(declared) == LIVE_RETAINED_IDS | DEBT_IDS | DEBT_RELEASE_IDS | c5_ids
    assert all("pass_filenames" in check for check in checks)
    by_id = _check_by_id(registry)
    for check_id, (argv, selection) in DISPOSITIONS.items():
        if check_id in SUPERSEDED:  # hook.pyright: oracle-only, see _c5_ids.py
            continue
        assert tuple(by_id[check_id]["argv"]) == tuple(argv)
        assert by_id[check_id]["selection"] == selection
        assert by_id[check_id]["group"] == _expected_group(check_id)
        assert by_id[check_id]["pass_filenames"] is _expected_pass_filenames(check_id)


def _assert_profile_contract(registry: dict) -> None:
    by_id = _check_by_id(registry)
    assert {profile: set(ids) for profile, ids in registry["profiles"].items()} == {
        profile: set(ids) for profile, ids in EXPECTED_PROFILES.items()
    }
    assert {profile: len(ids) for profile, ids in EXPECTED_PROFILES.items()} == {
        "quick": 31,
        "full": 64 + sum(len(x) for x in (DEBT_IDS, C5_FULL, SECURITY_IDS, C5_SEC)),
        "security": 4 + len(DEBT_IDS) + len(C5_SEC),
        "release": 70
        + sum(len(x) for x in (DEBT_IDS, DEBT_RELEASE_IDS, C5_FULL, C5_SEC)),
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
        if check_id in DISPOSITIONS  # C3 debt.* ids: additive, not migrated
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


def _assert_thin_probe_components(expected_version: str) -> None:
    if expected_version == "ok":
        return
    for component in expected_version.split(";"):
        assert component.startswith(("distribution:", "command:")), component


def _assert_version_contract(preservation: dict, registry: dict) -> None:
    tools = registry["tools"]
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
        _assert_thin_probe_components(tool["expected_version"])
    _assert_pin_drift_is_explained(preservation, registry)


def _assert_pin_drift_is_explained(preservation: dict, registry: dict) -> None:
    # A pin's frozen `tool_pin` names the historically observed WRAPPER
    # revision (e.g. shellcheck-py==0.11.0.1, gitleaks@v8.30.0); once the
    # registry pins the underlying ENGINE instead (C2, "the registry pins
    # engines; wrappers are provisioning detail"), that literal substring
    # legitimately stops appearing in the live version string. A recorded
    # `equivalence` entry (hook_id, wrapper_rev, engine, engine_version,
    # wrapper_entry_argv, evidence, fixture_corpus) is the reviewed proof
    # that gap is understood and closed, not an unexplained drift -- it is
    # an alternative, stricter accounting to the coverage_pending fallback,
    # not a weaker one: only a check_id with its own equivalence record
    # (evidence + fixture corpus) is exempt from the pending-obligation
    # requirement below.
    by_id = _check_by_id(registry)
    equivalence_hook_ids = {
        entry["hook_id"] for entry in preservation.get("equivalence", ())
    }
    retained_controls = [
        control
        for control in preservation["controls"]
        if control["disposition"] == "retained"
    ]
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
            if pin_version not in version and check_id not in equivalence_hook_ids:
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
    c5_ids = C5_FULL | C5_SEC | C5_DECLARED_ONLY_IDS
    assert {check.id for check in registry["checks"]} == (
        LIVE_RETAINED_IDS | DEBT_IDS | DEBT_RELEASE_IDS | c5_ids
    )
    for profile, expected_ids in EXPECTED_PROFILES.items():
        assert {check.id for check in resolve_checks(registry, profile, None)} == set(
            expected_ids
        )
        for group in {"lint", "test", "structure", "package", "security"}:
            assert {check.id for check in resolve_checks(registry, profile, group)} == {
                check_id
                for check_id in expected_ids
                if _expected_group(check_id) == group
            }


def test_tools_use_real_composite_probes_and_reviewed_pins():
    preservation, registry = _raw_documents()

    _assert_version_contract(preservation, registry)


def test_shellcheck_and_yamllint_probes_bind_the_engine_not_the_wrapper():
    """C2 (engines-not-wrappers): these five tools used to probe BOTH a
    wrapper distribution (shellcheck-py, or a redundant second yamllint
    probe) AND the engine, producing a compound identity string. They now
    probe the engine alone -- the wrapper distribution is provisioning
    detail recorded in config/check-preservation.json's "equivalence" list
    instead (see test_check_preservation.py), not re-verified on every run.
    """
    _, registry = _raw_documents()
    shellcheck = ("command-version", "shellcheck", "command:shellcheck=0.11.0")
    yamllint = ("distribution-version", "yamllint", "distribution:yamllint=1.38.0")
    expected = {
        "lint.shell.scripts": shellcheck,
        "lint.shell.bootstrap": shellcheck,
        "hook.shellcheck": shellcheck,
        "lint.yaml.config": yamllint,
        "hook.yamllint": yamllint,
    }
    for check_id, (mode, probe, expected_version) in expected.items():
        tool = registry["tools"][check_id]
        argv = tool["version_argv"]
        flags = list(itertools.pairwise(argv))
        assert (mode, probe) in flags
        assert "--distribution" not in argv
        assert "--command" not in argv
        assert tool["expected_version"] == expected_version


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
    tool["expected_version"] = check["version"] = "command:ruff=0.0.0"
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
