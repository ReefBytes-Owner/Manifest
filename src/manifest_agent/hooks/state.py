"""Dedup + stop-continuation state, serialized with the fcntl idiom
`preparation.py` already uses for candidate-local receipts.

The lock is held for the full duration of an uncached event -- including the
`manifest check` invocation the caller's `compute` callback performs, not
just the bookkeeping. This is deliberate: review round 1 found that a bare
"seen before" flag fails open -- a duplicate arriving while the first
event's verdict was still being computed had no verdict to consult, so it
defaulted to `allow`, which is exactly backwards for a guard that exists to
block. Holding the lock across `compute()` means no caller can ever observe
an "unknown" state: a duplicate either computes the verdict itself (it is
first) or blocks until a complete verdict exists, then replays it exactly.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MAX_DEDUP_ENTRIES = 500
MAX_DEDUP_AGE_SECONDS = 24 * 3600


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


def _prune(state: dict) -> None:
    """Bound state.json growth: drop entries past the age limit, then the
    oldest survivors past the count limit."""
    now = time.time()
    dedup = state["dedup"]
    fresh = {
        key: value
        for key, value in dedup.items()
        if now - value.get("ts", 0) <= MAX_DEDUP_AGE_SECONDS
    }
    if len(fresh) > MAX_DEDUP_ENTRIES:
        ordered = sorted(fresh.items(), key=lambda item: item[1].get("ts", 0))
        fresh = dict(ordered[-MAX_DEDUP_ENTRIES:])
    state["dedup"] = fresh


@dataclass(frozen=True)
class Verdict:
    """A cached, replayable outcome: exactly what a duplicate reproduces."""

    replayed: bool
    outcome: dict


@dataclass(frozen=True)
class DedupKey:
    """What identifies one deduplicated event -- travels as one record so
    `run_deduplicated` stays a 3-parameter call."""

    client: str
    event: str
    digest: str
    is_stop: bool


def run_deduplicated(
    state_dir: Path, key: DedupKey, compute: Callable[[], dict]
) -> Verdict:
    """Serialize (client, event, digest). The first caller runs `compute`
    while holding the lock and caches its return value; every duplicate --
    even one that arrives mid-computation -- blocks on the same lock and
    replays that exact cached verdict, never a bare allow."""
    fd = _lock(state_dir)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        state = _load(state_dir)
        cache_key = _dedup_key(key.client, key.event, key.digest)
        cached = state["dedup"].get(cache_key)
        if cached is not None:
            return Verdict(replayed=True, outcome=cached["outcome"])
        stop_key = f"{key.client}\0{key.event}"
        stop_allowed = (
            state["stop_continuations"].get(stop_key) != key.digest
            if key.is_stop
            else True
        )
        if key.is_stop and not stop_allowed:
            outcome = {
                "status": "SKIPPED_CONTINUATION",
                "action": "allow",
                "reason": "stop continuation already used for this candidate",
            }
        else:
            outcome = compute()
            if key.is_stop:
                state["stop_continuations"][stop_key] = key.digest
        state["dedup"][cache_key] = {"outcome": outcome, "ts": time.time()}
        _prune(state)
        _write(state_dir, state)
        return Verdict(replayed=False, outcome=outcome)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
