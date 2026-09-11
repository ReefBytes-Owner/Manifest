"""C7j / Correction 8 rule 3: group budget ceilings, single source, no
constants.

Each `test`/`lint`/`structure` group's summed `timeout_seconds` in
`config/project-checks.json` must fit inside its ci.yml producer job's OWN
`timeout-minutes` -- that job runs the group's underlying tools directly
(pytest, bats, ruff, ...), so its timeout is real evidence of how long the
group actually takes, not a number duplicated and left to drift. Previously
this was three constants (`1200`/`1800`/`900`) hand-copied from ci.yml at
write time; this test instead parses ci.yml itself, so a future ci.yml edit
that doesn't also update the registry's `timeout_seconds` budgets (or vice
versa) fails here immediately.

Split out of `test_check_profile_parity.py` (C-SIZE: that file was already
at 440/500 lines; this test plus its YAML-parsing helper would have pushed
it to 462, the next responsibility that belongs in a new module)."""

from __future__ import annotations

import yaml

from tests.python.manifest_agent._check_profile_oracle import ROOT, _raw_documents

#: Group -> the ci.yml job whose `timeout-minutes` is that group's ceiling.
#: These three groups each have exactly one producer job that runs their
#: checks' underlying tools directly; `shadow-checks-<group>` jobs run the
#: SAME checks again through the registry runner and are sized FROM the
#: group total instead (ci.yml's `shadow-checks-test` timeout-minutes =
#: ceil(sum(test group timeout_seconds)/60) + 5), not the other way around.
_GROUP_CEILING_JOB = {"lint": "lint", "test": "test", "structure": "validate"}


def _ci_workflow_job_timeout_minutes(job_name: str) -> int:
    document = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    return int(document["jobs"][job_name]["timeout-minutes"])


def test_timeouts_are_finite_and_fit_existing_group_ceilings():
    """Every check's `timeout_seconds` is positive and finite, and each of
    the three groups' summed budget fits inside its producer ci.yml job's
    own `timeout-minutes` -- read from ci.yml, never a constant duplicated
    here."""
    _, registry = _raw_documents()
    totals = dict.fromkeys(_GROUP_CEILING_JOB, 0.0)
    for check in registry["checks"]:
        timeout = float(check["timeout_seconds"])
        assert timeout > 0 and timeout != float("inf")
        if check["group"] in totals:
            totals[check["group"]] += timeout
    for group, job_name in _GROUP_CEILING_JOB.items():
        ceiling_seconds = _ci_workflow_job_timeout_minutes(job_name) * 60
        assert totals[group] <= ceiling_seconds, (
            f"{group} group timeout_seconds sum to {totals[group]}s, "
            f"over the {job_name!r} job's {ceiling_seconds}s ceiling"
        )
