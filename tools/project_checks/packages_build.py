"""uv/build-backend resolution seam for ``packages.py``'s build bodies.

Split out of ``packages.py`` (CON-002/C-SIZE: the module had grown past this
repo's 500-line Python ceiling) along the responsibility seam between
"validate a lockfile" and "resolve a store-attested uv + build backend and
run a real build" -- the latter is the half that touches the toolchain
store, wheel contents, and destination-directory safety.
"""

from __future__ import annotations

import importlib.util
import os
import tomllib
import zipfile
from pathlib import Path

try:
    from tools.project_checks import toolchain_resolve
except ModuleNotFoundError:  # direct script execution from this directory
    import toolchain_resolve

PASS = 0
FAIL = 2


class BlockedError(RuntimeError):
    """An offline tool, backend, lock, or source input is unavailable."""


def uv(root: Path) -> tuple[str, dict[str, str]]:
    """Resolve the store-attested `uv` executable and its run environment."""
    try:
        return toolchain_resolve.resolve_env("store:uv/bin/uv", root, dict(os.environ))
    except toolchain_resolve.ToolchainBlocked as error:
        raise BlockedError(str(error)) from error


def build_python(root: Path) -> str:
    """`store:python-env/bin/python` -- the interpreter `build()`'s
    `--no-build-isolation` `uv build` must run the build backend under.

    `--no-build-isolation` means uv never installs `[build-system]
    requires` itself, so whatever interpreter it resolves has to already
    have `hatchling` importable; the store's attested python-env bundle is
    that interpreter (config/toolchain/pyproject.toml pins hatchling for
    exactly this). Without an explicit `--python`, uv would fall back to
    whatever ambient interpreter it can find, which the store's PATH
    discipline deliberately does not offer it (C7f).
    """
    try:
        executable, _ = toolchain_resolve.resolve_tool(
            "store:python-env/bin/python", root
        )
    except toolchain_resolve.ToolchainBlocked as error:
        raise BlockedError(str(error)) from error
    return str(executable)


def backend(project: Path) -> None:
    """Block unless `project`'s declared PEP 517 build backend is importable."""
    try:
        document = tomllib.loads(
            (project / "pyproject.toml").read_text(encoding="utf-8")
        )
        backend_name = document["build-system"]["build-backend"]
    except (
        OSError,
        UnicodeError,
        tomllib.TOMLDecodeError,
        KeyError,
        TypeError,
    ) as error:
        raise BlockedError(f"build metadata unavailable: {error}") from error
    if not isinstance(backend_name, str) or not backend_name:
        raise BlockedError("build backend is not declared")
    try:
        available = importlib.util.find_spec(backend_name) is not None
    except (ImportError, AttributeError, ValueError):
        available = False
    if not available:
        raise BlockedError(f"preprovisioned build backend unavailable: {backend_name}")


def build_destination(output: Path, check_id: str) -> Path:
    """Create and return an empty, non-symlinked build output directory."""
    destination = output / check_id.replace(".", "-")
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise BlockedError(f"build output is unsafe: {destination}")
    destination.mkdir(parents=False, exist_ok=True)
    if destination.resolve(strict=True).parent != output.resolve(strict=True):
        raise BlockedError(f"build output escapes output directory: {destination}")
    if any(destination.iterdir()):
        raise BlockedError(f"build output must be empty: {destination}")
    return destination


def revalidate_destination(output: Path, destination: Path) -> None:
    """Block if `destination` was swapped to something unsafe since creation."""
    try:
        unsafe = destination.is_symlink() or destination.resolve(
            strict=True
        ).parent != output.resolve(strict=True)
    except OSError as error:
        raise BlockedError(f"build output became unavailable: {error}") from error
    if unsafe:
        raise BlockedError("build output changed to an unsafe destination")


def wheel_contains(wheel: Path, required_member: str) -> bool:
    """Return whether `required_member` is a namelist entry of `wheel`."""
    with zipfile.ZipFile(wheel) as archive:
        return required_member in set(archive.namelist())
