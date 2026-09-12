"""Candidate-relative `PYTHONPATH` roots for the root project's local path
dependencies (phase-3-5-decisions.md Correction 9, rule 2).

`project-env` deliberately never installs the root project itself
(`toolchain_materialize.materialize_project_env`'s `--no-install-project`),
but it DOES install the root project's own local path dependencies
(currently `manifest-model-policy`) -- editable. An editable install's
`.pth`/RECORD machinery points at wherever `manifest provision` ran FROM
(the provisioning checkout), never at the CANDIDATE a check is actually
verifying. A check that imports one of these packages through
`store:project-env/bin/python` (`test.python`, `test.hooks`) must still see
the CANDIDATE's own copy -- exactly like the root project's own `src/`,
which the candidate's own `pyproject.toml` (`[tool.pytest.ini_options]
pythonpath`) already resolves relative to the check's `cwd`.

This module derives the other half: each path dependency's import root,
relative to the candidate, parsed from the root project's own `uv.lock` --
never hard-coded, so a lockfile change changes the result. `source_root`
(the TRUSTED checkout the run started from) supplies the lockfile bytes and
each dependency's `pyproject.toml` layout; only the relative path is reused
against `candidate_root` -- the BYTES a check reads always come from the
candidate's own copy.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import toolchain
from .path_filters import forwarded_paths

if TYPE_CHECKING:
    from .models import Candidate, CheckSpec
    from .toolchain import ResolvedTool

_ROOT_PYTHONPATH_MEMBER = ("src",)
_PROJECT_ENV_BUNDLE = "project-env"


@dataclass(frozen=True)
class BlockedPythonPath:
    """A path dependency's import root could not be determined -- never
    guessed; callers must report this as BLOCKED, not silently skip it."""

    reason: str


def _local_path_dependencies(
    lock: Mapping, lock_path: Path
) -> tuple[tuple[str, str], ...] | BlockedPythonPath:
    """`(package_name, relative_dir)` for every `uv.lock` `[[package]]` whose
    `source` is `{editable = <dir>}` or `{directory = <dir>}` -- excluding
    the root project's own entry (`editable = "."`, the only shape a
    self-reference can take in a `uv.lock`)."""
    packages = lock.get("package")
    if not isinstance(packages, list):
        return BlockedPythonPath(f"toolchain: {lock_path} has no [[package]] table")
    deps: list[tuple[str, str]] = []
    for package in packages:
        if not isinstance(package, Mapping):
            continue
        name = package.get("name")
        source = package.get("source")
        if not name or not isinstance(source, Mapping):
            continue
        relative = source.get("editable") or source.get("directory")
        if relative and relative != ".":
            deps.append((str(name), str(relative)))
    return tuple(deps)


def _hatch_import_root(dep_dir: Path, hatch: Mapping) -> Path | None:
    build = hatch.get("build")
    if not isinstance(build, Mapping):
        return None
    targets = build.get("targets")
    wheel = targets.get("wheel") if isinstance(targets, Mapping) else None
    packages = wheel.get("packages") if isinstance(wheel, Mapping) else None
    if (
        isinstance(packages, list)
        and packages
        and all(isinstance(p, str) and p.startswith("src/") for p in packages)
    ):
        return dep_dir / "src"
    if packages == ["."] and build.get("dev-mode-dirs") == [".."]:
        # The dependency directory itself IS the importable package (its
        # name on disk equals the distribution's import name); the parent
        # directory is what must sit on `sys.path`.
        return dep_dir.parent
    return None


def _setuptools_import_root(dep_dir: Path, setuptools: Mapping) -> Path | None:
    packages = setuptools.get("packages")
    find = packages.get("find") if isinstance(packages, Mapping) else None
    where = find.get("where") if isinstance(find, Mapping) else None
    if isinstance(where, list) and where == ["src"]:
        return dep_dir / "src"
    return None


def _import_root(dep_dir: Path) -> Path | None:
    """The single directory that must sit on `PYTHONPATH` for `dep_dir`'s
    package to import -- `None` if the layout cannot be determined from its
    own `pyproject.toml` (callers must BLOCK, never guess)."""
    try:
        document = tomllib.loads(
            (dep_dir / "pyproject.toml").read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError):
        return None
    tool = document.get("tool")
    if not isinstance(tool, Mapping):
        return None
    hatch = tool.get("hatch")
    if isinstance(hatch, Mapping):
        root = _hatch_import_root(dep_dir, hatch)
        if root is not None:
            return root
    setuptools = tool.get("setuptools")
    if isinstance(setuptools, Mapping):
        return _setuptools_import_root(dep_dir, setuptools)
    return None


def candidate_path_dependency_roots(
    source_root: Path, candidate_root: Path
) -> tuple[Path, ...] | BlockedPythonPath:
    """Every root-project local path dependency's import root, derived from
    `source_root`'s (trusted) `uv.lock` and each dependency's own
    `pyproject.toml` layout, resolved against `candidate_root` (the
    directory a check body actually reads from)."""
    lock_path = source_root / "uv.lock"
    try:
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return BlockedPythonPath(f"toolchain: could not read {lock_path}: {error}")
    deps = _local_path_dependencies(lock, lock_path)
    if isinstance(deps, BlockedPythonPath):
        return deps
    roots: list[Path] = []
    for name, relative in deps:
        dep_dir = source_root / relative
        import_root = _import_root(dep_dir)
        if import_root is None:
            return BlockedPythonPath(
                f"toolchain: path dependency {name} layout undetermined"
            )
        try:
            candidate_relative = import_root.resolve().relative_to(
                source_root.resolve()
            )
        except (OSError, ValueError):
            return BlockedPythonPath(
                f"toolchain: path dependency {name} import root outside repository"
            )
        roots.append(candidate_root / candidate_relative)
    return tuple(roots)


def with_candidate_pythonpath(
    env: Mapping[str, str], candidate_root: Path, extra_roots: tuple[Path, ...]
) -> dict[str, str]:
    """`env` with `PYTHONPATH` set to the candidate's own `src/` followed by
    every derived path-dependency root, ahead of any inherited value."""
    entries = [
        str(candidate_root.joinpath(*_ROOT_PYTHONPATH_MEMBER)),
        *map(str, extra_roots),
    ]
    inherited = env.get("PYTHONPATH")
    if inherited:
        entries.append(inherited)
    result = dict(env)
    result["PYTHONPATH"] = os.pathsep.join(entries)
    return result


def resolved_env_with_path_dependencies(
    candidate_root: Path,
    source_root: Path,
    resolved: ResolvedTool | None,
    env: Mapping[str, str],
) -> tuple[dict[str, str], str]:
    """`(env, "")` with the candidate's path-dependency roots on
    `PYTHONPATH` when `resolved` came from `project-env` (`test.python`,
    `test.hooks`); `(env, reason)` when a path dependency's import root
    could not be determined; `(dict(env), "")` unchanged otherwise."""
    if resolved is None or resolved.bundle != _PROJECT_ENV_BUNDLE:
        return dict(env), ""
    roots = candidate_path_dependency_roots(source_root, candidate_root)
    if isinstance(roots, BlockedPythonPath):
        return dict(env), roots.reason
    return with_candidate_pythonpath(env, candidate_root, roots), ""


@dataclass(frozen=True)
class ArgvEnvRequest:
    """The arguments `resolve_argv_and_env` needs -- they only ever travel
    together, one per executed check."""

    check: CheckSpec
    candidate: Candidate
    cwd: Path
    selected: tuple[str, ...]
    resolved: ResolvedTool | None


def resolve_argv_and_env(
    request: ArgvEnvRequest, env: dict[str, str]
) -> tuple[tuple[str, ...], dict[str, str], str]:
    """Resolve a check's argv and environment against the toolchain store."""
    check, candidate, cwd, selected, resolved = (
        request.check,
        request.candidate,
        request.cwd,
        request.selected,
        request.resolved,
    )
    execution_paths = forwarded_paths(candidate.root, cwd, selected)
    argv = check.argv + execution_paths if check.pass_filenames else check.argv
    argv = toolchain.resolve_interpreter_argv(argv)
    argv = toolchain.rewrite_argv(argv, resolved)
    env = toolchain.resolved_env(env, resolved) if resolved is not None else env
    env, path_error = resolved_env_with_path_dependencies(
        candidate.root, candidate.source_root, resolved, env
    )
    return argv, env, path_error
