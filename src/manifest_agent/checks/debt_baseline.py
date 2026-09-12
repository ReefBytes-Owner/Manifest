"""Debt-ratchet baseline loading and per-entry validation (Phase 3 chunk C3).

Split out of ``debt.py`` (identity computation vs. baseline loading/
validation vs. verdict rules are three independent responsibilities) so
neither half grows past the Code Constitution's file-size ceiling.

``config/debt-baseline.json`` (schema v2) is a REVIEWED RECORD, never
agent-granted authority -- see ``debt.py``'s module docstring for the
authority boundary this whole ratchet exists to enforce.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from manifest_agent.process import redact_text

SCHEMA_VERSION = 2
MAX_EXCEPTION_DAYS = 180

REQUIRED_FIELDS = (
    "identity",
    "check",
    "path",
    "anchor",
    "reason",
    "owner",
    "introduced_base",
    "expires",
)

CommitDate = Callable[[str], "date | None"]


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    """One reviewed exception. ``retired_base`` set = the finding once left the
    base tree; a retired entry can never excuse a reappearance again."""

    identity: str
    check: str
    path: str
    anchor: str
    reason: str
    owner: str
    introduced_base: str
    expires: str  # ISO date
    retired_base: str | None = None


def commit_date(repo_root: Path, sha: str) -> date | None:
    """The committer date of ``sha``, or ``None`` if it cannot be read."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show", "-s", "--format=%cs", sha],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    try:
        return date.fromisoformat(result.stdout.strip())
    except ValueError:
        return None


def _validate_entry(
    item: dict, *, commit_date_fn: CommitDate
) -> tuple[BaselineEntry | None, list[str]]:
    errors = _missing_field_errors(item)
    if errors:
        return None, errors

    try:
        expires = date.fromisoformat(item["expires"])
    except ValueError:
        return None, [f"entry {item['identity']!r}: expires is not an ISO date"]

    base_date = commit_date_fn(item["introduced_base"])
    if base_date is None:
        return None, [
            f"entry {item['identity']!r}: introduced_base commit date unavailable"
        ]
    if expires > base_date + timedelta(days=MAX_EXCEPTION_DAYS):
        return None, [
            f"entry {item['identity']!r}: expires more than {MAX_EXCEPTION_DAYS} days "
            "after introduced_base"
        ]

    reason = item["reason"]
    if redact_text(reason) != reason:
        return None, [f"entry {item['identity']!r}: reason looks secret-shaped"]

    entry = BaselineEntry(
        identity=item["identity"],
        check=item["check"],
        path=item["path"],
        anchor=item["anchor"],
        reason=reason,
        owner=item["owner"],
        introduced_base=item["introduced_base"],
        expires=item["expires"],
        retired_base=item.get("retired_base"),
    )
    return entry, []


def _missing_field_errors(item: dict) -> list[str]:
    errors = []
    for field in REQUIRED_FIELDS:
        if (
            field not in item
            or not isinstance(item[field], str)
            or (field != "anchor" and not item[field])
        ):
            errors.append(
                f"entry {item.get('identity', '?')!r}: missing field {field!r}"
            )
    retired = item.get("retired_base")
    if retired is not None and not isinstance(retired, str):
        errors.append(
            f"entry {item.get('identity', '?')!r}: retired_base must be string or null"
        )
    return errors


@dataclass(frozen=True, slots=True)
class Baseline:
    """A loaded (not yet validated) schema-v2 debt baseline file."""

    raw_entries: tuple[dict, ...] = ()

    @classmethod
    def load(cls, path: Path) -> Baseline:
        """Read the raw entry list; ``validate`` still needs to run before use."""
        if not path.is_file():
            return cls(raw_entries=())
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != SCHEMA_VERSION:
            raise ValueError(
                f"{path}: unsupported debt baseline version {raw.get('version')!r}"
            )
        return cls(raw_entries=tuple(raw.get("entries") or ()))

    def validate(
        self, *, commit_date: CommitDate
    ) -> tuple[dict[str, BaselineEntry], list[str]]:
        """Split entries into (identity -> validated entry, error strings)."""
        valid: dict[str, BaselineEntry] = {}
        errors: list[str] = []
        for item in self.raw_entries:
            entry, item_errors = _validate_entry(item, commit_date_fn=commit_date)
            if item_errors:
                errors.extend(item_errors)
                continue
            valid[entry.identity] = entry
        return valid, errors
