"""Materialize and prepare complete disposable Git candidates without source writes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

from .models import Candidate
from .process import git_output


class CandidateBlockedError(RuntimeError):
    """A complete, stable, safely isolated candidate could not be established."""


def _git(
    root: Path,
    *args: str,
    data: bytes | None = None,
    allowed_codes: tuple[int, ...] = (0,),
) -> bytes:
    try:
        return git_output(root, *args, data=data, allowed_codes=allowed_codes)
    except (OSError, subprocess.SubprocessError) as error:
        raise CandidateBlockedError(f"Git operation blocked: {args[0]}") from error


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_digest(value: object) -> str:
    return _hash(json.dumps(value, sort_keys=True).encode())


def _safe_path(root: Path, name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise CandidateBlockedError(f"unsafe candidate-relative path: {name!r}")
    result = root / path
    for parent in result.relative_to(root).parents:
        if (root / parent).is_symlink():
            raise CandidateBlockedError(f"directory symlink traversal: {name!r}")
    return result


def _identity(root: Path, name: str) -> dict[str, str | int]:
    path = _safe_path(root, name)
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        if Path(target).is_absolute() or not path.resolve().is_relative_to(root):
            raise CandidateBlockedError(f"unsafe symlink: {name!r}")
        return {"kind": "symlink", "mode": mode, "target": target}
    if not stat.S_ISREG(metadata.st_mode):
        raise CandidateBlockedError(f"non-regular candidate file: {name!r}")
    with os.fdopen(
        os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
    ) as stream:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_ino != metadata.st_ino
            or opened.st_dev != metadata.st_dev
        ):
            raise CandidateBlockedError(f"file changed while reading: {name!r}")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()

        def stable(info: os.stat_result) -> tuple:
            return (
                info.st_ino,
                info.st_dev,
                info.st_mode,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        if stable(os.fstat(stream.fileno())) != stable(opened) or stable(
            path.lstat()
        ) != stable(metadata):
            raise CandidateBlockedError(f"file changed while reading: {name!r}")
    return {"kind": "file", "mode": mode, "sha256": digest}


def _index(root: Path) -> tuple[bytes, dict[str, str]]:
    flags = _git(root, "ls-files", "-v", "-z").split(b"\0")
    if any(entry[:1].upper() == b"S" for entry in flags):
        raise CandidateBlockedError("sparse/skip-worktree inputs are unsupported")
    entries = _git(root, "ls-files", "--stage", "-z")
    links = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        mode, sha, stage = header.split()
        if stage != b"0":
            raise CandidateBlockedError("unresolved index conflicts")
        if mode == b"160000":
            links[os.fsdecode(name)] = sha.decode("ascii")
    return entries, links


def _walk(
    root: Path, prefix: str = "", *, exclude: tuple[str, ...] = ()
) -> dict[str, dict[str, str | int]]:
    result = {}
    folder = root / prefix
    for entry in sorted(folder.iterdir()):
        name = entry.relative_to(root).as_posix()
        if name in exclude:
            continue
        if entry.is_dir() and not entry.is_symlink():
            result[name] = {
                "kind": "directory",
                "mode": stat.S_IMODE(entry.lstat().st_mode),
            }
            result.update(_walk(root, name, exclude=exclude))
        else:
            result[name] = _identity(root, name)
    return result


def _snapshot(root: Path, *, populated_links: bool = True) -> dict[str, object]:
    for folder, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name != ".git"]
        for filename in filenames:
            if filename == ".git":
                continue
            mode = (Path(folder) / filename).lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                raise CandidateBlockedError("non-regular candidate file")
    index, links = _index(root)
    names = sorted(
        set(
            _git(
                root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
            ).split(b"\0")
        )
        - {b""}
    )
    files = {}
    metadata = [".git"]
    for raw in names:
        name = os.fsdecode(raw)
        path = _safe_path(root, name)
        if name in links:
            if populated_links and (
                not (path / ".git").exists()
                or _git(path, "rev-parse", "HEAD").decode().strip() != links[name]
                or _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all")
            ):
                raise CandidateBlockedError(
                    f"submodule must be populated, clean and pinned: {name!r}"
                )
            nested_metadata = (
                tuple(_snapshot(path)["metadata"]) if populated_links else ()
            )
            metadata.extend(f"{name}/{item}" for item in nested_metadata)
            files[name] = {
                "kind": "gitlink",
                "sha": links[name],
                "files": _walk(path, exclude=nested_metadata),
            }
        elif path.exists() or path.is_symlink():
            files[name] = _identity(root, name)
    index_path = Path(
        os.fsdecode(
            _git(root, "rev-parse", "--path-format=absolute", "--git-path", "index")
        ).strip()
    )
    return {
        "index": _hash(index_path.read_bytes()) if index_path.exists() else "",
        "entries": _hash(index),
        "names": _hash(b"\0".join(names)),
        "head": _git(root, "rev-parse", "HEAD").decode().strip(),
        "files": files,
        "metadata": metadata,
    }


def candidate_digest(source: Path) -> str:
    """Fingerprint bytes, modes, links, gitlinks and index, with stable enumeration."""
    try:
        root = source.resolve(strict=True)
        populated = not (root / ".git/candidate-state.json").exists()
        before = _snapshot(root, populated_links=populated)
        if before != _snapshot(root, populated_links=populated):
            raise CandidateBlockedError("candidate changed during fingerprint")
        return _json_digest(before)
    except (OSError, ValueError, RuntimeError) as error:
        if isinstance(error, CandidateBlockedError):
            raise
        raise CandidateBlockedError("candidate fingerprint unavailable") from error


def _copy(root: Path, destination: Path, files: dict) -> None:
    for name, identity in files.items():
        source = _safe_path(root, name)
        target = _safe_path(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if identity["kind"] == "gitlink":
            target.mkdir()
            _copy(source, target, identity["files"])
        elif identity["kind"] == "directory":
            target.mkdir(exist_ok=True)
            target.chmod(identity["mode"])
        elif identity["kind"] == "symlink":
            target.symlink_to(identity["target"])
        else:
            with (
                os.fdopen(
                    os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
                ) as incoming,
                target.open("xb") as outgoing,
            ):
                if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                    raise CandidateBlockedError(f"source file changed: {name!r}")
                while chunk := incoming.read(1024 * 1024):
                    outgoing.write(chunk)
            target.chmod(identity["mode"])
        if (
            identity["kind"] not in ("gitlink", "directory")
            and _identity(destination, name) != identity
        ):
            raise CandidateBlockedError(f"source changed while copying: {name!r}")


def _destination(source: Path, destination: Path) -> Path:
    destination = Path(os.path.abspath(destination))
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise CandidateBlockedError("destination must be outside source")
    if any(parent.is_symlink() for parent in (destination, *destination.parents)):
        raise CandidateBlockedError("destination parent traversal through symlink")
    if destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    ):
        raise CandidateBlockedError("destination must be empty")
    destination.mkdir(mode=0o700, exist_ok=True)
    destination.chmod(0o700)
    return destination


def _stage(root: Path, files: dict) -> str:
    records = bytearray()
    for name, identity in files.items():
        if identity["kind"] == "gitlink":
            mode, sha = "160000", identity["sha"]
        else:
            path = root / name
            data = (
                os.fsencode(identity["target"])
                if identity["kind"] == "symlink"
                else path.read_bytes()
            )
            mode = (
                "120000"
                if identity["kind"] == "symlink"
                else ("100755" if identity["mode"] & 0o111 else "100644")
            )
            sha = _git(root, "hash-object", "-w", "--stdin", data=data).decode().strip()
        records.extend(f"{mode} {sha}\t".encode() + os.fsencode(name) + b"\0")
    _git(root, "update-index", "-z", "--index-info", data=bytes(records))
    return _git(root, "write-tree").decode().strip()


def _revision(root: Path, revision: str) -> str:
    return (
        _git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
        .decode()
        .strip()
    )


def materialize_candidate(source: Path, base_sha: str, destination: Path) -> Candidate:
    """Snapshot current HEAD plus all local changes into a private complete repository."""
    try:
        source = source.resolve(strict=True)
        if (
            Path(
                os.fsdecode(_git(source, "rev-parse", "--show-toplevel")).strip()
            ).resolve()
            != source
        ):
            raise CandidateBlockedError("source must be a Git worktree root")
        head, base = _revision(source, "HEAD"), _revision(source, base_sha)
        before = _snapshot(source)
        destination = _destination(source, destination)
        _git(destination, "init", "--quiet", "--template=")
        (destination / ".git/info").mkdir(exist_ok=True)
        (destination / ".git/info/attributes").write_text(
            "* -export-ignore -export-subst\n"
        )
        bundle = destination / ".git/source.bundle"
        _git(source, "bundle", "create", str(bundle), head, base, "HEAD")
        _git(destination, "bundle", "unbundle", str(bundle))
        bundle.unlink()
        _git(destination, "update-ref", "HEAD", head)
        _copy(source, destination, before["files"])
        tree = _stage(destination, before["files"])
        if (
            before != _snapshot(source)
            or head != _git(source, "rev-parse", "HEAD").decode().strip()
        ):
            raise CandidateBlockedError("source changed during materialization")
        changed = tuple(
            os.fsdecode(p)
            for p in _git(
                destination, "diff", "--name-only", "--no-renames", "-z", base, tree
            ).split(b"\0")
            if p
        )
        candidate = Candidate(
            destination,
            source,
            head,
            base,
            tree,
            _json_digest(before),
            changed,
            destination / ".git/preparation-receipt.json",
        )
        _seal_candidate_state(destination, base)
        return candidate
    except (OSError, ValueError, RuntimeError) as error:
        if isinstance(error, CandidateBlockedError):
            raise
        raise CandidateBlockedError("candidate materialization blocked") from error


def _seal_candidate_state(destination: Path, base: str) -> None:
    """Write the integrity-checked digest, then a small informational
    sidecar so a project check body (running with cwd inside this candidate,
    no other view of run context) can locate the protected base commit for
    debt-ratchet comparison (C3) without widening candidate-state.json's own
    strict, digest-only contract (see ``runner.py``'s validation of it).
    """
    _atomic_json(destination / ".git/candidate-state.json", {})
    _atomic_json(
        destination / ".git/candidate-state.json",
        {"digest": candidate_digest(destination)},
    )
    (destination / ".git/candidate-base-sha").write_text(base + "\n")


def _atomic_json(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
