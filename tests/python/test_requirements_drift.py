"""CI pin parity: requirements-*.txt must match configs/claude/uv.lock groups."""

from __future__ import annotations

import importlib
import re
import tomllib
from importlib import metadata
from pathlib import Path

import pytest
import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[2]
UV_LOCK = REPO_ROOT / "configs/claude/uv.lock"

REQUIREMENTS_FILES: dict[Path, set[str] | None] = {
    REPO_ROOT / "tests/requirements-runtime.txt": None,  # manifest-runtime core deps
    REPO_ROOT / "tests/requirements-smoke.txt": {"smoke"},
    REPO_ROOT / "tests/requirements-smoke-agent.txt": {"smoke-agent"},
}

ROOT_PYPROJECT = REPO_ROOT / "pyproject.toml"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
CI_REQUIREMENTS = REPO_ROOT / "tests/requirements-ci.txt"
MODEL_POLICY_PYPROJECT = (
    REPO_ROOT / "configs/claude/scripts/manifest_model_policy/pyproject.toml"
)


def _load_uv_lock() -> tuple[dict[str, str], dict[str, set[str]]]:
    data = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    versions = {pkg["name"]: pkg["version"] for pkg in data["package"]}

    groups: dict[str, set[str]] = {"core": set()}
    for pkg in data["package"]:
        if pkg["name"] != "manifest-runtime":
            continue
        groups["core"] = {dep["name"] for dep in pkg["dependencies"]}
        dev = pkg.get("dev-dependencies", {})
        for group_name, deps in dev.items():
            groups[group_name] = {dep["name"] for dep in deps}
        break

    if not groups["core"]:
        pytest.fail(f"manifest-runtime entry missing from {UV_LOCK}")

    return versions, groups


def _parse_requirements(path: Path) -> list[Requirement]:
    requirements: list[Requirement] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        requirements.append(Requirement(stripped))
    return requirements


def test_ci_requirements_include_model_policy_editable_build_backend() -> None:
    """Offline editable fixtures need their declared backend in the test process."""
    policy = tomllib.loads(MODEL_POLICY_PYPROJECT.read_text(encoding="utf-8"))
    required = {
        _normalize_name(Requirement(spec).name)
        for spec in policy["build-system"]["requires"]
    }
    installed = {
        _normalize_name(requirement.name)
        for requirement in _parse_requirements(CI_REQUIREMENTS)
    }
    assert required <= installed


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@pytest.mark.parametrize("req_path,allowed_groups", list(REQUIREMENTS_FILES.items()))
def test_requirements_pins_match_uv_lock(
    req_path: Path, allowed_groups: set[str] | None
) -> None:
    assert req_path.is_file(), f"missing requirements file: {req_path}"

    locked_versions, groups = _load_uv_lock()
    requirements = _parse_requirements(req_path)

    for req in requirements:
        name = _normalize_name(req.name)
        locked_version = locked_versions.get(name)
        assert locked_version is not None, (
            f"{req_path.name}: {req.name} not found in {UV_LOCK.name}"
        )

        locked = Version(locked_version)
        assert locked in req.specifier, (
            f"{req_path.name}: {req.name} locked at {locked_version} "
            f"does not satisfy {req.specifier}"
        )

        if allowed_groups is None:
            ok = name in groups["core"]
            # anthropic is an optional [claude] SDK dep (uv --group claude) kept
            # in the runtime requirements file for CI SDK tests (see its header);
            # allow it from the claude group, analogous to the pyyaml case below.
            if not ok and name == "anthropic":
                ok = name in groups.get("claude", set())
            assert ok, (
                f"{req_path.name}: {req.name} is not a manifest-runtime core dependency"
            )
        else:
            in_group = any(name in groups[g] for g in allowed_groups)
            # PyYAML is core runtime; smoke.txt keeps it for legacy CI installs.
            if req_path.name == "requirements-smoke.txt" and name == "pyyaml":
                in_group = name in groups["core"]
            assert in_group, (
                f"{req_path.name}: {req.name} not in uv.lock groups "
                f"{sorted(allowed_groups)} (or core for pyyaml)"
            )


def _uv_path_sources(data: dict) -> dict[str, str]:
    """Map normalized dist name -> repo-relative path, for uv `path` sources only."""
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    return {
        _normalize_name(name): str(spec["path"]).rstrip("/")
        for name, spec in sources.items()
        if isinstance(spec, dict) and "path" in spec
    }


def _requirement_name(entry: object) -> str | None:
    """Normalized name for a requirement string, or None for non-requirements.

    PEP 735 dependency-group entries may be `{include-group = "..."}` tables
    rather than requirement strings; those name a group, not a distribution.
    """
    if not isinstance(entry, str):
        return None
    try:
        return _normalize_name(Requirement(entry).name)
    except InvalidRequirement:
        return None


def _pinned_names(data: dict) -> set[str]:
    """Every distribution this project can pull in, across all dependency tables.

    Checking only `project.dependencies` would miss three real install paths:
    extras, PEP 735 dependency groups, and build requirements — the last of
    which runs arbitrary code at build time, so it is the worst one to miss.
    """
    project = data.get("project", {})
    entries: list[object] = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        entries.extend(extra)
    for group in data.get("dependency-groups", {}).values():
        entries.extend(group)
    entries.extend(data.get("build-system", {}).get("requires", []))
    return {name for name in map(_requirement_name, entries) if name}


_PYPROJECT_SKIP = {".venv", "node_modules", "templates", ".git", "__pycache__"}


def _repo_pyprojects() -> list[Path]:
    """Every pyproject in the repo, however deeply nested.

    Globbed rather than listed: a hand-maintained list silently stops covering
    projects added later, which is the failure this guard exists to prevent.
    """
    return sorted(
        path
        for path in REPO_ROOT.rglob("pyproject.toml")
        if not _PYPROJECT_SKIP & set(path.relative_to(REPO_ROOT).parts)
    )


# Distributions this repo authors that are genuinely published to PyPI, and so
# may legitimately be resolved from the index. Empty today: every project here
# is repo-local. An entry belongs here only once the name is actually claimed on
# PyPI by this project's owners — otherwise it re-opens the confusion window.
_PUBLISHED_DISTRIBUTIONS: set[str] = set()


def _authored_distributions() -> dict[str, Path]:
    """Every distribution name this repo itself defines, mapped to its pyproject.

    Derived from `[project].name`, deliberately NOT from `[tool.uv.sources]`.
    Reading the sources table would make the check circular: a new repo-local
    package that nobody mapped would never enter the set, so the very case the
    guard exists to catch — an unmapped local pin — would pass silently.
    """
    authored: dict[str, Path] = {}
    for path in _repo_pyprojects():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        name = data.get("project", {}).get("name")
        if name:
            authored[_normalize_name(name)] = path
    return authored


def _local_only_distributions() -> set[str]:
    """Repo-authored names that are NOT published, so must never hit an index."""
    return set(_authored_distributions()) - _PUBLISHED_DISTRIBUTIONS


def _ci_install_steps() -> list[str]:
    """Every `run:` script in the workflow, as executable text.

    Parsed rather than substring-matched: a bare `in workflow` test is satisfied
    by a comment, a different job, or a command placed *after* `pip install .` —
    none of which establish the ordering the guard claims to enforce.
    """
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    scripts: list[str] = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run = step.get("run")
            if isinstance(run, str):
                scripts.append(run)
    return scripts


def test_path_sourced_pins_are_installed_from_path_in_ci() -> None:
    """An unpublished pin must be satisfied locally before pip consults an index.

    Ordering is the whole control: once the local distribution is installed, the
    pin in `pip install .` is already satisfied and pip never queries PyPI. So
    this asserts both commands live in the SAME shell script and that the path
    install comes first — not merely that both strings appear somewhere.
    """
    data = tomllib.loads(ROOT_PYPROJECT.read_text(encoding="utf-8"))
    sources = _uv_path_sources(data)
    targets = sorted(set(sources) & _pinned_names(data) & _local_only_distributions())
    assert targets, (
        f"{ROOT_PYPROJECT.name}: expected an unpublished path-sourced pin to guard"
    )

    for name in targets:
        # Editable and plain path installs both satisfy the pin locally, which
        # is what keeps pip off the index. `-e` additionally makes the import
        # resolve to the working tree, which is what
        # test_installed_local_distribution_resolves_inside_this_repository
        # checks — so accept either form rather than pinning one spelling.
        candidates = (
            f"pip install -e ./{sources[name]}",
            f"pip install ./{sources[name]}",
        )
        matching = [
            (script, found)
            for script in _ci_install_steps()
            for found in (next((c for c in candidates if c in script), None),)
            if found is not None
        ]
        assert matching, (
            f"{CI_WORKFLOW.name}: no run step installs {name} via "
            f"`{candidates[0]}` or `{candidates[1]}`"
        )
        for script, path_install in matching:
            project_install = script.find("pip install -r")
            if project_install == -1:
                continue
            assert script.find(path_install) < project_install, (
                f"{CI_WORKFLOW.name}: `{path_install}` must run BEFORE the project "
                f"install in the same step, or pip resolves {name} from PyPI"
            )


def test_every_pyproject_pinning_a_local_distribution_maps_it_to_a_path() -> None:
    """A pin on a repo-authored, unpublished name must resolve from a path.

    Otherwise installing that project with any tool resolves the name against
    PyPI, where it is unregistered and therefore claimable by anyone.
    """
    local_only = _local_only_distributions()
    assert local_only, "expected at least one repo-authored distribution"

    offenders: list[str] = []
    for path in _repo_pyprojects():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        mapped = set(_uv_path_sources(data))
        for name in sorted(_pinned_names(data) & local_only):
            if name not in mapped:
                offenders.append(f"{path.relative_to(REPO_ROOT)} pins {name}")

    assert not offenders, (
        "these projects pin a repo-authored distribution that is not published, "
        "without a [tool.uv.sources] path mapping, so the name resolves against "
        "an index: " + "; ".join(offenders)
    )


def test_installed_local_distribution_resolves_inside_this_repository() -> None:
    """Catch a squatted name actually being installed, not merely declared.

    Provenance is checked by WHERE the files landed, not by `direct_url.json`
    alone: that file ships inside the wheel, so an index package can forge it.
    An index install lands in site-packages; a path install of a repo project
    resolves back into the working tree. Absence fails rather than skips — a
    silently missing distribution would make this pass without proving anything.
    """
    depended_on: set[str] = set()
    for path in _repo_pyprojects():
        depended_on |= _pinned_names(tomllib.loads(path.read_text(encoding="utf-8")))

    targets = sorted(_local_only_distributions() & depended_on)
    assert targets, "expected a repo-authored distribution that something depends on"

    for name in targets:
        pyproject = _authored_distributions()[name]
        try:
            # Presence check only: absence must fail, not skip. The location
            # assertion below uses the imported module, not this metadata.
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            pytest.fail(
                f"{name} is declared by {pyproject.relative_to(REPO_ROOT)} but is not "
                f"installed; this guard cannot prove provenance for a missing package"
            )
        # Assert on where the EXECUTED CODE lives, not where the metadata
        # landed. dist-info always goes to site-packages, so locate_file()
        # only lands inside the repo when site-packages happens to be the
        # repo's own .venv -- true locally, false on a CI runner using a
        # hosted interpreter, which made this guard unpassable there. An
        # editable path install points the import at the working tree; an
        # index install copies files into site-packages, so this still
        # catches a squatted name.
        module = importlib.import_module(name.replace("-", "_"))
        located = Path(module.__file__).resolve()
        assert located.is_relative_to(REPO_ROOT), (
            f"{name} imports from {located}, outside this repository. It exists only "
            f"here, so an out-of-tree copy is someone else's code — treat this as a "
            f"supply-chain incident, not a test failure"
        )
