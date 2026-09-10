"""Adapter receipts: every one carries `client_version_verified: false`
because no adapter has been run under a real pinned client (see C10)."""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReceiptInput:
    """The fields a receipt records; travel together as one record."""

    client: str
    event: str
    profile: str | None
    status: str
    digest: str | None
    diagnostics: str


def build_receipt(info: ReceiptInput) -> dict:
    return {
        "schema_version": 1,
        "client": info.client,
        "event": info.event,
        "profile": info.profile,
        "status": info.status,
        "candidate_digest": info.digest,
        "diagnostics": info.diagnostics,
        "client_version_verified": False,
    }


def write_receipt(state_dir: Path, receipt: dict) -> None:
    """Serialize the write with the same fcntl idiom `preparation.py` uses."""
    receipts_dir = state_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        receipts_dir / "receipts.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        digest = (receipt.get("candidate_digest") or "unknown")[:16]
        name = f"{receipt['client']}-{receipt['event']}-{digest}.json"
        tmp = receipts_dir / f".{name}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
        os.replace(tmp, receipts_dir / name)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
