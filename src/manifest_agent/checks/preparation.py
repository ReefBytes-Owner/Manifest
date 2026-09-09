"""Prepare candidate-local ignored outputs with serialized identity receipts."""

from __future__ import annotations

import fcntl
import fnmatch
import json
import os
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from manifest_agent.process import redact_text

from .candidate import (
    CandidateBlockedError,
    _atomic_json,
    _git,
    _json_digest,
    _safe_path,
    _walk,
    candidate_digest,
)
from .models import Candidate, CheckResult, PreparationSpec
from .process import ProcessResult, run_argv

PreparationPreflight = Callable[[PreparationSpec], CheckResult | None]


def _select(snapshot: dict, roots: tuple[str, ...]) -> dict:
    return {
        name: identity
        for name, identity in snapshot.items()
        if any(
            fnmatch.fnmatchcase(name, root) or name.startswith(root.rstrip("/") + "/")
            for root in roots
        )
    }


def _preparation_paths(root: Path, spec: PreparationSpec) -> Path:
    cwd = _safe_path(root, spec.cwd)
    if cwd.is_symlink() or not cwd.is_dir():
        raise CandidateBlockedError("unsafe preparation cwd")
    for output in spec.outputs:
        _safe_path(root, output)
        ignored = _git(
            root,
            "check-ignore",
            "--no-index",
            "--non-matching",
            "--verbose",
            "--stdin",
            "-z",
            data=os.fsencode(output.rstrip("/") + "/") + b"\0",
            allowed_codes=(0, 1),
        )
        if not ignored.split(b"\0")[2]:
            raise CandidateBlockedError("preparation output must be ignored")
    return cwd


def _check_changes(
    root: Path, baseline: str, before: dict, outputs: tuple[str, ...]
) -> dict:
    after = _walk(root, exclude=(".git",))
    if candidate_digest(root) != baseline:
        raise CandidateBlockedError("candidate inputs changed during preparation")
    changes = {
        name
        for name in before.keys() | after.keys()
        if before.get(name) != after.get(name)
    }
    allowed = set(_select(dict.fromkeys(changes), outputs))
    ancestors = {
        str(parent)
        for output in outputs
        for parent in Path(output).parents
        if str(parent) != "." and str(parent) not in before
    }
    if changes - allowed - ancestors:
        raise CandidateBlockedError("undeclared preparation output mutation")
    return after


def _outcome(
    spec: PreparationSpec, result: ProcessResult, inputs: tuple[str, ...]
) -> CheckResult:
    if result.error or result.timed_out or result.returncode != 0:
        message = result.error or (
            "preparation timeout" if result.timed_out else "preparation exit failure"
        )
        return CheckResult(
            spec.id,
            "BLOCKED",
            result.returncode,
            result.duration_seconds,
            message + "\n" + result.stdout + result.stderr,
            tuple(inputs),
        )
    return CheckResult(
        spec.id,
        "PASS",
        result.returncode,
        result.duration_seconds,
        result.stdout + result.stderr,
        tuple(inputs),
    )


def _blocked_preparation(spec: PreparationSpec, diagnostic: str) -> CheckResult:
    return CheckResult(spec.id, "BLOCKED", None, 0.0, diagnostic, ())


def _preparation(
    candidate: Candidate, spec: PreparationSpec, env: dict[str, str], receipt: dict
) -> CheckResult:
    try:
        root = candidate.root
        baseline = json.loads((root / ".git/candidate-state.json").read_text())[
            "digest"
        ]
        if candidate_digest(root) != baseline:
            raise CandidateBlockedError("candidate inputs changed")
        cwd = _preparation_paths(root, spec)
        before = _walk(root, exclude=(".git",))
        inputs = _select(before, spec.inputs)
        if any(not _select(before, (pattern,)) for pattern in spec.inputs):
            raise CandidateBlockedError("missing preparation input")
        outputs = _select(before, spec.outputs)
        identity = {
            "spec_digest": _json_digest(asdict(spec)),
            "inputs": inputs,
            "outputs": outputs,
        }
        previous = receipt["preparations"].get(spec.id)
        if previous == identity and outputs:
            return CheckResult(
                spec.id, "PASS", 0, 0.0, "completed preparation receipt", tuple(inputs)
            )
        if spec.id in receipt["preparations"]:
            del receipt["preparations"][spec.id]
            _atomic_json(candidate.preparation_receipt, receipt)
        metadata = _walk(root / ".git")
        result = run_argv(
            spec.argv, cwd=cwd, env=env, timeout_seconds=spec.timeout_seconds
        )
        if (root / ".git").is_symlink() or metadata != _walk(root / ".git"):
            raise CandidateBlockedError(
                "candidate Git metadata changed during preparation"
            )
        after = _check_changes(root, baseline, before, spec.outputs)
        outcome = _outcome(spec, result, tuple(inputs))
        if outcome.status == "BLOCKED":
            return outcome
        outputs = _select(after, spec.outputs)
        if any(output.rstrip("/") not in outputs for output in spec.outputs):
            raise CandidateBlockedError("missing preparation output")
        identity["outputs"] = outputs
        receipt["preparations"][spec.id] = identity
        _atomic_json(candidate.preparation_receipt, receipt)
        return outcome
    except (CandidateBlockedError, OSError, ValueError, KeyError) as error:
        return CheckResult(spec.id, "BLOCKED", None, 0.0, redact_text(str(error)), ())


def _prepare_candidate_guarded(
    candidate: Candidate,
    preparations: tuple[PreparationSpec, ...],
    env: dict[str, str],
    preflight: PreparationPreflight | None,
) -> tuple[CheckResult, ...]:
    lock = candidate.root / ".git/preparation.lock"
    try:
        if (
            candidate.root.is_symlink()
            or (candidate.root / ".git").is_symlink()
            or candidate.preparation_receipt
            != candidate.root / ".git/preparation-receipt.json"
            or candidate.preparation_receipt.is_symlink()
        ):
            raise CandidateBlockedError("unsafe candidate preparation metadata path")
        with os.fdopen(
            os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a"
        ) as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            receipt = {
                "schema_version": 1,
                "root": str(candidate.root),
                "tree_sha": candidate.tree_sha,
                "source_digest": candidate.source_digest,
                "preparations": {},
            }
            if candidate.preparation_receipt.exists():
                previous = json.loads(candidate.preparation_receipt.read_text())
                if (
                    not isinstance(previous, dict)
                    or set(previous) != set(receipt)
                    or not isinstance(previous["preparations"], dict)
                ):
                    raise CandidateBlockedError("invalid preparation receipt")
                if all(
                    previous.get(key) == value
                    for key, value in receipt.items()
                    if key != "preparations"
                ):
                    receipt = previous
            results = []
            stopped = False
            for spec in preparations:
                if stopped:
                    result = _blocked_preparation(
                        spec, "prior candidate preparation did not pass"
                    )
                else:
                    result = preflight(spec) if preflight else None
                    result = result or _preparation(candidate, spec, env, receipt)
                    stopped = preflight is not None and result.status != "PASS"
                results.append(result)
            return tuple(results)
    except (CandidateBlockedError, OSError, ValueError) as error:
        return tuple(
            CheckResult(spec.id, "BLOCKED", None, 0.0, redact_text(str(error)), ())
            for spec in preparations
        )


def prepare_candidate(
    candidate: Candidate, preparations: tuple[PreparationSpec, ...], env: dict[str, str]
) -> tuple[CheckResult, ...]:
    """Serialize candidate preparations and reuse only matching identity receipts."""
    return _prepare_candidate_guarded(candidate, preparations, env, None)
