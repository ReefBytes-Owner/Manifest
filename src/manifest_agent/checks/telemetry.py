"""Append-only run telemetry: one record per `manifest check` / `manifest
hook` / CI run, written to `$XDG_STATE_HOME/manifest/telemetry/runs.jsonl`.

Implements phase-3-5-decisions.md section 5c. The whole point of this
module: **unknown is never zero**. `model_id`, `runtime.version` and `cost`
default to `"unknown"` and are filled only from values a caller actually
supplies -- never inferred, never guessed. `cost.status` is `"known"` only
with a provider-reported usage figure; there is no zero default.

Telemetry is an observer, never a gate: `write_record` never raises. A write
failure (unwritable directory, race, disk full) degrades silently and must
never change a check's verdict or exit code. Writes are confined to
`$XDG_STATE_HOME/manifest/telemetry/`; never a paid service, never the
network -- the JSONL file is the only sink.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from manifest_agent.paths import xdg_paths
from manifest_agent.process import redact_text

from .candidate import CandidateBlockedError, _git

SCHEMA = 1
RECORD_FILENAME = "runs.jsonl"
LOCK_FILENAME = ".runs.lock"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class RuntimeInfo:
    """`client`/`version` as the adapter actually supplied them -- never a
    guess. `version` stays `"unknown"` until a live client pilot (C10)."""

    client: str = UNKNOWN
    version: str = UNKNOWN


@dataclass(frozen=True)
class CostInfo:
    """`status` is `"known"` only with a provider-reported usage figure.
    There is no zero default -- an absent figure is `"unknown"`, not `0`."""

    status: str = UNKNOWN
    amount_usd: float | None = None

    def payload(self) -> dict[str, object]:
        """The record's `cost` field: `{"status": "unknown"}` unless a real
        provider-reported figure is present."""
        if self.status != "known" or self.amount_usd is None:
            return {"status": UNKNOWN}
        return {"status": "known", "amount_usd": self.amount_usd}


@dataclass(frozen=True)
class RunRecordInputs:
    """Everything one telemetry record needs, gathered once per run."""

    profile: str
    status: str
    duration_seconds: float
    receipt_key: str = ""
    head_sha: str = ""
    candidate_lineage: str | None = None
    attempt: int = 1
    model_id: str = UNKNOWN
    runtime: RuntimeInfo = field(default_factory=RuntimeInfo)
    cost: CostInfo = field(default_factory=CostInfo)
    ts: str | None = None


def build_record(inputs: RunRecordInputs) -> dict[str, object]:
    """Assemble one telemetry record. Never fabricates a value the caller
    did not supply -- unset fields render as `"unknown"`, not `0`/`""`."""
    record = {
        "schema": SCHEMA,
        "ts": inputs.ts or datetime.now(UTC).isoformat(),
        "profile": inputs.profile,
        "receipt_key": inputs.receipt_key,
        "head_sha": inputs.head_sha,
        "candidate_lineage": inputs.candidate_lineage,
        "attempt": inputs.attempt,
        "status": inputs.status,
        "duration_seconds": inputs.duration_seconds,
        "model_id": inputs.model_id,
        "runtime": {"client": inputs.runtime.client, "version": inputs.runtime.version},
        "cost": inputs.cost.payload(),
    }
    return _redact(record)


def _redact(value: object) -> object:
    """Recursively apply the shared credential redaction helper so no
    environment value or token can reach the JSONL through a free-text
    field (`head_sha`, `candidate_lineage`, diagnostics-derived strings)."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def telemetry_dir(env: Mapping[str, str] | None = None) -> Path:
    """`$XDG_STATE_HOME/manifest/telemetry` -- the only sink; never inside
    the repository or a candidate checkout."""
    return xdg_paths(env).state / "telemetry"


def telemetry_path(env: Mapping[str, str] | None = None) -> Path:
    """The append-only run log itself: `<telemetry_dir>/runs.jsonl`."""
    return telemetry_dir(env) / RECORD_FILENAME


def append_record(record: Mapping[str, object], env: Mapping[str, str] | None = None) -> None:
    """Append one JSON line, serialized against concurrent writers with the
    same `fcntl` idiom `preparation.py`/`hooks/receipt.py` already use.
    Raises on failure -- callers that must never fail wrap this in
    `write_record`."""
    directory = telemetry_dir(env)
    directory.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    lock_fd = os.open(
        directory / LOCK_FILENAME, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        with open(directory / RECORD_FILENAME, "a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def write_record(inputs: RunRecordInputs, env: Mapping[str, str] | None = None) -> None:
    """Best-effort telemetry write: never raises. A telemetry write failure
    must never change the exit code or verdict of the check it observes."""
    try:
        append_record(build_record(inputs), env)
    except (OSError, ValueError, TypeError):
        # Documented fallback: telemetry is an observer, never a gate --
        # nothing was lost that a verdict depended on, so degrading to "no
        # record this run" (rather than raising) is the whole point.
        return


def resolve_lineage(env: Mapping[str, str], source_root: Path | None) -> str | None:
    """`<branch|pr-number|null>`. CI's `GITHUB_REF` wins when present (a PR
    merge ref yields the PR number); otherwise the source checkout's current
    branch; otherwise `null` -- never fabricated."""
    ref = env.get("GITHUB_REF", "")
    if ref.startswith("refs/pull/") and ref.endswith("/merge"):
        number = ref[len("refs/pull/") : -len("/merge")]
        if number.isdigit():
            return number
    branch_name = env.get("GITHUB_REF_NAME") or env.get("GITHUB_HEAD_REF")
    if branch_name:
        return branch_name
    if source_root is None:
        return None
    try:
        branch = _git(source_root, "symbolic-ref", "--short", "-q", "HEAD").decode().strip()
    except CandidateBlockedError:
        return None
    return branch or None


def count_prior_attempts(lineage: str | None, env: Mapping[str, str] | None = None) -> int:
    """1-based attempt number: one more than the number of existing records
    sharing this `candidate_lineage`. A `null` lineage cannot be tracked
    across attempts, so it is always attempt 1."""
    if lineage is None:
        return 1
    path = telemetry_path(env)
    if not path.is_file():
        return 1
    count = 0
    try:
        with open(path, encoding="utf-8") as stream:
            for raw_line in stream:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    parsed = json.loads(raw_line)
                except ValueError:
                    continue
                if isinstance(parsed, dict) and parsed.get("candidate_lineage") == lineage:
                    count += 1
    except OSError:
        return 1
    return count + 1


@dataclass(frozen=True)
class RecordRunRequest:
    """What one run needs to observe itself -- the arguments that always
    travel together for a `record_run` call."""

    profile: str
    status: str
    duration_seconds: float
    source_root: Path | None
    receipt_key: str = ""
    head_sha: str = ""
    runtime: RuntimeInfo = field(default_factory=RuntimeInfo)
    cost: CostInfo = field(default_factory=CostInfo)


def record_run(request: RecordRunRequest, env: Mapping[str, str] | None = None) -> None:
    """The one safety boundary for the whole telemetry path: resolve
    lineage, compute the attempt number, build and append the record.
    Never raises -- every step it performs (lineage/attempt resolution,
    record assembly, the append itself) is covered by this single narrow
    `try`, so a telemetry failure can never change the verdict of the
    `manifest check` / `manifest hook` run it observes."""
    try:
        environment = env if env is not None else os.environ
        lineage = resolve_lineage(environment, request.source_root)
        inputs = RunRecordInputs(
            profile=request.profile,
            status=request.status,
            duration_seconds=request.duration_seconds,
            receipt_key=request.receipt_key,
            head_sha=request.head_sha,
            candidate_lineage=lineage,
            attempt=count_prior_attempts(lineage, environment),
            runtime=request.runtime,
            cost=request.cost,
        )
        append_record(build_record(inputs), environment)
    except (OSError, ValueError, TypeError, CandidateBlockedError):
        # Documented fallback: telemetry is an observer, never a gate --
        # nothing was lost that a verdict depended on, so degrading to "no
        # record this run" (rather than raising) is the whole point.
        return
