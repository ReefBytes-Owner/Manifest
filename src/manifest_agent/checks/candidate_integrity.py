"""Post-materialization identity checks for a disposable candidate.

Split out of `runner.py` to keep it under the Code Constitution's 500-line
ceiling. `identity_error` re-verifies everything `candidate.materialize_
candidate` sealed at creation time (`candidate-state.json`'s digest, HEAD,
tree, and the source checkout's own digest) -- called both before and after
a check runs, so a check body that mutates its own tree is caught even when
it also happens to report success. `post_run_diagnostic` additionally folds
in the store's own before/after fingerprint (Correction 3's launcher-swap
detection) so a single BLOCKED reason covers "the candidate changed" and
"the store changed" without a check needing to ask twice.
"""

from __future__ import annotations

import json

from manifest_agent.process import redact_text

from . import toolchain
from .candidate import (
    CandidateBlockedError,
    _git,
    _walk,
    candidate_digest,
    git_dir_snapshot,
)
from .models import Candidate


def identity_error(candidate: Candidate) -> str:
    """Empty unless the candidate or its source have drifted from the
    identities `materialize_candidate` sealed."""
    try:
        root = candidate.root
        source = candidate.source_root
        if root.is_symlink() or root.resolve(strict=True) != root:
            return "candidate identity unavailable: unsafe root"
        git_dir = root / ".git"
        state_path = git_dir / "candidate-state.json"
        if git_dir.is_symlink() or state_path.is_symlink():
            return "candidate identity unavailable: unsafe metadata"
        state = json.loads(state_path.read_text())
        if set(state) != {"digest"} or not isinstance(state["digest"], str):
            return "candidate identity unavailable: invalid state"
        if candidate_digest(root) != state["digest"]:
            return "candidate identity changed"
        if _git(root, "rev-parse", "HEAD").decode().strip() != candidate.head_sha:
            return "candidate identity changed: HEAD"
        if _git(root, "write-tree").decode().strip() != candidate.tree_sha:
            return "candidate identity changed: tree"
        if source.is_symlink() or source.resolve(strict=True) != source:
            return "source identity unavailable: unsafe root"
        if candidate_digest(source) != candidate.source_digest:
            return "source identity changed"
    except (CandidateBlockedError, OSError, ValueError, KeyError, TypeError) as error:
        return "candidate or source identity unavailable: " + redact_text(str(error))
    return ""


def post_run_diagnostic(
    candidate: Candidate,
    pre_run: tuple[dict, dict],
    resolved: toolchain.ResolvedTool | None,
    store_before: dict[str, str],
    env: dict[str, str],
) -> str:
    """Empty unless the candidate tree or the resolved store changed
    mid-run. `pre_run` is `(before, git_before)`, the pre-run snapshots
    `_walk`/`git_dir_snapshot` produced."""
    before, git_before = pre_run
    try:
        changed = before != _walk(
            candidate.root, exclude=(".git",)
        ) or git_before != git_dir_snapshot(candidate.root)
        current_identity_error = identity_error(candidate)
    except (CandidateBlockedError, OSError) as error:
        changed, current_identity_error = True, redact_text(str(error))
    return toolchain.integrity_reason(
        store_before,
        toolchain.fingerprint_for(resolved, env),
        changed,
        current_identity_error,
    )
