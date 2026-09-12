"""Shared mechanics for thin per-client `manifest hook` adapters.

Every per-client module (`claude_code.py`, `codex.py`, `cursor.py`,
`gemini.py`) parses its own protocol shape and calls `process_event` here for
everything that is not client-specific: bounded stdin parsing, recursion
refusal, dedup, the `manifest check` invocation, and the receipt.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from manifest_agent.process import redact_text

from ..checks.candidate import CandidateBlockedError, candidate_digest
from .receipt import ReceiptInput, build_receipt, write_receipt
from .runner import RECURSION_ENV_VAR, run_manifest_check
from .state import DedupKey, run_deduplicated
from .telemetry import record_hook_telemetry

STDIN_CAP = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 120.0
QUICK = "quick"
FULL = "full"


class ProtocolError(RuntimeError):
    """Input that must yield a protocol block response, never a traceback."""


REASON_CAP = 2048


def safe_reason(error: Exception) -> str:
    """Every crash path (`ProtocolError`, or an `OSError`/`ValueError` from
    an unwritable state dir or a bad deadline) converts to this instead of
    propagating -- the same "catch, redact, respond" idiom
    `checks/cli.py::_blocked_report` already uses for its own infrastructure
    failures. Never a traceback, never empty stdout."""
    return redact_text(str(error))[:REASON_CAP]


def read_bounded_stdin(stream) -> bytes:
    """Read at most STDIN_CAP+1 bytes; never parse a truncated document."""
    data = stream.read(STDIN_CAP + 1)
    if isinstance(data, str):
        data = data.encode("utf-8", errors="surrogateescape")
    if len(data) > STDIN_CAP:
        raise ProtocolError("stdin exceeds the 256 KiB cap")
    return data


def parse_event_object(raw: bytes) -> dict:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("stdin is not valid UTF-8") from error
    try:
        value = json.loads(text) if text.strip() else {}
    except ValueError as error:
        raise ProtocolError("stdin is not valid JSON") from error
    if not isinstance(value, dict):
        raise ProtocolError("stdin JSON must be an object")
    return value


def require_string_fields(payload: dict, fields: tuple[str, ...]) -> None:
    for name in fields:
        if name in payload and not isinstance(payload[name], str):
            raise ProtocolError(f"field {name!r} must be a string")


def require_object_fields(payload: dict, fields: tuple[str, ...]) -> None:
    for name in fields:
        if name in payload and not isinstance(payload[name], dict):
            raise ProtocolError(f"field {name!r} must be an object")


def reject_traversal(value: str, label: str) -> None:
    if not value:
        return
    if "\0" in value:
        raise ProtocolError(f"{label} contains a null byte")
    normalized = value.replace("\\", "/")
    if any(part == ".." for part in normalized.split("/")):
        raise ProtocolError(f"{label} contains a path traversal segment")


def resolve_source_root(cwd_raw: str) -> Path | None:
    try:
        path = Path(cwd_raw).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    return path if path.is_dir() else None


def default_project_config() -> Path:
    """`MANIFEST_HOOK_PROJECT_CONFIG` lets tests/hosts point at a different
    registry; the repo default is this checkout's own `config/project-checks.json`."""
    override = os.environ.get("MANIFEST_HOOK_PROJECT_CONFIG")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "config" / "project-checks.json"


def default_state_dir() -> Path:
    from manifest_agent.paths import xdg_paths

    return xdg_paths().state / "hooks"


def default_timeout_seconds() -> float:
    """`MANIFEST_HOOK_TIMEOUT_SECONDS` overrides the adapter deadline for
    tests; a malformed or non-positive value is not a usable deadline, so it
    falls back rather than raising out of a hook that must always emit
    protocol JSON (`run_argv` rejects `timeout_seconds <= 0` with a
    `ValueError`, which is exactly what must never reach the caller here)."""
    override = os.environ.get("MANIFEST_HOOK_TIMEOUT_SECONDS", "")
    if override.replace(".", "", 1).isdigit() and float(override) > 0:
        return float(override)
    return DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class AdapterOutcome:
    coverage: str  # "supported" | "unsupported"
    status: str
    action: str  # "allow" | "block"
    reason: str
    receipt: dict | None


@dataclass(frozen=True)
class EventRequest:
    """What `process_event` needs; the caller's per-client parsing produces
    this in one shot so the dispatcher itself stays a single-record call."""

    client: str
    event: str
    profile: str | None
    payload: dict
    project_config: Path
    state_dir: Path
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def _compute_verdict(request: EventRequest, root: Path, digest: str) -> dict:
    """Run the real check, record telemetry, write the receipt, and return
    the verdict `run_deduplicated` caches -- this is the ONLY place a
    non-replayed outcome is produced."""
    run_start = time.monotonic()
    status, diagnostics = run_manifest_check(
        profile=request.profile,
        project_config=request.project_config,
        base="HEAD",
        cwd=root,
        timeout_seconds=request.timeout_seconds,
    )
    record_hook_telemetry(
        request.client, request.profile, root, status, time.monotonic() - run_start
    )
    receipt = build_receipt(
        ReceiptInput(
            client=request.client,
            event=request.event,
            profile=request.profile,
            status=status,
            digest=digest,
            diagnostics=diagnostics,
        )
    )
    write_receipt(request.state_dir, receipt)
    action = "block" if status in ("FAIL", "BLOCKED") else "allow"
    return {"status": status, "action": action, "reason": diagnostics}


def process_event(request: EventRequest) -> AdapterOutcome:
    """Everything after per-client parsing/validation has passed."""
    if request.profile is None:
        return AdapterOutcome(
            "unsupported", "UNSUPPORTED", "allow", "event not covered", None
        )
    if os.environ.get(RECURSION_ENV_VAR):
        return AdapterOutcome(
            "supported",
            "SKIPPED_RECURSION",
            "allow",
            "recursive hook invocation refused",
            None,
        )
    cwd_raw = request.payload.get("cwd") or os.getcwd()
    reject_traversal(cwd_raw, "cwd")
    root = resolve_source_root(cwd_raw)
    if root is None:
        return AdapterOutcome(
            "supported", "BLOCKED", "block", "cwd is unavailable", None
        )
    try:
        digest = candidate_digest(root)
    except CandidateBlockedError as error:
        return AdapterOutcome(
            "supported", "BLOCKED", "block", redact_text(str(error)), None
        )
    dedup_key = DedupKey(
        client=request.client,
        event=request.event,
        digest=digest,
        is_stop=request.profile == FULL,
    )
    verdict = run_deduplicated(
        request.state_dir, dedup_key, lambda: _compute_verdict(request, root, digest)
    )
    outcome = verdict.outcome
    return AdapterOutcome(
        "supported", outcome["status"], outcome["action"], outcome["reason"], None
    )
