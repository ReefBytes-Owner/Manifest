"""Schema and normalization contracts for project-check registries."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from manifest_agent.checks.models import CheckSpec, PreparationSpec
from manifest_agent.checks.registry import load_registry, resolve_checks
from tests.python.manifest_agent.check_registry_fixtures import (
    _check,
    _preparation,
)
from tests.python.manifest_agent.check_registry_fixtures import (
    _registry_file as _registry_file,
)


def test_load_registry_returns_frozen_records(registry_file):
    registry = load_registry(registry_file(candidate_preparations=[_preparation()]))

    assert registry["checks"] == (
        CheckSpec(
            id="lint.a",
            category="lint",
            group="lint",
            argv=("python", "-m", "example_a"),
            cwd=".",
            inputs=("src/**/*.py",),
            dependencies=(),
            timeout_seconds=10.0,
            selection="changed",
            tool="python",
            version="3.14.0",
            pass_filenames=False,
        ),
        CheckSpec(
            id="lint.b",
            category="lint",
            group="lint",
            argv=("python", "-m", "example_b"),
            cwd=".",
            inputs=("src",),
            dependencies=("lint.a",),
            timeout_seconds=20.5,
            selection="project",
            tool="python",
            version="3.14.0",
        ),
    )
    assert registry["candidate_preparations"] == (
        PreparationSpec(
            id="prepare.skills",
            argv=("python", "tools/generate.py", ".apm/skills"),
            cwd=".",
            inputs=("plugins",),
            outputs=(".apm/skills",),
            groups=("lint", "structure"),
            timeout_seconds=30.0,
            tool="python",
            version="3.14.0",
        ),
    )
    with pytest.raises(FrozenInstanceError):
        registry["checks"][0].id = "changed"
    with pytest.raises(FrozenInstanceError):
        registry["candidate_preparations"][0].id = "changed"


def test_load_registry_preserves_pending_coverage(registry_file):
    pending = {
        "quick": [],
        "full": ["whole-project typing"],
        "security": [],
        "release": [],
    }
    registry = load_registry(registry_file(coverage_pending=pending))

    assert registry["coverage_pending"]["full"] == ("whole-project typing",)
    assert [check.id for check in resolve_checks(registry, "full", None)] == [
        "lint.a",
        "lint.b",
    ]


def test_pass_filenames_is_validated_and_normalized(registry_file):
    first = _check("lint.a")
    first["pass_filenames"] = True
    registry = load_registry(
        registry_file(checks=[first, _check("lint.b", dependencies=["lint.a"])])
    )
    invalid = _check("lint.a")
    invalid["pass_filenames"] = "yes"

    assert registry["checks"][0].pass_filenames is True
    assert registry["checks"][1].pass_filenames is False
    with pytest.raises(ValueError, match="schema"):
        load_registry(registry_file(checks=[invalid]))


def test_pre_amendment_v1_registry_defaults_filename_forwarding_by_selection():
    fixture = (
        Path(__file__).parent / "fixtures" / "project-checks-v1-pre-pass-filenames.json"
    )

    registry = load_registry(fixture)

    assert registry["checks"][0].selection == "changed"
    assert registry["checks"][0].pass_filenames is True
    assert registry["checks"][1].selection == "project"
    assert registry["checks"][1].pass_filenames is False


def test_direct_check_specs_default_filename_forwarding_by_selection():
    values = {
        "id": "lint.direct",
        "category": "lint",
        "group": "lint",
        "argv": ("python", "-m", "lint.direct"),
        "cwd": ".",
        "inputs": ("src",),
        "dependencies": (),
        "timeout_seconds": 10.0,
        "tool": "python",
        "version": "3.14.0",
    }

    assert CheckSpec(selection="changed", **values).pass_filenames is True
    assert CheckSpec(selection="project", **values).pass_filenames is False


def test_malformed_json_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON"):
        load_registry(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("unexpected", True),
        ("schema_version", 2),
    ],
)
def test_invalid_root_fields_are_rejected(registry_file, field, value):
    with pytest.raises(ValueError, match="schema"):
        load_registry(registry_file(**{field: value}))


@pytest.mark.parametrize(
    "boundary", ["check", "tool", "preparation", "profiles", "coverage"]
)
def test_unknown_nested_fields_are_rejected(registry_file, boundary):
    kwargs: dict[str, object] = {}
    if boundary == "check":
        check = _check("lint.a")
        check["unexpected"] = True
        kwargs["checks"] = [check]
    elif boundary == "tool":
        kwargs["tools"] = {
            "python": {
                "executable": "python",
                "version_argv": ["python", "--version"],
                "expected_version": "3.14.0",
                "required_modules": [],
                "unexpected": True,
            }
        }
    elif boundary == "preparation":
        preparation = _preparation()
        preparation["unexpected"] = True
        kwargs["candidate_preparations"] = [preparation]
    elif boundary == "profiles":
        kwargs["profiles"] = {
            "quick": ["lint.a"],
            "full": ["lint.b"],
            "security": ["lint.a"],
            "release": ["lint.b"],
            "unexpected": ["lint.a"],
        }
    else:
        kwargs["coverage_pending"] = {
            "quick": [],
            "full": [],
            "security": [],
            "release": [],
            "unexpected": [],
        }

    with pytest.raises(ValueError, match="schema"):
        load_registry(registry_file(**kwargs))


def test_schema_is_closed_at_every_object_boundary():
    schema_path = Path(__file__).parents[3] / "schemas" / "project-checks.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    def assert_closed(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for value in node.values():
                assert_closed(value)
        elif isinstance(node, list):
            for value in node:
                assert_closed(value)

    assert_closed(schema)


def test_duplicate_json_object_keys_are_rejected(tmp_path):
    path = tmp_path / "duplicate-key.json"
    path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_registry(path)


@pytest.mark.parametrize("kind", ["check", "preparation"])
def test_duplicate_record_ids_are_rejected(registry_file, kind):
    kwargs: dict[str, object]
    if kind == "check":
        kwargs = {"checks": [_check("lint.a"), _check("lint.a")]}
    else:
        kwargs = {"candidate_preparations": [_preparation(), _preparation()]}

    with pytest.raises(ValueError, match=r"duplicate .* id"):
        load_registry(registry_file(**kwargs))


@pytest.mark.parametrize("target", ["check", "preparation", "version"])
def test_nul_in_argv_is_rejected(registry_file, target):
    kwargs: dict[str, object] = {}
    if target == "check":
        check = _check("lint.a")
        check["argv"] = ["python", "bad\0arg"]
        kwargs["checks"] = [check]
    elif target == "preparation":
        kwargs["candidate_preparations"] = [_preparation(argv=["python", "bad\0arg"])]
    else:
        kwargs["tools"] = {
            "python": {
                "executable": "python",
                "version_argv": ["python", "bad\0arg"],
                "expected_version": "3.14.0",
                "required_modules": [],
            }
        }

    with pytest.raises(ValueError, match="NUL"):
        load_registry(registry_file(**kwargs))


@pytest.mark.parametrize("timeout", [-1, 0, float("nan"), float("inf")])
@pytest.mark.parametrize("target", ["check", "preparation"])
def test_nonpositive_or_nonfinite_timeout_is_rejected(registry_file, timeout, target):
    if target == "check":
        check = _check("lint.a")
        check["timeout_seconds"] = timeout
        kwargs = {"checks": [check]}
    else:
        kwargs = {"candidate_preparations": [_preparation(timeout_seconds=timeout)]}

    with pytest.raises(ValueError, match="timeout_seconds"):
        load_registry(registry_file(**kwargs))


@pytest.mark.parametrize(
    "reference", ["dependency", "profile", "tool", "preparation tool"]
)
def test_unknown_references_are_rejected(registry_file, reference):
    kwargs: dict[str, object] = {}
    if reference == "dependency":
        kwargs["checks"] = [_check("lint.a", dependencies=["missing"])]
    elif reference == "profile":
        kwargs["profiles"] = {
            "quick": ["missing"],
            "full": ["lint.b"],
            "security": ["lint.a"],
            "release": ["lint.b"],
        }
    elif reference == "tool":
        kwargs["checks"] = [_check("lint.a", tool="missing")]
    else:
        kwargs["candidate_preparations"] = [_preparation(tool="missing")]

    with pytest.raises(ValueError, match="unknown"):
        load_registry(registry_file(**kwargs))


@pytest.mark.parametrize("target", ["check", "preparation"])
def test_tool_version_mismatch_is_rejected(registry_file, target):
    if target == "check":
        kwargs = {"checks": [_check("lint.a", version="3.14.1")]}
    else:
        kwargs = {"candidate_preparations": [_preparation(version="3.14.1")]}

    with pytest.raises(ValueError, match="version"):
        load_registry(registry_file(**kwargs))


def test_tool_version_probe_may_use_a_separate_validated_executable(registry_file):
    tools = {
        "python": {
            "executable": "python",
            "version_argv": ["version-probe", "python", "3.14.0"],
            "expected_version": "3.14.0",
            "required_modules": [],
        }
    }

    registry = load_registry(registry_file(tools=tools))

    assert registry["tools"]["python"]["version_argv"] == (
        "version-probe",
        "python",
        "3.14.0",
    )
