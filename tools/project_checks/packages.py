"""Offline, no-install package and lock verification bodies."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from tools.project_checks import packages_build
except ModuleNotFoundError:  # direct script execution from this directory
    import packages_build

PASS = 0
FAIL = 2
BLOCKED = 3

_PROJECTS = {
    "dependency.lock.config": "configs/claude",
    "dependency.lock.delegate": "plugins/manifest-delegate",
    "dependency.lock.root": ".",
    "package.coordinator": ".",
    "package.config": "configs/claude",
}


# Shared with `packages_build` so a build-seam `BlockedError` and a
# lock/release-seam `BlockedError` are the exact same type -- `except
# BlockedError` in `main()` must catch both without a second handler.
BlockedError = packages_build.BlockedError


def _root(arguments: argparse.Namespace) -> Path:
    try:
        root = arguments.root.expanduser().resolve(strict=True)
    except OSError as error:
        raise BlockedError(f"root unavailable: {error}") from error
    if not root.is_dir():
        raise BlockedError(f"root is not a directory: {root}")
    return root


def _output(
    root: Path, requested: Path | None
) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    temporary = None
    if requested is None:
        temporary = tempfile.TemporaryDirectory(prefix="manifest-project-check-")
        output = Path(temporary.name).resolve()
    else:
        if requested.expanduser().is_symlink():
            raise BlockedError("output directory must not be a symlink")
        output = requested.expanduser().resolve(strict=False)
    if output == root or output.is_relative_to(root) or root.is_relative_to(output):
        if temporary is not None:
            temporary.cleanup()
        raise BlockedError("output directory must be disjoint from root")
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        if temporary is not None:
            temporary.cleanup()
        raise BlockedError(f"output directory unavailable: {error}") from error
    return output, temporary


def _project(root: Path, check_id: str) -> Path:
    candidate = root / _PROJECTS[check_id]
    try:
        project = candidate.resolve(strict=True)
    except OSError as error:
        raise BlockedError(
            f"project unavailable: {_PROJECTS[check_id]}: {error}"
        ) from error
    if not project.is_relative_to(root) or candidate.is_symlink():
        raise BlockedError(
            f"project escapes root or is symlinked: {_PROJECTS[check_id]}"
        )
    metadata = project / "pyproject.toml"
    if not metadata.is_file() or metadata.is_symlink():
        raise BlockedError(
            f"project metadata unavailable: {_PROJECTS[check_id]}/pyproject.toml"
        )
    return project


def _revalidate_project(root: Path, check_id: str, expected: Path) -> None:
    if _project(root, check_id) != expected:
        raise BlockedError(f"project changed during validation: {_PROJECTS[check_id]}")


def _run(
    command: tuple[str, ...], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"command unavailable: {error}") from error


def _emit(result: subprocess.CompletedProcess[str]) -> str:
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return (result.stdout + result.stderr).lower()


def _lock(root: Path, check_id: str) -> int:
    project = _project(root, check_id)
    lock = project / "uv.lock"
    if not lock.is_file():
        raise BlockedError(f"lockfile unavailable: {lock.relative_to(root)!s}")
    executable, env = packages_build.uv(root)
    _revalidate_project(root, check_id, project)
    result = _run(
        (
            executable,
            "lock",
            "--check",
            "--offline",
            "--no-python-downloads",
            "--project",
            str(project),
        ),
        root,
        env,
    )
    diagnostic = _emit(result)
    if result.returncode == 0:
        return PASS
    if any(
        word in diagnostic
        for word in ("needs to be updated", "out of date", "mismatch")
    ):
        return FAIL
    raise BlockedError("offline lock validation could not execute")


def _build(root: Path, check_id: str, output: Path) -> int:
    project = _project(root, check_id)
    packages_build.backend(project)
    destination = packages_build.build_destination(output, check_id)
    executable, env = packages_build.uv(root)
    build_python = packages_build.build_python(root)
    _revalidate_project(root, check_id, project)
    packages_build.revalidate_destination(output, destination)
    result = _run(
        (
            executable,
            "build",
            "--offline",
            "--no-python-downloads",
            "--no-build-isolation",
            "--no-create-gitignore",
            "--python",
            build_python,
            "--out-dir",
            str(destination),
            str(project),
        ),
        root,
        env,
    )
    diagnostic = _emit(result)
    packages_build.revalidate_destination(output, destination)
    if result.returncode:
        if any(
            word in diagnostic for word in ("offline", "not found in cache", "backend")
        ):
            raise BlockedError("offline build prerequisites unavailable")
        return FAIL
    wheels = sorted(destination.glob("*.whl"))
    if not wheels:
        print("FAIL: build produced no wheel", file=sys.stderr)
        return FAIL
    for artifact in sorted(destination.iterdir()):
        if artifact.is_file():
            print(
                f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}"
            )
    if check_id == "package.coordinator":
        required = "manifest_agent/data/project-checks.schema.json"
        try:
            has_required = packages_build.wheel_contains(wheels[0], required)
        except (OSError, zipfile.BadZipFile) as error:
            print(f"FAIL: invalid wheel: {error}", file=sys.stderr)
            return FAIL
        if not has_required:
            print(f"FAIL: wheel missing required member: {required}", file=sys.stderr)
            return FAIL
    return PASS


def _archive_base(value: str | None) -> str:
    if value is None:
        raise BlockedError("archive base URL metadata is required")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise BlockedError(f"archive base URL is malformed: {error}") from error
    path_parts = unquote(parsed.path).split("/")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(part in {".", ".."} for part in path_parts)
        or "\\" in value
        or any(character.isspace() for character in value)
        or "OWNER" in value
        or "REPOSITORY" in value
    ):
        raise BlockedError(
            "archive base URL must be a credential-free concrete HTTPS URL"
        )
    del port  # Access above validates both syntax and range.
    return value.rstrip("/")


def _release_archive(root: Path, output: Path, archive_base_url: str) -> int:
    script = root / "tools/build_manifest_release.py"
    if not script.is_file():
        raise BlockedError("release builder unavailable")
    result = _run(
        (
            sys.executable,
            str(script),
            "--repo-root",
            str(root),
            "--output-dir",
            str(output),
            "--archive-base-url",
            archive_base_url,
        ),
        root,
    )
    _emit(result)
    if result.returncode == 0:
        return PASS
    if result.returncode == 1:
        return FAIL
    raise BlockedError(
        f"release builder could not execute successfully (exit {result.returncode})"
    )


def _release_manifest(output: Path, archive_base_url: str) -> int:
    path = output / "manifest-release.json"
    if not path.is_file():
        raise BlockedError("release manifest unavailable")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        version = document["version"]
        archive_url = document["archive_url"]
        bundles = document["bundles"]
        valid = (
            isinstance(version, str)
            and bool(version)
            and isinstance(archive_url, str)
            and archive_url.startswith(f"{archive_base_url}/{version}/")
            and isinstance(bundles, dict)
            and bool(bundles)
            and all(
                isinstance(bundle, dict) and bundle.get("version") == version
                for bundle in bundles.values()
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"FAIL: invalid release manifest: {error}", file=sys.stderr)
        return FAIL
    if not valid:
        print(
            "FAIL: release manifest versions or archive URL are inconsistent",
            file=sys.stderr,
        )
        return FAIL
    if "archive_sha256" in document:
        archive = output / archive_url.rsplit("/", 1)[-1]
        if (
            not archive.is_file()
            or hashlib.sha256(archive.read_bytes()).hexdigest()
            != document["archive_sha256"]
        ):
            print("FAIL: release archive checksum mismatch", file=sys.stderr)
            return FAIL
    return PASS


CHECK_IDS = (
    "dependency.lock.config",
    "dependency.lock.delegate",
    "package.coordinator",
    "package.config",
    "package.release-archive",
    "package.release-manifest",
)

# `dependency.lock.root` (Phase 3 chunk C5) shares this module's `_lock()`
# body but is NOT part of the frozen shadow-CI migration set `CHECK_IDS`
# feeds into `TASK7_DISPOSITIONS` -- it has no legacy pre-commit/CI job in
# `config/check-preservation.json` to preserve 1:1 (same reasoning as C3's
# `debt.*` ids; see test_check_profile_parity.py's `DISPOSITIONS`/
# `RETAINED_IDS`). Kept as a distinct tuple so the oracle-backed set and the
# runtime argparse choices cannot silently drift apart.
ADDITIONAL_CHECK_IDS = ("dependency.lock.root",)
ALL_CHECK_IDS = CHECK_IDS + ADDITIONAL_CHECK_IDS

TASK7_DISPOSITIONS = {
    check_id: (
        (
            "python3",
            "tools/project_checks/packages.py",
            check_id,
            "--root",
            ".",
            *(
                (
                    "--archive-base-url",
                    "https://github.com/RB-chrismandich/Manifest/releases/download",
                )
                if check_id.startswith("package.release-")
                else ()
            ),
        ),
        "project",
    )
    for check_id in CHECK_IDS
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_id", choices=ALL_CHECK_IDS)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--archive-base-url")
    arguments = parser.parse_args(argv)
    temporary = None
    try:
        root = _root(arguments)
        output, temporary = _output(root, arguments.output_dir)
        if arguments.check_id.startswith("dependency.lock."):
            return _lock(root, arguments.check_id)
        if arguments.check_id in {"package.coordinator", "package.config"}:
            return _build(root, arguments.check_id, output)
        archive_base_url = _archive_base(arguments.archive_base_url)
        if arguments.check_id == "package.release-archive":
            return _release_archive(root, output, archive_base_url)
        if arguments.output_dir is None:
            status = _release_archive(root, output, archive_base_url)
            if status != PASS:
                return status
        return _release_manifest(output, archive_base_url)
    except BlockedError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
