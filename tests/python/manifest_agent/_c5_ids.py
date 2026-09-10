"""C5 additive check-id sets shared by ``test_check_profile_parity.py``.

Split out purely to keep that file under the Code Constitution's file-size
ceiling: these ids have no legacy pre-commit/CI job in
``config/check-preservation.json`` to preserve 1:1 (same reasoning as C3's
``DEBT_IDS``/``DEBT_RELEASE_IDS`` in the test file itself), so they are
additive to ``RETAINED_IDS`` rather than drawn from the frozen oracle.
``hook.pyright`` (PATH, unpinned) is removed the same change that adds
``types.python`` (phase-3-5-decisions.md 3d). ``dependency.audit.python``/
``.node`` are registered (BLOCKED-path bodies only) but deliberately NOT
wired into any profile -- enabling them awaits chunk C8.
"""

from __future__ import annotations

C5_FULL_RELEASE_IDS = frozenset(
    {
        "types.python",
        "package.node-runtime",
        "dependency.lock.root",
        "dependency.lock.node",
    }
)
C5_SECURITY_RELEASE_IDS = frozenset({"security.semgrep"})
C5_DECLARED_ONLY_IDS = frozenset({"dependency.audit.python", "dependency.audit.node"})
# hook.pyright's TASK7_DISPOSITIONS/`config/check-preservation.json` entries
# are left untouched (append-only oracle) so `retained == RETAINED_IDS`
# still holds; it is superseded (no longer a live registry check) by
# `types.python` and must be subtracted everywhere RETAINED_IDS stands in
# for "the live registry's carried-over checks".
SUPERSEDED_IDS = frozenset({"hook.pyright"})

# `_expected_group()`'s prefix heuristic can't tell "types.python" (lint job)
# from "security.semgrep"/"dependency.audit.*" (security job) apart from
# their id text, so these four get an explicit override.
GROUP_OVERRIDES = {
    "types.python": "lint",
    "security.semgrep": "security",
    "dependency.audit.python": "security",
    "dependency.audit.node": "security",
}
