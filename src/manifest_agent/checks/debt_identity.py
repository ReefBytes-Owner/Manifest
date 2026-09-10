"""The debt-ratchet identity scheme (Phase 3 chunk C3).

Split out of ``debt.py`` (identity computation vs. baseline loading/
validation vs. verdict rules are three independent responsibilities) so
neither half grows past the Code Constitution's file-size ceiling.

    identity = sha256(check_id | repo_path | anchor | normalized_message | ordinal)

``anchor`` is the innermost enclosing symbol (function/class), never a line
number; ``ordinal`` is the 0-based index among findings that would otherwise
collide (same check, path, anchor, normalized message) in one file, ordered
by line. Ordinal is what defeats a same-count replacement: a genuinely
different finding at the same "otherwise-equal" slot gets counted separately
from whatever occupied that slot before -- see ``debt.py``'s module
docstring for the full picture and ``tests/python/manifest_agent/
test_debt.py`` for the same-count-replacement proof.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_UNIT_SEP = "␟"  # never appears in a finding message; safe field join


@dataclass(frozen=True, slots=True)
class RawFinding:
    """One finding as produced by a scanner, before identity is assigned."""

    check: str
    path: str  # repo-relative, posix separators
    anchor: str  # innermost enclosing symbol, "" for file-level
    message: str
    line: int


@dataclass(frozen=True, slots=True)
class IdentifiedFinding:
    """A raw finding with its ratchet identity assigned; ``line`` is display-only."""

    identity: str
    check: str
    path: str
    anchor: str
    message: str
    line: int


def normalize_message(message: str) -> str:
    """Collapse digits, whitespace runs, and quoted paths.

    "function has 72 lines" and "...73 lines" must land on the same
    identity while the finding persists -- only the count of lines moved.
    """
    text = re.sub(r"""(['"`])(?:(?!\1).)*\1""", "<path>", message)
    text = re.sub(r"\d+", "#", text)
    return re.sub(r"\s+", " ", text).strip()


def identity_of(check: str, path: str, anchor: str, message: str, ordinal: int) -> str:
    """The ratchet identity: stable across line churn, sensitive to real change."""
    fields = (check, path, anchor, normalize_message(message), str(ordinal))
    payload = _UNIT_SEP.join(fields).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def assign_identities(findings: list[RawFinding]) -> list[IdentifiedFinding]:
    """Group otherwise-equal findings per file and assign line-ordered ordinals."""
    groups: dict[tuple[str, str, str, str], list[RawFinding]] = {}
    for finding in findings:
        key = (
            finding.check,
            finding.path,
            finding.anchor,
            normalize_message(finding.message),
        )
        groups.setdefault(key, []).append(finding)

    out: list[IdentifiedFinding] = []
    for group in groups.values():
        for ordinal, finding in enumerate(sorted(group, key=lambda f: f.line)):
            identity = identity_of(
                finding.check, finding.path, finding.anchor, finding.message, ordinal
            )
            out.append(
                IdentifiedFinding(
                    identity,
                    finding.check,
                    finding.path,
                    finding.anchor,
                    finding.message,
                    finding.line,
                )
            )
    return out
