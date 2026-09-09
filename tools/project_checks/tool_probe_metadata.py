"""Read bounded immutable snapshots of installed Python distribution metadata."""

from __future__ import annotations

import base64
import configparser
import csv
import hashlib
import io
import os
import re
import stat
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

METADATA_FILE_LIMIT = 4 * 1024 * 1024
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_VERSION = re.compile(r"[^\s\x00-\x1f\x7f]{1,128}\Z")
_HASH = re.compile(r"[A-Za-z0-9_-]+\Z")


class MetadataError(ValueError):
    """A distribution metadata snapshot could not be authenticated."""


@dataclass(frozen=True)
class Record:
    relative: str
    hash_mode: str | None
    hash_value: str | None
    size: int | None


@dataclass(frozen=True)
class EntryPoint:
    name: str
    value: str
    group: str


@dataclass(frozen=True)
class VerifiedDistribution:
    name: str
    version: str
    record_root: Path
    metadata_directory: Path
    records: tuple[Record, ...]
    entry_points: tuple[EntryPoint, ...]
    metadata_bytes: bytes
    record_bytes: bytes
    entry_points_bytes: bytes | None

    def locate(self, relative: str) -> Path:
        return Path(os.path.abspath(self.record_root / relative))


def _read_descriptor(descriptor: int) -> bytes:
    status = os.fstat(descriptor)
    if not stat.S_ISREG(status.st_mode):
        raise MetadataError("installed provenance is not a regular file")
    if status.st_size > METADATA_FILE_LIMIT:
        raise MetadataError("installed provenance file exceeds size limit")
    chunks = []
    total = 0
    while chunk := os.read(descriptor, 8192):
        total += len(chunk)
        if total > METADATA_FILE_LIMIT:
            raise MetadataError("installed provenance file exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _read_at(directory: int, name: str, *, optional: bool = False) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory)
    except FileNotFoundError:
        if optional:
            return None
        raise MetadataError(f"installed metadata file is missing: {name}") from None
    except OSError as error:
        raise MetadataError(f"installed metadata file is unsafe: {name}") from error
    try:
        return _read_descriptor(descriptor)
    finally:
        os.close(descriptor)


def _snapshot_files(
    candidate: Any, roots: tuple[Path, ...]
) -> tuple[Path, bytes, bytes, bytes | None]:
    raw = getattr(candidate, "_path", None)
    if raw is None:
        raise MetadataError("distribution metadata location is unavailable")
    path = Path(raw)
    if path.is_symlink():
        raise MetadataError("distribution metadata directory is a symlink")
    resolved = path.resolve(strict=True)
    if not any(resolved.is_relative_to(root) for root in roots):
        raise MetadataError("distribution metadata escapes trusted package roots")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory = os.open(path, flags)
    except OSError as error:
        raise MetadataError("distribution metadata directory is unsafe") from error
    try:
        if not stat.S_ISDIR(os.fstat(directory).st_mode):
            raise MetadataError("distribution metadata location is not a directory")
        metadata = _read_at(directory, "METADATA")
        record = _read_at(directory, "RECORD")
        entry_points = _read_at(directory, "entry_points.txt", optional=True)
    finally:
        os.close(directory)
    assert metadata is not None and record is not None
    return resolved, metadata, record, entry_points


def _parse_records(content: bytes) -> tuple[Record, ...]:
    try:
        rows = csv.reader(io.StringIO(content.decode("utf-8"), newline=""), strict=True)
        parsed = []
        for row in rows:
            if len(row) != 3:
                raise MetadataError("installed RECORD row is malformed")
            relative, encoded_hash, encoded_size = row
            path = PurePosixPath(relative)
            if not relative or path.is_absolute() or "\x00" in relative:
                raise MetadataError("installed RECORD path is malformed")
            hash_mode = hash_value = None
            if encoded_hash:
                hash_mode, separator, hash_value = encoded_hash.partition("=")
                if not separator or not hash_mode or not _HASH.fullmatch(hash_value):
                    raise MetadataError("installed RECORD hash is malformed")
            size = None
            if encoded_size:
                if not encoded_size.isascii() or not encoded_size.isdecimal():
                    raise MetadataError("installed RECORD size is malformed")
                size = int(encoded_size)
            parsed.append(Record(relative, hash_mode, hash_value, size))
    except (csv.Error, UnicodeError, ValueError) as error:
        if isinstance(error, MetadataError):
            raise
        raise MetadataError("installed RECORD is malformed") from error
    return tuple(parsed)


def _record_for(records: tuple[Record, ...], root: Path, path: Path) -> Record:
    absolute = Path(os.path.abspath(path))
    matches = [
        item
        for item in records
        if Path(os.path.abspath(root / item.relative)) == absolute
    ]
    if len(matches) != 1:
        raise MetadataError("installed file lacks a unique RECORD entry")
    return matches[0]


def _verify_snapshot(
    records: tuple[Record, ...], root: Path, path: Path, content: bytes
) -> None:
    item = _record_for(records, root, path)
    if item.hash_mode != "sha256" or item.hash_value is None or item.size is None:
        raise MetadataError("installed file lacks a unique hashed RECORD entry")
    actual = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
    if actual.decode() != item.hash_value or len(content) != item.size:
        raise MetadataError("installed file does not match its RECORD entry")


def _parse_identity(content: bytes) -> tuple[str, str]:
    try:
        message = BytesParser(policy=policy.compat32).parsebytes(content)
        names = message.get_all("Name", [])
        versions = message.get_all("Version", [])
    except (UnicodeError, ValueError) as error:
        raise MetadataError("installed METADATA is malformed") from error
    if len(names) != 1 or len(versions) != 1:
        raise MetadataError("installed METADATA identity is ambiguous")
    name, version = str(names[0]), str(versions[0])
    if not _NAME.fullmatch(name) or not _VERSION.fullmatch(version):
        raise MetadataError("installed METADATA identity is malformed")
    return name, version


def _parse_entry_points(content: bytes | None) -> tuple[EntryPoint, ...]:
    if content is None:
        return ()
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(content.decode("utf-8"))
    except (configparser.Error, UnicodeError, ValueError) as error:
        raise MetadataError("installed entry points are malformed") from error
    if not parser.has_section("console_scripts"):
        return ()
    return tuple(
        EntryPoint(name, value, "console_scripts")
        for name, value in parser.items("console_scripts")
    )


def snapshot_distribution(
    candidate: Any, roots: tuple[Path, ...]
) -> VerifiedDistribution:
    """Read, authenticate, and parse one immutable metadata snapshot."""
    directory, metadata, record, entry_points = _snapshot_files(candidate, roots)
    record_root = directory.parent
    records = _parse_records(record)
    _verify_snapshot(records, record_root, directory / "METADATA", metadata)
    if entry_points is not None:
        _verify_snapshot(
            records, record_root, directory / "entry_points.txt", entry_points
        )
    name, version = _parse_identity(metadata)
    return VerifiedDistribution(
        name,
        version,
        record_root,
        directory,
        records,
        _parse_entry_points(entry_points),
        metadata,
        record,
        entry_points,
    )
