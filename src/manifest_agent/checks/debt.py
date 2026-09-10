"""Identity-based debt ratchet (Phase 3 chunk C3): verdict rules and the
protected base-tree comparison.

Replaces per-file/rule COUNT allowances (``configs/claude/scripts/constitution/
baseline.py``, ``tools/bundle_link_baseline.py``) with per-finding IDENTITIES
(``debt_identity.py``): a count cannot see a same-count replacement -- fix one
violation and introduce a different one in the same file and the count is
unchanged, so the swap is invisible. An identity keyed on the finding's own
shape -- not its line number, which churns on unrelated edits -- catches
exactly that swap. Baseline loading and per-entry validation live in
``debt_baseline.py``; both are re-exported here so this stays the one import
surface (``from manifest_agent.checks import debt``) the design calls for.

AUTHORITY BOUNDARY -- read before wiring this into anything: a baseline delta
computed here (``propose_baseline`` / ``write_proposal``) is a PROPOSAL for a
human reviewer, never agent-granted authority. Nothing in this module may
replace the committed ``config/debt-baseline.json`` automatically; the write
helpers refuse to write inside the source tree for exactly that reason. The
``release`` profile is expected to run with ``baseline_from_base=True`` so a
candidate can never ship its own exceptions.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

from .debt_baseline import (
    MAX_EXCEPTION_DAYS,
    REQUIRED_FIELDS,
    SCHEMA_VERSION,
    Baseline,
    BaselineEntry,
    CommitDate,
    commit_date,
)
from .debt_identity import (
    IdentifiedFinding,
    RawFinding,
    assign_identities,
    identity_of,
    normalize_message,
)

# Three short literals (not one long one) so this re-export list never reads
# as a data table on its own -- this module re-exports debt_baseline/
# debt_identity on purpose (see module docstring); it is not configuration
# data. `__all__ +=` keeps each group a plain literal so ruff still
# recognizes the re-export (a computed __all__ loses that).
__all__ = [
    "MAX_EXCEPTION_DAYS",
    "REQUIRED_FIELDS",
    "SCHEMA_VERSION",
    "BaseUnavailableError",
    "Baseline",
    "BaselineEntry",
    "CommitDate",
]
__all__ += [
    "DebtReport",
    "EvaluationInputs",
    "IdentifiedFinding",
    "ProposalInputs",
    "RawFinding",
    "Verdict",
]
__all__ += [
    "assign_identities",
    "commit_date",
    "evaluate",
    "identity_of",
    "materialize_base_tree",
    "normalize_message",
    "propose_baseline",
    "write_proposal",
]


class BaseUnavailableError(RuntimeError):
    """The protected base tree could not be materialized."""


@dataclass(frozen=True, slots=True)
class Verdict:
    """One finding-or-entry the ratchet blocks on, with the rule that fired."""

    identity: str
    check: str
    path: str
    anchor: str
    message: str
    line: int
    reason: str  # "new debt" | "expired exception" | "restored debt" | "stale entry: retire it"


@dataclass(frozen=True, slots=True)
class DebtReport:
    """The verdict for one ratchet run: 0 PASS / 2 FAIL / 3 BLOCKED, never conflated."""

    status: str  # PASS | FAIL | BLOCKED
    fails: tuple[Verdict, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    proposed_exceptions: tuple[BaselineEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class EvaluationInputs:
    """Everything one debt-ratchet decision needs; travels together on purpose."""

    findings_cand: list[RawFinding]
    findings_base: list[RawFinding]
    baseline_cand: Baseline
    baseline_base: Baseline
    today: date
    commit_date: CommitDate
    baseline_from_base: bool = False


def evaluate(inputs: EvaluationInputs) -> DebtReport:
    """Apply the five verdict rules. See module docstring for the identity scheme."""
    commit_date_fn = inputs.commit_date
    cand_valid, cand_errors = inputs.baseline_cand.validate(commit_date=commit_date_fn)
    base_valid, base_errors = inputs.baseline_base.validate(commit_date=commit_date_fn)
    blocked = [*cand_errors, *(f"base baseline: {e}" for e in base_errors)]
    if blocked:
        return DebtReport("BLOCKED", blocked_reasons=tuple(blocked))

    ident_cand = {f.identity: f for f in assign_identities(inputs.findings_cand)}
    ident_base_ids = {f.identity for f in assign_identities(inputs.findings_base)}
    active = base_valid if inputs.baseline_from_base else cand_valid

    fails = _findings_fails(ident_cand, ident_base_ids, active, inputs.today)
    fails.extend(_stale_fails(active, ident_cand))

    proposed = tuple(
        entry for identity, entry in cand_valid.items() if identity not in base_valid
    )
    status = "FAIL" if fails else "PASS"
    return DebtReport(status, fails=tuple(fails), proposed_exceptions=proposed)


def _findings_fails(ident_cand, ident_base_ids, active, today: date) -> list[Verdict]:
    fails: list[Verdict] = []
    for identity, finding in ident_cand.items():
        in_base = identity in ident_base_ids
        entry = active.get(identity)
        if entry is None:
            if not in_base:
                fails.append(_verdict(finding, "new debt"))
            continue
        if date.fromisoformat(entry.expires) < today:
            fails.append(_verdict(finding, "expired exception"))
        elif not in_base and entry.retired_base is not None:
            fails.append(_verdict(finding, "restored debt"))
    return fails


def _stale_fails(active, ident_cand) -> list[Verdict]:
    return [
        Verdict(
            identity,
            entry.check,
            entry.path,
            entry.anchor,
            entry.reason,
            0,
            "stale entry: retire it",
        )
        for identity, entry in active.items()
        if entry.retired_base is None and identity not in ident_cand
    ]


def _verdict(finding: IdentifiedFinding, reason: str) -> Verdict:
    return Verdict(
        finding.identity,
        finding.check,
        finding.path,
        finding.anchor,
        finding.message,
        finding.line,
        reason,
    )


def materialize_base_tree(repo_root: Path, base_sha: str) -> Path:
    """``git archive <base_sha>`` into a fresh sibling temp dir.

    Never the source worktree: a mutable candidate tree comparing itself
    against itself would defeat every verdict rule above. The archive is
    extracted into a new ``tempfile.mkdtemp`` directory (outside
    ``repo_root`` by construction) and the caller owns cleanup.
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="manifest-debt-base-"))
    archive = subprocess.Popen(
        ["git", "-C", str(repo_root), "archive", "--format=tar", base_sha],
        stdout=subprocess.PIPE,
    )
    try:
        extract = subprocess.run(
            ["tar", "-x", "-C", str(tmp_root)], stdin=archive.stdout, timeout=60
        )
    finally:
        if archive.stdout is not None:
            archive.stdout.close()
        archive.wait(timeout=60)
    if archive.returncode != 0 or extract.returncode != 0:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise BaseUnavailableError(f"git archive {base_sha} failed or was empty")
    return tmp_root


@dataclass(frozen=True, slots=True)
class ProposalInputs:
    """Everything one baseline proposal needs; travels together on purpose."""

    findings_cand: list[RawFinding]
    findings_base: list[RawFinding]
    existing_cand: Baseline
    repo_root: Path
    base_sha: str
    today: date
    owner: str
    reason: str
    commit_date: CommitDate = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.commit_date is None:
            object.__setattr__(
                self, "commit_date", lambda sha: commit_date(self.repo_root, sha)
            )


def propose_baseline(inputs: ProposalInputs) -> dict:
    """Build the COMPLETE proposed baseline document (never partial, never in place).

    Existing entries whose finding has disappeared from the base tree are
    auto-retired (``retired_base`` set); every candidate finding with no
    baseline entry and no match in the base tree becomes a new placeholder
    entry a human must fill in with a real reason before this is mergeable.
    """
    cand_valid, _ = inputs.existing_cand.validate(commit_date=inputs.commit_date)
    ident_cand = assign_identities(inputs.findings_cand)
    ident_base_ids = {f.identity for f in assign_identities(inputs.findings_base)}

    out: dict[str, BaselineEntry] = {}
    for identity, entry in cand_valid.items():
        if entry.retired_base is None and identity not in ident_base_ids:
            entry = replace(entry, retired_base=inputs.base_sha)
        out[identity] = entry

    expires = (inputs.today + timedelta(days=MAX_EXCEPTION_DAYS)).isoformat()
    for finding in ident_cand:
        if finding.identity in out or finding.identity in ident_base_ids:
            continue
        out[finding.identity] = BaselineEntry(
            identity=finding.identity,
            check=finding.check,
            path=finding.path,
            anchor=finding.anchor,
            reason=inputs.reason,
            owner=inputs.owner,
            introduced_base=inputs.base_sha,
            expires=expires,
            retired_base=None,
        )
    return _render(out)


def _render(entries: dict[str, BaselineEntry]) -> dict:
    return {
        "version": SCHEMA_VERSION,
        "_comment": (
            "PROPOSAL -- for human review before replacing config/debt-baseline.json. "
            "Never applied automatically; an agent may not grant itself an exception "
            "here. See docs/SHARED_CHECKS.md (debt.constitution / debt.bundle-links)."
        ),
        "entries": [
            {
                "identity": e.identity,
                "check": e.check,
                "path": e.path,
                "anchor": e.anchor,
                "reason": e.reason,
                "owner": e.owner,
                "introduced_base": e.introduced_base,
                "expires": e.expires,
                "retired_base": e.retired_base,
            }
            for e in sorted(
                entries.values(), key=lambda e: (e.path, e.check, e.identity)
            )
        ],
    }


def write_proposal(payload: dict, output: Path, repo_root: Path) -> None:
    """Write ``payload`` to ``output``; refuse any path inside ``repo_root``."""
    resolved_output = output.expanduser().resolve()
    resolved_root = repo_root.expanduser().resolve()
    if resolved_output == resolved_root or resolved_output.is_relative_to(
        resolved_root
    ):
        raise ValueError("propose-baseline output must be outside the source tree")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
