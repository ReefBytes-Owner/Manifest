"""Unit coverage for `tools/project_checks/packages_build.py` (C7g split of
`packages.py`'s uv/build-backend resolution seam) that isn't already
exercised end-to-end through `packages.main()` in
`test_project_check_bodies.py`.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from tools.project_checks import packages_build


def test_backend_blocks_when_pyproject_declares_no_build_backend(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    try:
        packages_build.backend(project)
    except packages_build.BlockedError as error:
        assert "build metadata unavailable" in str(error)
    else:
        raise AssertionError("expected BlockedError")


def test_backend_blocks_when_declared_backend_is_not_importable(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['definitely-not-a-real-package']\n"
        "build-backend = 'definitely_not_a_real_package.build'\n",
        encoding="utf-8",
    )
    try:
        packages_build.backend(project)
    except packages_build.BlockedError as error:
        assert "preprovisioned build backend unavailable" in str(error)
    else:
        raise AssertionError("expected BlockedError")


def test_backend_passes_for_an_importable_backend(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['setuptools']\nbuild-backend = 'json'\n",
        encoding="utf-8",
    )
    # `json` is always importable and only used here as a stand-in module
    # name -- the point under test is find_spec() succeeding, not a real
    # PEP 517 backend.
    packages_build.backend(project)


def test_build_destination_rejects_a_symlinked_destination(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    real = tmp_path / "elsewhere"
    real.mkdir()
    (output / "package-coordinator").symlink_to(real)
    try:
        packages_build.build_destination(output, "package.coordinator")
    except packages_build.BlockedError as error:
        assert "unsafe" in str(error)
    else:
        raise AssertionError("expected BlockedError")


def test_build_destination_rejects_a_nonempty_destination(tmp_path: Path) -> None:
    output = tmp_path / "out"
    destination = output / "package-coordinator"
    destination.mkdir(parents=True)
    (destination / "stale.whl").write_bytes(b"")
    try:
        packages_build.build_destination(output, "package.coordinator")
    except packages_build.BlockedError as error:
        assert "must be empty" in str(error)
    else:
        raise AssertionError("expected BlockedError")


def test_wheel_contains_true_and_false(tmp_path: Path) -> None:
    wheel = tmp_path / "pkg.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("manifest_agent/data/project-checks.schema.json", "{}")
    assert packages_build.wheel_contains(
        wheel, "manifest_agent/data/project-checks.schema.json"
    )
    assert not packages_build.wheel_contains(wheel, "missing/member.json")
