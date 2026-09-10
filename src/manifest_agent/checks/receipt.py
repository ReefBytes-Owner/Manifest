"""Receipt schema v2: provenance digests and the derived `receipt_key`.

Implements phase-3-5-decisions.md section 3e. A receipt is evidence, never a
skip permission: nothing here shortcuts execution. `build_report` assembles
the full receipt -- unchanged v1 shape plus the v2-only fields
`toolchain_digest`, interpreter provenance, `environment_digest`,
`expires_at` (security/release only) and the derived `receipt_key`. A
consumer (Phase 4 adapter, or `aggregate.py` here) recomputes these
independently from the receipt's own inputs; a receipt is never trusted
just because it claims a `receipt_key` -- `aggregate.py` cross-checks
`toolchain_digest` consistency and `expires_at` freshness, it never reads
`receipt_key` as an assertion of validity.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import toolchain
from .models import Candidate, CheckSpec, ProfileSelector, RunContext

EXPIRY_HOURS = 24
EXPIRING_PROFILES = frozenset({"security", "release"})
_KEY_SEPARATOR = "\x1f"


def _digest(payload: Mapping) -> str:
    serialized = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def resolved_tool_digests(tool_results: Mapping) -> dict[str, str]:
    """`{tool: sha256(exe)}` for every `store:` tool actually resolved this
    run -- plain-name tools (python3, bash) contribute nothing here."""
    digests: dict[str, str] = {}
    for outcome in tool_results.values():
        resolved = outcome[3]
        if resolved is not None:
            digests[resolved.bundle] = resolved.tool_sha256
    return digests


def toolchain_digest(resolved: Mapping[str, str]) -> str:
    """sha256 over the sorted `{tool: sha256(exe)}` mapping."""
    return _digest(dict(sorted(resolved.items())))


def environment_digest(env: Mapping[str, str], platform: str) -> str:
    """sha256 of the platform triple plus forwarded environment values,
    excluding `HOME` (a receipt must not vary by which developer produced
    it)."""
    filtered = {key: value for key, value in env.items() if key != "HOME"}
    return _digest({"platform": platform, "env": dict(sorted(filtered.items()))})


def interpreter_provenance(store_manifest: Mapping | None) -> dict[str, str]:
    """`sys.version` + `sys.executable` sha256, as recorded in the store's
    `manifest.json` at provisioning time. Empty strings when unavailable --
    never fabricated."""
    interpreter = (store_manifest or {}).get("interpreter") or {}
    return {
        "version": str(interpreter.get("version", "")),
        "executable_sha256": str(interpreter.get("executable_sha256", "")),
    }


def interpreter_from_store(
    env: Mapping[str, str], candidate_root: Path
) -> dict[str, str]:
    """`interpreter_provenance` for whatever store the current run resolved
    against, or empty when no store is reachable/configured."""
    try:
        store = toolchain.store_root(env, candidate_root)
    except toolchain.UnsafeStoreLocationError:
        return interpreter_provenance(None)
    return interpreter_provenance(toolchain.load_store_manifest(store))


def expires_at(profile: str, produced: datetime) -> str | None:
    """`produced + 24h` for `security`/`release` profiles only -- advisory
    feeds are time-sensitive; other profiles never expire on their own."""
    if profile not in EXPIRING_PROFILES:
        return None
    return (produced + timedelta(hours=EXPIRY_HOURS)).isoformat()


def is_expired(expires_at_value: str | None, now: datetime) -> bool:
    """An unset expiry never expires; an unparseable one always does --
    unverifiable is BLOCKED, never trusted."""
    if not expires_at_value:
        return False
    try:
        deadline = datetime.fromisoformat(expires_at_value)
    except ValueError:
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return now >= deadline


@dataclass(frozen=True)
class DigestInputs:
    """The six identity components `receipt_key` folds together."""

    profile: str
    group: str | None
    candidate_digest: str
    config_digest: str
    toolchain_digest: str
    environment_digest: str


def receipt_key(inputs: DigestInputs) -> str:
    """`sha256(profile | group | candidate_digest | config_digest |
    toolchain_digest | environment_digest)` -- the identity a consumer asks
    "is there evidence for exactly this state" against. Never a skip
    permission: absence or mismatch means re-run, not degrade to PASS."""
    payload = _KEY_SEPARATOR.join(
        [
            inputs.profile,
            inputs.group or "",
            inputs.candidate_digest,
            inputs.config_digest,
            inputs.toolchain_digest,
            inputs.environment_digest,
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class FieldInputs:
    """What `build_fields` needs, gathered once per run."""

    profile: str
    group: str | None
    tool_results: Mapping
    env: Mapping[str, str]
    candidate_root: Path
    candidate_digest: str
    config_digest: str
    produced: datetime | None = None


def build_fields(inputs: FieldInputs) -> dict[str, object]:
    """Every v2-only receipt field, computed from this run's actual inputs."""
    tc_digest = toolchain_digest(resolved_tool_digests(inputs.tool_results))
    env_digest = environment_digest(inputs.env, toolchain.current_platform())
    interpreter = interpreter_from_store(inputs.env, inputs.candidate_root)
    produced = inputs.produced or datetime.now(UTC)
    key = receipt_key(
        DigestInputs(
            profile=inputs.profile,
            group=inputs.group,
            candidate_digest=inputs.candidate_digest,
            config_digest=inputs.config_digest,
            toolchain_digest=tc_digest,
            environment_digest=env_digest,
        )
    )
    return {
        "toolchain_digest": tc_digest,
        "interpreter_version": interpreter["version"],
        "interpreter_executable_sha256": interpreter["executable_sha256"],
        "environment_digest": env_digest,
        "expires_at": expires_at(inputs.profile, produced),
        "receipt_key": key,
    }


def reported_candidate_digest(candidate: Candidate) -> str:
    """The candidate-state digest a report claims -- receipt-local, never
    trusted as authoritative by a consumer without independent recompute."""
    try:
        state = json.loads((candidate.root / ".git/candidate-state.json").read_text())
    except (OSError, ValueError, TypeError):
        return ""
    digest = state.get("digest") if isinstance(state, dict) else None
    return digest if isinstance(digest, str) else ""


@dataclass(frozen=True)
class ReportInputs:
    """What `build_report` needs to assemble one profile/group receipt."""

    context: RunContext
    selector: ProfileSelector
    checks: tuple[CheckSpec, ...]
    result_dicts: list[dict]
    start: float


def build_report(
    inputs: ReportInputs, pending: list[str], status: str, config_digest: str
) -> dict[str, object]:
    """Assemble the full v2 receipt: the unchanged v1 field shape plus the
    provenance digests and `receipt_key` from `build_fields`."""
    context, selector = inputs.context, inputs.selector
    candidate = context.candidate
    candidate_digest = reported_candidate_digest(candidate)
    fields = build_fields(
        FieldInputs(
            profile=selector.profile,
            group=selector.group,
            tool_results=context.tool_results,
            env=context.env,
            candidate_root=candidate.root,
            candidate_digest=candidate_digest,
            config_digest=config_digest,
        )
    )
    return {
        "schema_version": 2,
        "profile": selector.profile,
        "group": selector.group,
        "partial": selector.group is not None,
        "candidate_digest": candidate_digest,
        "source_digest": candidate.source_digest,
        "head_sha": candidate.head_sha,
        "base_sha": candidate.base_sha,
        "tree_sha": candidate.tree_sha,
        "config_digest": config_digest,
        "coverage_pending": pending,
        "required_ids": [check.id for check in inputs.checks],
        "results": inputs.result_dicts,
        "status": status,
        "duration_seconds": time.monotonic() - inputs.start,
        **fields,
    }
