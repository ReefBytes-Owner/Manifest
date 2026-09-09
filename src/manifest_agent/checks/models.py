"""Immutable records shared by project-check registry consumers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckSpec:
    id: str
    category: str
    group: str
    argv: tuple[str, ...]
    cwd: str
    inputs: tuple[str, ...]
    dependencies: tuple[str, ...]
    timeout_seconds: float
    selection: str
    tool: str
    version: str
    include_regex: str = ""
    exclude_regex: str = r"$^"
    types: tuple[str, ...] = ()
    types_or: tuple[str, ...] = ()
    pass_filenames: bool | None = None

    def __post_init__(self) -> None:
        if self.pass_filenames is None:
            object.__setattr__(self, "pass_filenames", self.selection == "changed")


@dataclass(frozen=True)
class PreparationSpec:
    id: str
    argv: tuple[str, ...]
    cwd: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    groups: tuple[str, ...]
    timeout_seconds: float
    tool: str
    version: str


@dataclass(frozen=True)
class CheckResult:
    """One honest check or preparation outcome."""

    id: str
    status: str
    returncode: int | None
    duration_seconds: float
    diagnostics: str
    selected_inputs: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    """Complete disposable worktree with destination-local Git identities."""

    root: Path
    source_root: Path
    head_sha: str
    base_sha: str
    tree_sha: str
    source_digest: str
    changed_paths: tuple[str, ...]
    preparation_receipt: Path
