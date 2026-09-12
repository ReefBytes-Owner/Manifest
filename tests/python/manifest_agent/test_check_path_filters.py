"""Focused validation and matching tests for declarative path filters."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from manifest_agent.checks.models import Candidate, CheckSpec
from manifest_agent.checks.path_filters import filter_inputs
from manifest_agent.checks.registry import load_registry
from manifest_agent.checks.runner import _selection_outcome
from tests.python.manifest_agent.check_registry_fixtures import (
    _check,
)
from tests.python.manifest_agent.check_registry_fixtures import (
    _registry_file as _registry_file,
)


def _spec(**overrides: object) -> CheckSpec:
    values = {
        "id": "lint.paths",
        "category": "lint",
        "group": "lint",
        "argv": ("python", "check.py"),
        "cwd": ".",
        "inputs": (".",),
        "dependencies": (),
        "timeout_seconds": 10.0,
        "selection": "changed",
        "tool": "python",
        "version": "3.14.0",
    }
    values.update(overrides)
    return CheckSpec(**values)


def test_check_path_filters_are_normalized_with_safe_defaults(registry_file):
    filtered = _check("lint.a")
    filtered.update(
        include_regex=r"^src/",
        exclude_regex=r"^src/vendor/",
        types=["python"],
        types_or=["python", "pyi"],
    )
    registry = load_registry(
        registry_file(checks=[filtered, _check("lint.b", dependencies=["lint.a"])])
    )

    assert registry["checks"][0] == _spec(
        id="lint.a",
        argv=("python", "-m", "lint.a"),
        inputs=("src",),
        selection="project",
        include_regex=r"^src/",
        exclude_regex=r"^src/vendor/",
        types=("python",),
        types_or=("python", "pyi"),
    )
    assert registry["checks"][1].include_regex == ""
    assert registry["checks"][1].exclude_regex == r"$^"
    assert registry["checks"][1].types == ()
    assert registry["checks"][1].types_or == ()


@pytest.mark.parametrize("field", ["include_regex", "exclude_regex"])
def test_invalid_path_filter_regex_is_rejected(registry_file, field):
    check = _check("lint.a")
    check[field] = "["

    with pytest.raises(ValueError, match=field):
        load_registry(registry_file(checks=[check, _check("lint.b")]))


@pytest.mark.parametrize("field", ["types", "types_or"])
def test_unknown_path_filter_type_is_rejected(registry_file, field):
    check = _check("lint.a")
    check[field] = ["unknown-language"]

    with pytest.raises(ValueError, match=field):
        load_registry(registry_file(checks=[check, _check("lint.b")]))


@pytest.mark.parametrize(
    ("type_tags", "expected"),
    [
        (("python",), ("selectors/python.py",)),
        (("pyi",), ("selectors/stub.pyi",)),
        (("shell",), ("selectors/script.sh", "selectors/shebang")),
        (("markdown",), ("selectors/readme.md",)),
        (("rust",), ("selectors/main.rs",)),
        (
            ("javascript", "jsx", "ts", "tsx"),
            (
                "selectors/code.js",
                "selectors/code.ts",
                "selectors/view.jsx",
                "selectors/view.tsx",
            ),
        ),
        (("terraform",), ("selectors/main.tf", "selectors/values.tfvars")),
    ],
)
def test_declarative_filters_apply_include_exclude_and_repository_type_tags(
    tmp_path: Path, type_tags: tuple[str, ...], expected: tuple[str, ...]
):
    bodies = {
        "python.py": "VALUE = 1\n",
        "stub.pyi": "VALUE: int\n",
        "script.sh": "#!/bin/sh\n",
        "shebang": "#!/usr/bin/env bash\n",
        "readme.md": "# Fixture\n",
        "main.rs": "fn main() {}\n",
        "code.js": "export const value = 1;\n",
        "view.jsx": "export const View = () => null;\n",
        "code.ts": "export const value: number = 1;\n",
        "view.tsx": "export const View = () => null;\n",
        "main.tf": "terraform {}\n",
        "values.tfvars": "value = 1\n",
        "excluded.py": "VALUE = 2\n",
    }
    folder = tmp_path / "selectors"
    folder.mkdir()
    for name, body in bodies.items():
        path = folder / name
        path.write_text(body, encoding="utf-8")
        if name == "shebang":
            path.chmod(0o755)
    names = tuple(sorted(f"selectors/{name}" for name in bodies))
    check = _spec(
        include_regex=r"^selectors/",
        exclude_regex=r"^selectors/excluded\.py$",
        types_or=type_tags,
    )

    assert filter_inputs(check, tmp_path, names) == expected


def test_project_filter_with_no_matching_applicable_files_is_not_applicable(
    tmp_path: Path,
):
    source = tmp_path / "value.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    candidate = Candidate(tmp_path, tmp_path, "h", "b", "t", "s", (), tmp_path / "r")
    check = replace(
        _spec(), selection="project", include_regex=r"^selectors/", types_or=("go",)
    )

    selected, outcome = _selection_outcome(check, candidate, ("value.py",))

    assert selected == ()
    assert outcome is not None
    assert outcome.status == "NOT_APPLICABLE"
    assert "zero applicable project paths" in outcome.diagnostics


def test_regex_only_filter_keeps_only_regular_files_without_requiring_a_suffix(
    tmp_path: Path,
):
    regular = tmp_path / "extensionless"
    regular.write_text("plain text\n", encoding="utf-8")
    (tmp_path / "directory").mkdir()
    (tmp_path / "link").symlink_to(regular)
    selected = ("extensionless", "directory", "link", "missing")

    assert filter_inputs(_spec(include_regex=r".*"), tmp_path, selected) == (
        "extensionless",
    )


@pytest.mark.parametrize(
    ("body", "expected_type"),
    [
        ("#!/usr/bin/python3\n", "python"),
        ("#!/bin/bash\n", "shell"),
        ("#!/usr/bin/env python3\n", "python"),
        ("#!/usr/bin/env sh\n", "shell"),
        ("#!/usr/bin/env -S python3 -I\n", "python"),
        ("#!/usr/bin/env -S bash -eu\n", "shell"),
    ],
)
def test_executable_shebangs_preserve_python_and_shell_types(
    tmp_path: Path, body: str, expected_type: str
):
    path = tmp_path / "script"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)

    assert filter_inputs(
        _spec(include_regex=r".*", types=(expected_type,)),
        tmp_path,
        ("script",),
    ) == ("script",)


def test_non_executable_extensionless_shebang_has_no_interpreter_type(tmp_path: Path):
    path = tmp_path / "script"
    path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    path.chmod(0o644)

    assert (
        filter_inputs(
            _spec(include_regex=r".*", types=("python",)), tmp_path, ("script",)
        )
        == ()
    )


@pytest.mark.parametrize(
    ("name", "expected_type"),
    [
        ("module.py", "python"),
        ("interface.pyi", "pyi"),
        ("script.sh", "shell"),
        ("script.bash", "shell"),
        ("main.go", "go"),
        ("readme.md", "markdown"),
        ("main.rs", "rust"),
        ("code.js", "javascript"),
        ("view.jsx", "jsx"),
        ("code.ts", "ts"),
        ("view.tsx", "tsx"),
        ("main.tf", "terraform"),
        ("values.tfvars", "terraform"),
    ],
)
def test_suffixes_preserve_declared_repository_types(
    tmp_path: Path, name: str, expected_type: str
):
    (tmp_path / name).write_text("fixture\n", encoding="utf-8")

    assert filter_inputs(
        _spec(include_regex=r".*", types=(expected_type,)), tmp_path, (name,)
    ) == (name,)


def test_known_suffix_takes_precedence_over_executable_shebang(tmp_path: Path):
    path = tmp_path / "script.py"
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)

    assert (
        filter_inputs(
            _spec(include_regex=r".*", types=("shell",)), tmp_path, ("script.py",)
        )
        == ()
    )


def test_types_and_types_or_are_combined_as_intersection_requirements(tmp_path: Path):
    python = tmp_path / "module.py"
    python.write_text("VALUE = 1\n", encoding="utf-8")
    shell = tmp_path / "script.sh"
    shell.write_text("#!/bin/sh\n", encoding="utf-8")
    check = _spec(
        include_regex=r".*",
        types=("python",),
        types_or=("shell", "pyi"),
    )

    assert filter_inputs(check, tmp_path, ("module.py", "script.sh")) == ()
    matching = replace(check, types_or=("python", "shell"))
    assert filter_inputs(matching, tmp_path, ("module.py", "script.sh")) == (
        "module.py",
    )


def test_no_filter_preserves_legacy_selection_without_filesystem_narrowing(
    tmp_path: Path,
):
    selected = ("missing", "directory", "link")

    assert filter_inputs(_spec(), tmp_path, selected) == selected
