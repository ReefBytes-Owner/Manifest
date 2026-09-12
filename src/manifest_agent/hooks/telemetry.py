"""Adapter-level run telemetry: the `manifest hook` counterpart of
`checks/telemetry.py`. Every hook event that actually invokes
`manifest check` writes one record carrying the client identity the inner
subprocess has no way to know -- see phase-3-5-decisions.md section 5c."""

from __future__ import annotations

import os
from pathlib import Path

from ..checks.candidate import CandidateBlockedError, _git
from ..checks.telemetry import RecordRunRequest, RuntimeInfo, record_run


def _hook_head_sha(root: Path) -> str:
    """The candidate's HEAD sha, or `""` when it cannot be read -- never
    fabricated."""
    try:
        return _git(root, "rev-parse", "HEAD").decode().strip()
    except CandidateBlockedError:
        return ""


def record_hook_telemetry(
    client: str, profile: str | None, root: Path, status: str, duration_seconds: float
) -> None:
    """One append-only observer record per `manifest hook` run that actually
    executed `manifest check`. `record_run` is itself the safety boundary --
    it never raises -- so a telemetry failure here can never change what
    `process_event` returns."""
    request = RecordRunRequest(
        profile=profile or "",
        status=status,
        duration_seconds=duration_seconds,
        source_root=root,
        head_sha=_hook_head_sha(root),
        runtime=RuntimeInfo(client=client),
    )
    record_run(request, env=os.environ)
