"""Identity-based debt ratchet (C3): the same-count-replacement proof, the
five verdict rules, and the propose-baseline containment guarantee.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from manifest_agent.checks import debt

TODAY = date(2026, 9, 9)


def _commit_date(_sha: str) -> date:
    return TODAY - timedelta(days=10)


def _entry(
    identity: str, *, expires: str | None = None, retired: str | None = None
) -> dict:
    return {
        "identity": identity,
        "check": "C-SIZE",
        "path": "a.py",
        "anchor": "f",
        "reason": "legacy debt, tracked",
        "owner": "@owner",
        "introduced_base": "base1",
        "expires": expires or (TODAY + timedelta(days=30)).isoformat(),
        "retired_base": retired,
    }


def _baseline(*entries: dict) -> debt.Baseline:
    return debt.Baseline(raw_entries=tuple(entries))


def _evaluate(
    cand, base, baseline_cand, baseline_base, *, from_base=False
) -> debt.DebtReport:
    inputs = debt.EvaluationInputs(
        findings_cand=cand,
        findings_base=base,
        baseline_cand=baseline_cand,
        baseline_base=baseline_base,
        today=TODAY,
        commit_date=_commit_date,
        baseline_from_base=from_base,
    )
    return debt.evaluate(inputs)


# --- identity scheme -------------------------------------------------------


def test_normalize_message_collapses_digits_and_whitespace_runs():
    a = debt.normalize_message("function has   72 lines (ceiling 500)")
    b = debt.normalize_message("function has 73 lines (ceiling 500)")
    assert a == b == "function has # lines (ceiling #)"


def test_normalize_message_preserves_distinct_wording():
    # Only digits/whitespace/quoted-paths collapse; distinct prose survives,
    # so two genuinely different findings do not collide on identity.
    a = debt.normalize_message("nests 5 deep (ceiling 4)")
    b = debt.normalize_message("takes 5 parameters (ceiling 4)")
    assert a != b


def test_normalize_message_collapses_path_shaped_quoted_strings():
    a = debt.normalize_message("already documented at `size.py:89,118`")
    b = debt.normalize_message("already documented at `size.py:200,310`")
    assert a == b  # both collapse to the same <path> placeholder


def test_normalize_message_preserves_distinct_quoted_identifiers():
    # Real constitution messages quote bare symbol names, not paths -- e.g.
    # `` `cfg` `` / `` `routes` `` from C-DATA. Collapsing ANY quoted string
    # (the pre-fix behaviour) reopens the same-count hole this ratchet
    # exists to close: two different literals at the same anchor would
    # collide on one identity, and a same-anchor swap would PASS.
    a = debt.normalize_message("literal `cfg` is a 23-line literal data table")
    b = debt.normalize_message("literal `routes` is a 23-line literal data table")
    assert a != b


def test_same_anchor_identifier_swap_is_caught_by_identity():
    """A same-anchor swap between two different quoted identifiers must be a
    different identity -- proof the path/identifier distinction in
    ``normalize_message`` does not reopen the same-count hole.
    """
    before = [
        debt.RawFinding(
            "C-DATA",
            "payloads.py",
            "f",
            "literal `cfg` is a 23-line literal table",
            104,
        )
    ]
    after = [
        debt.RawFinding(
            "C-DATA",
            "payloads.py",
            "f",
            "literal `routes` is a 23-line literal table",
            104,
        )
    ]
    ids_before = {f.identity for f in debt.assign_identities(before)}
    ids_after = {f.identity for f in debt.assign_identities(after)}
    assert ids_before != ids_after

    report = _evaluate(after, before, _baseline(), _baseline())
    assert report.status == "FAIL"
    assert report.fails[0].reason == "new debt"


def test_identity_excludes_line_number():
    id_a = debt.identity_of("C-SIZE", "a.py", "f", "72 lines", 0)
    id_b = debt.identity_of("C-SIZE", "a.py", "f", "72 lines", 0)
    assert id_a == id_b  # line is not a parameter at all


def test_identity_changes_with_ordinal():
    first = debt.identity_of("C-SIZE", "a.py", "", "too long", 0)
    second = debt.identity_of("C-SIZE", "a.py", "", "too long", 1)
    assert first != second


def test_ordinal_assignment_is_line_ordered_within_a_collision_group():
    findings = [
        debt.RawFinding("C-SIZE", "a.py", "", "too long", line=50),
        debt.RawFinding("C-SIZE", "a.py", "", "too long", line=10),
    ]
    identified = debt.assign_identities(findings)
    by_line = {f.line: f.identity for f in identified}
    assert by_line[10] == debt.identity_of("C-SIZE", "a.py", "", "too long", 0)
    assert by_line[50] == debt.identity_of("C-SIZE", "a.py", "", "too long", 1)


def test_same_count_replacement_is_caught_by_identity_but_invisible_to_a_count():
    """The headline case: fix one violation, introduce a different one in the
    same file -- the COUNT is unchanged, but the identities differ.
    """
    before = [debt.RawFinding("C-SIZE", "a.py", "old_fn", "function is 72 lines", 10)]
    after = [debt.RawFinding("C-SIZE", "a.py", "new_fn", "function is 90 lines", 10)]

    # The old count-based scheme: one (file, check) -> count.
    def count(findings):
        return len(findings)

    assert count(before) == count(after) == 1  # old scheme sees NO change

    ids_before = {f.identity for f in debt.assign_identities(before)}
    ids_after = {f.identity for f in debt.assign_identities(after)}
    assert ids_before != ids_after  # the ratchet sees a genuinely different finding

    # And the full ratchet actually FAILs it: `after` has no baseline entry
    # and is absent from the (pre-fix) base tree findings `before`.
    report = _evaluate(after, before, _baseline(), _baseline())
    assert report.status == "FAIL"
    assert report.fails[0].reason == "new debt"


# --- the five verdict rules -------------------------------------------------


def test_new_debt_fails():
    finding = debt.RawFinding("C-SIZE", "a.py", "f", "too long", 1)
    report = _evaluate([finding], [], _baseline(), _baseline())
    assert report.status == "FAIL"
    assert report.fails[0].reason == "new debt"


def test_baselined_debt_present_on_base_tree_passes():
    finding = debt.RawFinding("C-SIZE", "a.py", "f", "too long", 1)
    ident = debt.identity_of("C-SIZE", "a.py", "f", "too long", 0)
    report = _evaluate([finding], [finding], _baseline(_entry(ident)), _baseline())
    assert report.status == "PASS"


def test_expired_exception_fails():
    finding = debt.RawFinding("C-SIZE", "a.py", "f", "too long", 1)
    ident = debt.identity_of("C-SIZE", "a.py", "f", "too long", 0)
    expired = _entry(ident, expires=(TODAY - timedelta(days=1)).isoformat())
    report = _evaluate([finding], [finding], _baseline(expired), _baseline())
    assert report.status == "FAIL"
    assert report.fails[0].reason == "expired exception"


def test_restored_debt_fails_even_with_a_baseline_entry():
    finding = debt.RawFinding("C-SIZE", "a.py", "f", "too long", 1)
    ident = debt.identity_of("C-SIZE", "a.py", "f", "too long", 0)
    retired = _entry(ident, retired="base9")
    # absent from the base tree (it was fixed once) but back in the candidate
    report = _evaluate([finding], [], _baseline(retired), _baseline())
    assert report.status == "FAIL"
    assert report.fails[0].reason == "restored debt"


def test_stale_entry_fails_when_the_finding_is_gone():
    ident = debt.identity_of("C-SIZE", "a.py", "f", "too long", 0)
    entry = _entry(ident)  # not retired
    report = _evaluate([], [], _baseline(entry), _baseline())
    assert report.status == "FAIL"
    assert report.fails[0].reason == "stale entry: retire it"


def test_invalid_baseline_entry_blocks_missing_field():
    bad = _entry("id1")
    del bad["owner"]
    report = _evaluate([], [], _baseline(bad), _baseline())
    assert report.status == "BLOCKED"
    assert any("owner" in reason for reason in report.blocked_reasons)


def test_invalid_baseline_entry_blocks_expiry_over_180_days():
    ident = debt.identity_of("C-SIZE", "a.py", "f", "too long", 0)
    too_far = _entry(ident, expires=(TODAY + timedelta(days=400)).isoformat())
    report = _evaluate([], [], _baseline(too_far), _baseline())
    assert report.status == "BLOCKED"
    assert any("180 days" in reason for reason in report.blocked_reasons)


def test_invalid_baseline_entry_blocks_secret_shaped_reason():
    ident = debt.identity_of("C-SIZE", "a.py", "f", "too long", 0)
    entry = _entry(ident)
    entry["reason"] = "temporary, password=hunter2 rotates next quarter"
    report = _evaluate([], [], _baseline(entry), _baseline())
    assert report.status == "BLOCKED"
    assert any("secret-shaped" in reason for reason in report.blocked_reasons)


def test_baseline_from_base_ignores_candidate_only_exceptions():
    """The `release` profile rule: a candidate cannot ship its own exception."""
    finding = debt.RawFinding("C-SIZE", "a.py", "f", "too long", 1)
    ident = debt.identity_of("C-SIZE", "a.py", "f", "too long", 0)
    # Only the CANDIDATE baseline excuses it; the base baseline does not.
    report = _evaluate(
        [finding], [], _baseline(_entry(ident)), _baseline(), from_base=True
    )
    assert report.status == "FAIL"
    assert report.fails[0].reason == "new debt"


def test_release_profile_passes_when_candidate_fixes_and_retires_baselined_debt():
    """Controller ruling: under ``baseline_from_base``, staleness reads
    ``B_cand`` (never ``B_base``). Retiring a fixed entry is the ratchet
    working as designed, not a candidate-granted exception -- FAILing it
    would block every debt-reduction PR until a second PR lands the
    retirement, inverting the chunk's purpose.
    """
    ident = debt.identity_of("C-SIZE", "a.py", "f", "too long", 0)
    cand_retired = _entry(ident, retired="fix-sha")  # candidate fixed + retired it
    base_unretired = _entry(ident)  # base tree's copy has not caught up yet
    report = _evaluate(
        [],  # finding is gone from the candidate
        [],  # and from the base tree's findings
        _baseline(cand_retired),
        _baseline(base_unretired),
        from_base=True,
    )
    assert report.status == "PASS"


def test_proposed_exceptions_are_reported_but_never_gate():
    ident = debt.identity_of("C-SIZE", "a.py", "f", "too long", 0)
    finding = debt.RawFinding("C-SIZE", "a.py", "f", "too long", 1)
    report = _evaluate([finding], [finding], _baseline(_entry(ident)), _baseline())
    assert report.status == "PASS"
    assert [e.identity for e in report.proposed_exceptions] == [ident]


# --- propose-baseline: containment and content ------------------------------


def test_propose_baseline_refuses_output_inside_source_tree(tmp_path: Path):
    payload = {"version": debt.SCHEMA_VERSION, "entries": []}
    inside = tmp_path / "config" / "debt-baseline.json"
    with pytest.raises(ValueError, match="outside the source tree"):
        debt.write_proposal(payload, inside, tmp_path)


def test_propose_baseline_refuses_output_equal_to_repo_root(tmp_path: Path):
    payload = {"version": debt.SCHEMA_VERSION, "entries": []}
    with pytest.raises(ValueError, match="outside the source tree"):
        debt.write_proposal(payload, tmp_path, tmp_path)


def test_propose_baseline_writes_outside_the_tree(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "proposals" / "debt-baseline.json"
    payload = {"version": debt.SCHEMA_VERSION, "entries": [{"identity": "x"}]}
    debt.write_proposal(payload, outside, repo_root)
    assert outside.is_file()
    assert not (repo_root / "debt-baseline.json").exists()


def test_propose_baseline_never_edits_config_debt_baseline_in_place(tmp_path: Path):
    """`config/debt-baseline.json` inside the repo must be untouched by a proposal."""
    repo_root = tmp_path / "repo"
    (repo_root / "config").mkdir(parents=True)
    committed = repo_root / "config" / "debt-baseline.json"
    committed.write_text('{"version": 2, "entries": []}\n')
    before = committed.read_text()

    finding = debt.RawFinding("C-SIZE", "a.py", "f", "too long", 1)
    inputs = debt.ProposalInputs(
        findings_cand=[finding],
        findings_base=[],
        existing_cand=debt.Baseline.load(committed),
        repo_root=repo_root,
        base_sha="base1",
        today=TODAY,
        owner="@owner",
        reason="new debt found",
        commit_date=_commit_date,
    )
    proposal = debt.propose_baseline(inputs)
    outside = tmp_path / "review" / "debt-baseline.json"
    debt.write_proposal(proposal, outside, repo_root)

    assert committed.read_text() == before  # untouched
    assert outside.is_file()
    assert len(proposal["entries"]) == 1


def test_propose_baseline_auto_retires_entries_absent_from_base():
    ident = debt.identity_of("C-SIZE", "a.py", "f", "too long", 0)
    existing = debt.Baseline(raw_entries=(_entry(ident),))
    inputs = debt.ProposalInputs(
        findings_cand=[],  # fixed in the candidate too
        findings_base=[],  # and absent from the base tree
        existing_cand=existing,
        repo_root=Path("/does/not/matter"),
        base_sha="base2",
        today=TODAY,
        owner="@owner",
        reason="n/a",
        commit_date=_commit_date,
    )
    proposal = debt.propose_baseline(inputs)
    assert proposal["entries"][0]["retired_base"] == "base2"
