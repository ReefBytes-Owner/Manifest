"""Authenticate Python distribution metadata and generated console scripts."""

from __future__ import annotations

import ast
import base64
import hashlib
import os
import re
import stat
import sys
from pathlib import Path

PROVENANCE_FILE_LIMIT = 4 * 1024 * 1024


class ProvenanceError(ValueError):
    """Installed provenance cannot be authenticated."""


def _regular_bytes(path: Path) -> bytes:
    if path.is_symlink():
        raise ProvenanceError("installed provenance contains a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise ProvenanceError("installed provenance is not a regular file")
        if status.st_size > PROVENANCE_FILE_LIMIT:
            raise ProvenanceError("installed provenance file exceeds size limit")
        chunks = []
        total = 0
        while chunk := os.read(descriptor, 8192):
            total += len(chunk)
            if total > PROVENANCE_FILE_LIMIT:
                raise ProvenanceError("installed provenance file exceeds size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _recorded_path(snapshot, path: Path):
    matches = [
        item
        for item in snapshot.records
        if Path(os.path.abspath(snapshot.locate(item.relative)))
        == Path(os.path.abspath(path))
    ]
    if (
        len(matches) != 1
        or matches[0].hash_mode is None
        or matches[0].hash_value is None
        or matches[0].size is None
    ):
        raise ProvenanceError("installed file lacks a unique hashed RECORD entry")
    return matches[0]


def _verify_recorded(
    snapshot, path: Path, roots: tuple[Path, ...] | None = None
) -> bytes:
    item = _recorded_path(snapshot, path)
    if item.hash_mode != "sha256":
        raise ProvenanceError("installed RECORD hash is not sha256")
    content = _regular_bytes(path)
    resolved = path.resolve(strict=True)
    if roots is not None and not _within(resolved, roots):
        raise ProvenanceError("installed file escapes trusted package roots")
    actual = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
    if actual.decode() != item.hash_value or len(content) != item.size:
        raise ProvenanceError("installed file does not match its RECORD entry")
    return content


def _entry_point(snapshot, console: str):
    matches = [
        entry
        for entry in snapshot.entry_points
        if entry.group == "console_scripts" and entry.name == console
    ]
    if len(matches) != 1:
        raise ProvenanceError(
            f"distribution does not uniquely declare console {console}"
        )
    return matches[0]


def _verify_python_implementation(
    snapshot,
    entry,
    roots: tuple[Path, ...],
) -> None:
    module_name, separator, attribute = entry.value.partition(":")
    if (
        not separator
        or not re.fullmatch(r"[A-Za-z_]\w*", attribute)
        or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module_name)
    ):
        raise ProvenanceError("console entry point implementation is unsupported")
    parts = module_name.split(".")
    for index in range(1, len(parts)):
        parent = "/".join(parts[:index]) + "/__init__.py"
        _verify_module_file(snapshot, parent, roots)
    leaf = "/".join(parts)
    candidates = [
        item
        for item in (f"{leaf}.py", f"{leaf}/__init__.py")
        if _has_record(snapshot, item)
    ]
    if len(candidates) != 1:
        raise ProvenanceError("console entry point module is unavailable or ambiguous")
    content = _verify_module_file(snapshot, candidates[0], roots)
    try:
        tree = ast.parse(content)
    except SyntaxError as error:
        raise ProvenanceError("console entry point module is invalid") from error
    declarations = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    declarations.update(
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    )
    if attribute not in declarations:
        raise ProvenanceError("console entry point attribute is not declared")


def _has_record(snapshot, relative: str) -> bool:
    return any(item.relative == relative for item in snapshot.records)


def _verify_module_file(
    snapshot,
    relative: str,
    roots: tuple[Path, ...],
) -> bytes:
    matches = [item for item in snapshot.records if item.relative == relative]
    if len(matches) != 1:
        raise ProvenanceError("console package chain is not fully recorded")
    path = snapshot.locate(matches[0].relative)
    return _verify_recorded(snapshot, path, roots)


def verify_distribution(snapshot, roots: tuple[Path, ...]) -> None:
    """Authenticate required metadata for every distribution probe mode."""
    if not _within(snapshot.metadata_directory, roots):
        raise ProvenanceError("distribution metadata escapes trusted package roots")


def verify_consoles(
    snapshot,
    consoles: list[str],
    roots: tuple[Path, ...],
) -> dict[str, Path]:
    """Return authenticated Python console paths for one distribution."""
    verify_distribution(snapshot, roots)
    scripts = Path(sys.prefix, "bin").resolve(strict=True)
    resolved: dict[str, Path] = {}
    for console in consoles:
        entry = _entry_point(snapshot, console)
        candidate = scripts / console
        content = _verify_recorded(snapshot, candidate)
        mode = candidate.stat(follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode) or not mode & 0o111:
            raise ProvenanceError(f"console is not executable: {console}")
        first_line = content.splitlines()[0] if content else b""
        if not first_line.startswith(b"#!"):
            raise ProvenanceError("native executable console provenance is unsupported")
        interpreter = Path(os.fsdecode(first_line[2:]))
        exact = Path(os.path.abspath(interpreter)) == Path(
            os.path.abspath(sys.executable)
        )
        same_base = sys.prefix == sys.base_prefix and interpreter.resolve(
            strict=True
        ) == Path(sys.executable).resolve(strict=True)
        if not interpreter.is_absolute() or not (exact or same_base):
            raise ProvenanceError(
                "console interpreter does not match trusted interpreter"
            )
        _verify_python_implementation(snapshot, entry, roots)
        resolved[console] = candidate
    return resolved
