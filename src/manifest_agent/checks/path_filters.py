"""Validate and apply declarative repository path filters."""

from __future__ import annotations

import fnmatch
import os
import re
import stat
from pathlib import Path
from typing import Any

from .models import CheckSpec

VALID_PATH_TYPES = frozenset(
    {
        "executable",
        "go",
        "javascript",
        "json",
        "jsx",
        "markdown",
        "pyi",
        "python",
        "rust",
        "shell",
        "terraform",
        "text",
        "ts",
        "tsx",
        "yaml",
    }
)
# "executable" and "text" are pre-commit `identify` cross-cutting tags applied
# to every regular file in addition to any suffix/shebang tag below (mirrors
# identify's own "executable" = x-bit set, "text" = no NUL byte in a sample):
# see `_path_tags`. `identify`'s "file" tag (implicitly true for every
# regular file we tag at all) is not modeled -- requiring it would be a
# no-op, since `_path_tags` already returns `None` for anything that is not
# a regular file.
_SUFFIX_TAGS = {
    ".bash": frozenset({"shell"}),
    ".bats": frozenset({"shell"}),
    ".cjs": frozenset({"javascript"}),
    ".go": frozenset({"go"}),
    ".js": frozenset({"javascript"}),
    ".json": frozenset({"json"}),
    ".jsx": frozenset({"jsx"}),
    ".markdown": frozenset({"markdown"}),
    ".md": frozenset({"markdown"}),
    ".mjs": frozenset({"javascript"}),
    ".py": frozenset({"python"}),
    ".pyi": frozenset({"pyi"}),
    ".rs": frozenset({"rust"}),
    ".sh": frozenset({"shell"}),
    ".tf": frozenset({"terraform"}),
    ".tfvars": frozenset({"terraform"}),
    ".ts": frozenset({"ts"}),
    ".tsx": frozenset({"tsx"}),
    ".yaml": frozenset({"yaml"}),
    ".yml": frozenset({"yaml"}),
}
_BINARY_SAMPLE_BYTES = 8192
_SHELLS = frozenset({"ash", "bash", "dash", "ksh", "sh", "zsh"})
_PYTHON_INTERPRETER = re.compile(r"python(?:\d+(?:\.\d+)*)?\Z")


def validate_path_filters(check: dict[str, Any], label: str) -> None:
    """Reject invalid regular expressions and unknown repository type tags."""
    for field, default in (("include_regex", ""), ("exclude_regex", r"$^")):
        try:
            re.compile(check.get(field, default))
        except re.error as error:
            raise ValueError(
                f"{label} {field} is not a valid regular expression: {error}"
            ) from error
    for field in ("types", "types_or"):
        unknown = set(check.get(field, ())) - VALID_PATH_TYPES
        if unknown:
            raise ValueError(
                f"{label} {field} has unknown type tags: {sorted(unknown)}"
            )


def matches(names: tuple[str, ...], pattern: str) -> tuple[str, ...]:
    """Return names matched by one candidate-relative selector."""
    while pattern.startswith("./"):
        pattern = pattern[2:]
    pattern = "" if pattern == "." else pattern.rstrip("/")
    if not pattern:
        return names
    prefix = pattern + "/"
    return tuple(
        name
        for name in names
        if name == pattern
        or name.startswith(prefix)
        or fnmatch.fnmatchcase(name, pattern)
    )


def has_path_filters(check: CheckSpec) -> bool:
    """Return whether a check declares filtering beyond base input selection."""
    return bool(
        check.include_regex
        or check.exclude_regex != r"$^"
        or check.types
        or check.types_or
    )


def forwarded_paths(
    root: Path, cwd: Path, selected: tuple[str, ...]
) -> tuple[str, ...]:
    """Return literal cwd-relative argv paths with option-like names made safe."""
    paths = []
    for name in selected:
        relative = os.path.relpath(root / name, cwd)
        paths.append(f"./{relative}" if relative.startswith("-") else relative)
    return tuple(paths)


def _shebang_interpreter(first_line: str) -> str:
    if not first_line.startswith("#!"):
        return ""
    words = first_line[2:].strip().split()
    if not words:
        return ""
    interpreter = Path(words[0]).name
    if interpreter != "env":
        return interpreter
    arguments = words[1:]
    if arguments[:1] == ["-S"]:
        arguments = arguments[1:]
    return Path(arguments[0]).name if arguments else ""


def _path_tags(root: Path, name: str) -> frozenset[str] | None:
    path = root / name
    try:
        mode = path.lstat().st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    tags = set(_SUFFIX_TAGS.get(path.suffix.casefold(), ()))
    try:
        sample = path.open("rb").read(_BINARY_SAMPLE_BYTES)
    except OSError:
        sample = b""
    if not tags and mode & 0o111:
        first_line = sample[:256].decode("utf-8", errors="ignore")
        interpreter = _shebang_interpreter(first_line)
        if interpreter in _SHELLS:
            tags.add("shell")
        if _PYTHON_INTERPRETER.fullmatch(interpreter):
            tags.add("python")
    if mode & 0o111:
        tags.add("executable")
    if b"\x00" not in sample:
        tags.add("text")
    return frozenset(tags)


def filter_inputs(
    check: CheckSpec, root: Path, selected: tuple[str, ...]
) -> tuple[str, ...]:
    """Apply include, exclusion, and type filters to regular repository files."""
    if not has_path_filters(check):
        return selected
    include = re.compile(check.include_regex)
    exclude = re.compile(check.exclude_regex)
    required = set(check.types)
    alternatives = set(check.types_or)
    filtered = []
    for name in selected:
        if not include.search(name) or exclude.search(name):
            continue
        tags = _path_tags(root, name)
        if tags is None:
            continue
        if required and not required <= tags:
            continue
        if alternatives and not alternatives.intersection(tags):
            continue
        filtered.append(name)
    return tuple(filtered)
