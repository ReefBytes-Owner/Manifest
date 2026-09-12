"""C7k step 2 (phase-3-5-decisions.md Correction 9, rule 2): offline tests
for `toolchain_pythonpath`'s `uv.lock` parser and layout detection, against
a hand-written `uv.lock` snippet -- never the real repository lock, so this
stays independent of what the repo's own dependencies happen to be.

Covers the three shapes the spec calls out: `editable`, `directory`, and a
path dependency whose layout cannot be determined (must BLOCK, never guess).
"""

from __future__ import annotations

import os
from pathlib import Path

from manifest_agent.checks import toolchain_pythonpath as pythonpath_mod

_ROOT_ENTRY = """
[[package]]
name = "root-project"
version = "0.1.0"
source = { editable = "." }
"""

_SRC_LAYOUT_PYPROJECT = """
[project]
name = "editable-dep"

[tool.hatch.build.targets.wheel]
packages = ["src/editable_dep"]
"""

_FLAT_DEV_MODE_PYPROJECT = """
[project]
name = "directory-dep"

[tool.hatch.build]
dev-mode-dirs = [".."]

[tool.hatch.build.targets.wheel]
packages = ["."]

[tool.hatch.build.targets.wheel.sources]
"." = "directory_dep"
"""

_NO_LAYOUT_PYPROJECT = """
[project]
name = "undeterminable-dep"
"""


def _dependency_dir(repo: Path, relative: str, pyproject: str) -> None:
    directory = repo / relative
    directory.mkdir(parents=True)
    (directory / "pyproject.toml").write_text(pyproject)


def _write_lock(repo: Path, *package_entries: str) -> None:
    (repo / "uv.lock").write_text(_ROOT_ENTRY + "".join(package_entries))


def _package_entry(name: str, key: str, relative: str) -> str:
    return f"""
[[package]]
name = "{name}"
version = "0.1.0"
source = {{ {key} = "{relative}" }}
"""


class TestLocalPathDependencyLayouts:
    def test_src_layout_editable_dependency_resolves_under_src(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _dependency_dir(repo, "deps/editable_dep", _SRC_LAYOUT_PYPROJECT)
        (repo / "deps/editable_dep/src/editable_dep").mkdir(parents=True)
        _write_lock(
            repo, _package_entry("editable-dep", "editable", "deps/editable_dep")
        )

        candidate_root = tmp_path / "candidate"
        roots = pythonpath_mod.candidate_path_dependency_roots(repo, candidate_root)

        assert not isinstance(roots, pythonpath_mod.BlockedPythonPath), roots
        assert roots == (candidate_root / "deps" / "editable_dep" / "src",)

    def test_flat_dev_mode_dirs_directory_dependency_resolves_to_parent(
        self, tmp_path: Path
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _dependency_dir(repo, "deps/directory_dep", _FLAT_DEV_MODE_PYPROJECT)
        _write_lock(
            repo, _package_entry("directory-dep", "directory", "deps/directory_dep")
        )

        candidate_root = tmp_path / "candidate"
        roots = pythonpath_mod.candidate_path_dependency_roots(repo, candidate_root)

        assert not isinstance(roots, pythonpath_mod.BlockedPythonPath), roots
        # The dependency directory itself is the importable package, so its
        # PARENT is what belongs on PYTHONPATH.
        assert roots == (candidate_root / "deps",)

    def test_undeterminable_layout_blocks_with_a_clear_reason(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _dependency_dir(repo, "deps/undeterminable_dep", _NO_LAYOUT_PYPROJECT)
        _write_lock(
            repo,
            _package_entry(
                "undeterminable-dep", "directory", "deps/undeterminable_dep"
            ),
        )

        candidate_root = tmp_path / "candidate"
        result = pythonpath_mod.candidate_path_dependency_roots(repo, candidate_root)

        assert isinstance(result, pythonpath_mod.BlockedPythonPath)
        assert result.reason == (
            "toolchain: path dependency undeterminable-dep layout undetermined"
        )

    def test_multiple_path_dependencies_all_resolve_together(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _dependency_dir(repo, "deps/editable_dep", _SRC_LAYOUT_PYPROJECT)
        (repo / "deps/editable_dep/src/editable_dep").mkdir(parents=True)
        _dependency_dir(repo, "deps/directory_dep", _FLAT_DEV_MODE_PYPROJECT)
        _write_lock(
            repo,
            _package_entry("editable-dep", "editable", "deps/editable_dep"),
            _package_entry("directory-dep", "directory", "deps/directory_dep"),
        )

        candidate_root = tmp_path / "candidate"
        roots = pythonpath_mod.candidate_path_dependency_roots(repo, candidate_root)

        assert not isinstance(roots, pythonpath_mod.BlockedPythonPath), roots
        assert roots == (
            candidate_root / "deps" / "editable_dep" / "src",
            candidate_root / "deps",
        )

    def test_root_projects_own_editable_dot_entry_is_never_a_path_dependency(
        self, tmp_path: Path
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_lock(repo)

        candidate_root = tmp_path / "candidate"
        roots = pythonpath_mod.candidate_path_dependency_roots(repo, candidate_root)

        assert roots == ()


class TestWithCandidatePythonPath:
    def test_candidate_src_comes_first_then_derived_roots_then_inherited(
        self, tmp_path: Path
    ):
        candidate_root = tmp_path / "candidate"
        extra = (candidate_root / "deps" / "editable_dep" / "src",)

        env = pythonpath_mod.with_candidate_pythonpath(
            {"PYTHONPATH": "/inherited"}, candidate_root, extra
        )

        assert env["PYTHONPATH"] == os.pathsep.join(
            [str(candidate_root / "src"), str(extra[0]), "/inherited"]
        )
