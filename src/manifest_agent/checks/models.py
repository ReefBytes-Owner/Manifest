"""Immutable records shared by project-check registry consumers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .process import ProcessResult
from .toolchain import ResolvedTool


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
    honors_status_contract: bool = False
    scratch_home: bool = False
    failure_line_regex: str = ""

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


ToolKey = tuple[str, Path]
ToolOutcome = tuple[ProcessResult, bool, ProcessResult | None, ResolvedTool | None]


@dataclass(frozen=True)
class RunContext:
    """Per-run state that travels together through check execution: the
    loaded registry, the disposable candidate, its execution environment,
    the memoized tool preflight cache, and groups whose preparation failed."""

    registry: dict
    candidate: Candidate
    env: dict[str, str]
    tool_results: dict[ToolKey, ToolOutcome]
    failed_preparations: dict[str, str]


@dataclass(frozen=True)
class ProfileSelector:
    """The (profile, group) pair a run/report is generated for."""

    profile: str
    group: str | None
