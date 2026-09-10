"""Dedup + stop-continuation state, serialized with the fcntl idiom
`preparation.py` already uses for candidate-local receipts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


def _lock(state_dir: Path) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    return os.open(
        state_dir / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )


def _load(state_dir: Path) -> dict:
    try:
        value = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = None
    if not isinstance(value, dict):
        value = {}
    value.setdefault("dedup", {})
    value.setdefault("stop_continuations", {})
    return value


def _write(state_dir: Path, state: dict) -> None:
    tmp = state_dir / f".state-{os.getpid()}-{time.monotonic_ns()}.tmp"
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, state_dir / "state.json")


def _dedup_key(client: str, event: str, digest: str) -> str:
    return hashlib.sha256(f"{client}\0{event}\0{digest}".encode()).hexdigest()


@dataclass(frozen=True)
class DedupDecision:
    is_new: bool
    stop_allowed: bool


def check_and_record(
    state_dir: Path, client: str, event: str, digest: str, *, is_stop: bool
) -> DedupDecision:
    """Serialize (client, event, digest) dedup and, for stop events, allow at
    most one continuation per unchanged digest. A burst of N identical events
    collapses to exactly one `is_new=True` result because the whole
    read-modify-write happens under one flock, held for the burst's duration."""
    fd = _lock(state_dir)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        state = _load(state_dir)
        key = _dedup_key(client, event, digest)
        is_new = key not in state["dedup"]
        state["dedup"][key] = time.time()
        stop_allowed = True
        if is_stop:
            stop_key = f"{client}\0{event}"
            stop_allowed = state["stop_continuations"].get(stop_key) != digest
            if stop_allowed:
                state["stop_continuations"][stop_key] = digest
        _write(state_dir, state)
        return DedupDecision(is_new=is_new, stop_allowed=stop_allowed)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
