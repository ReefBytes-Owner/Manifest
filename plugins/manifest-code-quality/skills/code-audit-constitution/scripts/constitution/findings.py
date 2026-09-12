"""The one shape every check returns and every caller renders.

Checks never format their own output — a second renderer is the duplication
CON-003 exists to prevent, and the hook, the CLI, and the pre-commit gate each
need the same findings shaped differently.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

# Ordered weakest to strongest; index doubles as the comparison key.
SEVERITIES = ("info", "warn", "error")


def worst(severities: list[str]) -> str:
    """Return the strongest severity present, or "info" for an empty list."""
    return max(severities, key=SEVERITIES.index, default="info")


@dataclass(frozen=True, slots=True)
class Finding:
    """One violation, at one place, with the fix already named.

    `remedy` is mandatory: a finding that reports a problem without naming the
    move the author should make is a finding people learn to scroll past.
    """

    check: str
    article: str
    severity: str
    path: Path
    line: int
    message: str
    remedy: str
    anchor: str = ""
    """Innermost enclosing symbol (debt-ratchet identity input, C3); "" if file-level
    or unknown. Never a line number -- see ``manifest_agent.checks.debt``."""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def render_text(findings: list[Finding]) -> str:
    """Human/terminal form: one clickable location per line, remedy indented."""
    out = []
    for f in sorted(findings, key=_sort_key):
        out.append(f"{f.location}: {f.severity}: [{f.check}/{f.article}] {f.message}")
        out.append(f"    -> {f.remedy}")
    return "\n".join(out)


def render_json(findings: list[Finding]) -> str:
    payload = [
        {**asdict(f), "path": str(f.path)} for f in sorted(findings, key=_sort_key)
    ]
    return json.dumps(payload, indent=2)


def render_context(findings: list[Finding], limit: int = 12) -> str:
    """Compact form for injection into an agent's context.

    Bounded on purpose: injected text is paid for on every edit, so this reports
    the strongest findings and states how many it left out rather than growing
    without limit.
    """
    ranked = sorted(findings, key=_sort_key)
    shown, hidden = ranked[:limit], len(ranked) - limit
    lines = [f"- {f.check} L{f.line}: {f.message} -> {f.remedy}" for f in shown]
    if hidden > 0:
        lines.append(
            f"- ... and {hidden} more (run constitution_check.py for the full list)"
        )
    return "\n".join(lines)


def _sort_key(f: Finding) -> tuple:
    return (-SEVERITIES.index(f.severity), str(f.path), f.line, f.check)
