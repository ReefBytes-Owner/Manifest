"""Graph, path, profile, and preparation semantics for project checks."""

from __future__ import annotations

import pytest

from manifest_agent.checks.registry import load_registry, resolve_checks
from tests.python.manifest_agent.check_registry_fixtures import (
    _check,
    _preparation,
)
from tests.python.manifest_agent.check_registry_fixtures import (
    _registry_file as _registry_file,
)


def test_dependency_cycle_is_rejected(registry_file):
    checks = [
        _check("lint.a", dependencies=["lint.b"]),
        _check("lint.b", dependencies=["lint.a"]),
    ]

    with pytest.raises(ValueError, match="cycle"):
        load_registry(registry_file(checks=checks))


def test_cross_group_dependency_is_rejected(registry_file):
    checks = [
        _check("lint.a", group="lint", dependencies=["test.b"]),
        _check("test.b", group="test"),
    ]

    with pytest.raises(ValueError, match="cross-group"):
        load_registry(registry_file(checks=checks))


@pytest.mark.parametrize(
    "target,field,bad_path",
    [
        ("check", "cwd", "/tmp"),
        ("check", "cwd", "../outside"),
        ("check", "inputs", "src/../../outside"),
        ("preparation", "cwd", "/tmp"),
        ("preparation", "inputs", "../outside"),
        ("preparation", "outputs", "/tmp/output"),
        ("preparation", "outputs", "generated/../../outside"),
    ],
)
def test_absolute_or_escaping_paths_are_rejected(
    registry_file, target, field, bad_path
):
    if target == "check":
        record = _check("lint.a")
        record[field] = bad_path if field == "cwd" else [bad_path]
        kwargs = {"checks": [record]}
    else:
        value: object = bad_path if field == "cwd" else [bad_path]
        kwargs = {"candidate_preparations": [_preparation(**{field: value})]}

    with pytest.raises(ValueError, match="candidate-relative"):
        load_registry(registry_file(**kwargs))


@pytest.mark.parametrize(
    "target,field,bad_path",
    [
        (target, field, bad_path)
        for target, field in [
            ("check", "cwd"),
            ("check", "inputs"),
            ("preparation", "cwd"),
            ("preparation", "inputs"),
            ("preparation", "outputs"),
        ]
        for bad_path in [r"\rooted", r"C:drive-relative"]
    ],
)
def test_windows_anchored_or_drive_paths_are_rejected(
    registry_file, target, field, bad_path
):
    profiles = {
        "quick": ["lint.a"],
        "full": ["lint.a"],
        "security": ["lint.a"],
        "release": ["lint.a"],
    }
    if target == "check":
        record = _check("lint.a")
        record[field] = bad_path if field == "cwd" else [bad_path]
        kwargs = {"checks": [record], "profiles": profiles}
    else:
        value: object = bad_path if field == "cwd" else [bad_path]
        kwargs = {"candidate_preparations": [_preparation(**{field: value})]}

    with pytest.raises(ValueError, match="candidate-relative"):
        load_registry(registry_file(**kwargs))


def test_honors_status_contract_requires_repo_owned_check_body(registry_file):
    check = _check("lint.a")
    check["honors_status_contract"] = True
    check["argv"] = ["python", "-m", "not_repo_owned"]
    profiles = {
        "quick": ["lint.a"],
        "full": ["lint.a"],
        "security": ["lint.a"],
        "release": ["lint.a"],
    }

    with pytest.raises(ValueError, match="honors_status_contract"):
        load_registry(registry_file(checks=[check], profiles=profiles))


def test_honors_status_contract_is_accepted_for_repo_owned_check_body(registry_file):
    check = _check("lint.a")
    check["honors_status_contract"] = True
    check["argv"] = ["python", "tools/project_checks/structure.py"]
    profiles = {
        "quick": ["lint.a"],
        "full": ["lint.a"],
        "security": ["lint.a"],
        "release": ["lint.a"],
    }

    registry = load_registry(registry_file(checks=[check], profiles=profiles))

    assert registry["checks"][0].honors_status_contract is True


@pytest.mark.parametrize("category", ["arbitrary", "integration", "lock"])
def test_unknown_check_categories_are_rejected(registry_file, category):
    check = _check("lint.a", category=category)
    profiles = {
        "quick": ["lint.a"],
        "full": ["lint.a"],
        "security": ["lint.a"],
        "release": ["lint.a"],
    }

    with pytest.raises(ValueError, match="schema"):
        load_registry(registry_file(checks=[check], profiles=profiles))


@pytest.mark.parametrize("category", ["format", "lint", "syntax"])
def test_changed_selection_is_allowed_for_quick_lint_categories(
    registry_file, category
):
    checks = [
        _check("lint.a", category=category, selection="changed"),
        _check("lint.b", dependencies=["lint.a"]),
    ]

    registry = load_registry(registry_file(checks=checks))

    assert registry["checks"][0].selection == "changed"


@pytest.mark.parametrize(
    "group,category",
    [
        ("test", "test"),
        ("test", "type"),
        ("package", "build"),
        ("package", "dependency"),
        ("security", "security"),
        ("structure", "structure"),
        ("structure", "generated"),
        ("package", "package"),
        ("test", "lint"),
    ],
)
def test_changed_selection_is_rejected_outside_quick_lint_categories(
    registry_file, group, category
):
    check = _check("check.all", group=group, category=category, selection="changed")
    profiles = {
        "quick": ["check.all"],
        "full": ["check.all"],
        "security": ["check.all"],
        "release": ["check.all"],
    }

    with pytest.raises(ValueError, match="project selection"):
        load_registry(registry_file(checks=[check], profiles=profiles))


def test_resolution_is_stable_topological_and_deduplicated(registry_file):
    checks = [
        _check("lint.root"),
        _check("lint.second", dependencies=["lint.root"]),
        _check("lint.third", dependencies=["lint.root"]),
        _check("test.root", group="test", category="test"),
    ]
    profiles = {
        "quick": ["lint.root"],
        "full": ["lint.third", "lint.second", "test.root"],
        "security": ["lint.root"],
        "release": ["lint.third", "lint.second", "test.root"],
    }
    registry = load_registry(registry_file(checks=checks, profiles=profiles))

    assert [check.id for check in resolve_checks(registry, "full", None)] == [
        "lint.root",
        "lint.second",
        "lint.third",
        "test.root",
    ]


def test_group_resolution_returns_only_exact_assigned_checks(registry_file):
    checks = [
        _check("lint.root"),
        _check("lint.child", dependencies=["lint.root"]),
        _check("test.root", group="test", category="test"),
    ]
    profiles = {
        "quick": ["lint.root"],
        "full": ["lint.child", "test.root"],
        "security": ["lint.root"],
        "release": ["lint.child", "test.root"],
    }
    registry = load_registry(registry_file(checks=checks, profiles=profiles))

    assert [check.id for check in resolve_checks(registry, "full", "lint")] == [
        "lint.root",
        "lint.child",
    ]
    assert [check.id for check in resolve_checks(registry, "full", "test")] == [
        "test.root"
    ]


@pytest.mark.parametrize("profile,group", [("missing", None), ("full", "missing")])
def test_unknown_resolution_selector_is_rejected(registry_file, profile, group):
    registry = load_registry(registry_file())

    with pytest.raises(ValueError, match="unknown"):
        resolve_checks(registry, profile, group)


@pytest.mark.parametrize("omitted", ["full", "security", "package"])
def test_release_must_include_each_obligation_independently(registry_file, omitted):
    checks = [
        _check("lint.root"),
        _check("lint.full", dependencies=["lint.root"]),
        _check("security.a", group="security", category="security"),
        _check("package.a", group="package", category="package"),
    ]
    release = ["lint.full", "security.a", "package.a"]
    release.remove(
        {"full": "lint.full", "security": "security.a", "package": "package.a"}[omitted]
    )
    profiles = {
        "quick": ["lint.root"],
        "full": ["lint.full"],
        "security": ["security.a"],
        "release": release,
    }

    with pytest.raises(ValueError, match=r"release.*full.*security.*package"):
        load_registry(registry_file(checks=checks, profiles=profiles))


def test_release_inherits_dependencies_through_profile_closure(registry_file):
    checks = [
        _check("lint.root"),
        _check("lint.full", dependencies=["lint.root"]),
        _check("security.a", group="security", category="security"),
        _check("package.a", group="package", category="package"),
    ]
    profiles = {
        "quick": ["lint.root"],
        "full": ["lint.full"],
        "security": ["security.a"],
        "release": ["lint.full", "security.a", "package.a"],
    }

    registry = load_registry(registry_file(checks=checks, profiles=profiles))

    assert [check.id for check in resolve_checks(registry, "release", None)] == [
        "lint.root",
        "lint.full",
        "security.a",
        "package.a",
    ]


def test_preparation_allows_multiple_consuming_groups(registry_file):
    registry = load_registry(registry_file(candidate_preparations=[_preparation()]))

    assert registry["candidate_preparations"][0].groups == ("lint", "structure")


@pytest.mark.parametrize("groups", [[], ["unknown"]])
def test_preparation_rejects_empty_or_unknown_consuming_groups(registry_file, groups):
    with pytest.raises(ValueError, match="groups"):
        load_registry(
            registry_file(candidate_preparations=[_preparation(groups=groups)])
        )


def test_preparation_requires_declared_outputs(registry_file):
    with pytest.raises(ValueError, match="outputs"):
        load_registry(registry_file(candidate_preparations=[_preparation(outputs=[])]))


@pytest.mark.parametrize("field", ["network", "installs_dependencies"])
def test_preparation_rejects_requested_network_or_installation(registry_file, field):
    with pytest.raises(ValueError, match="schema"):
        load_registry(
            registry_file(candidate_preparations=[_preparation(**{field: True})])
        )


@pytest.mark.parametrize("executable", ["curl", "wget", "npm", "npx", "pip", "uv"])
def test_preparation_rejects_known_network_or_package_manager_executable(
    registry_file, executable
):
    tools = {
        "python": {
            "executable": "python",
            "version_argv": ["python", "--version"],
            "expected_version": "3.14.0",
            "required_modules": [],
        },
        "unsafe": {
            "executable": executable,
            "version_argv": [executable, "--version"],
            "expected_version": "1.0.0",
            "required_modules": [],
        },
    }
    preparation = _preparation(
        argv=[executable, "generate"], tool="unsafe", version="1.0.0"
    )

    with pytest.raises(ValueError, match="network or package-manager"):
        load_registry(registry_file(tools=tools, candidate_preparations=[preparation]))
