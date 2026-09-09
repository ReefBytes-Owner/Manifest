# Task 10 — Fix Report: Finding 1 (Critical, FAIL/BLOCKED confusion) + Finding 2 (structure.inventory)

## Finding 1 — root cause

`src/manifest_agent/checks/runner.py`, `execute_check()`, previously (pre-fix)
lines 130–137:

```python
return CheckResult(
    check.id,
    "PASS" if result.returncode == 0 else "FAIL",
    ...
)
```

Any non-zero exit — including exit 3, this project's own BLOCKED signal for
repo-owned check bodies — was mapped straight to `FAIL`. Nothing in the
runner distinguished "the process ran and found a real problem" from "the
process could not verify anything." That is the honesty defect: seven of the
eight FAILs in the reproduction carried diagnostics that literally began
`BLOCKED:`, because `tools/project_checks/*.py` bodies faithfully return 3
and the runner discarded that signal.

## Mechanism chosen

Added an explicit, schema-validated per-check boolean,
`honors_status_contract` (default `false`), set only on checks whose `argv`
invokes `tools/project_checks/*.py`. The runner now maps exit codes as:

```python
def _executed_status(check: CheckSpec, result: ProcessResult) -> str:
    if result.returncode == 0:
        return "PASS"
    if check.honors_status_contract and result.returncode == 3:
        return "BLOCKED"
    return "FAIL"
```

(`src/manifest_agent/checks/runner.py:143-155`, wired into `execute_check` at
the return that used to inline the ternary.)

Why this and not argv pattern-matching at runtime or diagnostics-text
sniffing (both explicitly disallowed): a declared field is auditable at
review time — anyone reading the registry entry sees the claim and the
schema/semantic validator enforces it is truthful, rather than the runtime
silently trusting whatever string a compromised or buggy check body prints.
Third-party tool exit codes (pytest, bats, golangci-lint, etc.) keep their
existing meaning — a completed pytest run returning 3 is still FAIL, per the
standing Phase 1 rule — because their checks never set the flag and default
to `false`.

Wiring:
- `src/manifest_agent/checks/models.py`: `CheckSpec.honors_status_contract:
  bool = False`.
- `schemas/project-checks.schema.json`: added `"honors_status_contract": {
  "type": "boolean" }` to the `check` def (optional, not required).
- `src/manifest_agent/checks/registry.py`: new `REPO_OWNED_CHECK_ARGV` regex
  (`^tools/project_checks/[^/]+\.py$`) and `_validate_status_contract()`,
  called from `_validate_checks`, rejecting `honors_status_contract: true`
  unless some `argv` element matches that pattern. `_normalize()` threads the
  field onto `CheckSpec`. File is 490/500 lines — under the ceiling, so no
  extraction was needed (the task flagged `runner.py`, which landed at
  497/500).
- `config/project-checks.json`: set `honors_status_contract: true` on the 36
  checks whose `argv[1]` is `tools/project_checks/*.py` (verified via
  `raise SystemExit(main())` in each of `structure.py`, `generated.py`,
  `hooks.py`, `packages.py`, `tool_versions.py`, `ci_context_cli.py` — all
  deliberately implement the 0/2/3 contract). Diff is additive-only, one line
  per entry; `config/check-preservation.json` untouched.

## RED evidence

New test file `tests/python/manifest_agent/test_check_runner_status_contract.py`
(kept separate from `test_check_runner.py`, which is already at 680 lines
against the 500 ceiling — adding tests there would have made a pre-existing
violation worse for no reason). Before the fix:

```
FAILED test_repo_owned_body_exiting_three_is_recorded_blocked
  TypeError: CheckSpec.__init__() got an unexpected keyword argument 'honors_status_contract'
FAILED test_third_party_tool_exiting_three_is_still_recorded_fail
  AttributeError: 'CheckSpec' object has no attribute 'honors_status_contract'
2 failed, 30 deselected
```

Also added `test_check_registry_semantics.py::test_honors_status_contract_requires_repo_owned_check_body`
and `..._is_accepted_for_repo_owned_check_body`, confirmed RED against the
schema/semantics layer before the registry validator existed (same run showed
the added tests failing for lack of the validator; confirmed by running them
standalone before `_validate_status_contract` was wired in).

## GREEN evidence

```
tests/python/manifest_agent/test_check_runner_status_contract.py: 2 passed
tests/python/manifest_agent/test_check_registry_semantics.py -k honors_status: 2 passed
test_check_runner.py + test_check_cli.py + test_check_registry.py +
  test_check_registry_semantics.py + test_check_profile_parity.py +
  test_project_check_bodies.py + test_check_aggregate.py +
  test_check_runner_status_contract.py: 236 passed
```

## Before/after Counter (reproduction command)

Before (controller's run, `full-run.json`):
`profile FAIL; BLOCKED=29, NOT_APPLICABLE=16, PASS=12, FAIL=8` (65 results).
All 7 of the 8 FAILs listed in the task began `BLOCKED:` in diagnostics.

After (this branch, `full-run-after3.json`, clean run — no concurrent edits,
caches cleared): `profile BLOCKED; Counter({'BLOCKED': 39, 'PASS': 15,
'NOT_APPLICABLE': 11})` — **zero FAILs**. All 7 named checks are now
`BLOCKED` with their honest `BLOCKED: ...` diagnostics preserved verbatim
(markdownlint-cli2/bats/pre-commit-hooks/shfmt genuinely unprovisioned in
this offline sandbox — expected, no network/install used). `structure.inventory`
is `PASS` (finding 2, below). `generated.cursor` and
`hook.check-cursor-rules-drift` — formerly two of the disguised FAILs — now
resolve honestly too: in one clean run both were `PASS` (mirror now present,
`Cursor skill inputs unavailable` no longer fires); in another run
`generated.cursor` came back `BLOCKED` with `candidate identity changed`,
an unrelated pre-existing race in that check's own candidate-mutation
detection, not a FAIL/BLOCKED confusion. Noted as a concern below, not
fixed (out of scope — it's honest either way, never FAIL).

Profile status is `BLOCKED` rather than `FAIL` because every remaining
non-PASS result is a genuine tool-unavailability BLOCKED in this
no-network/no-install sandbox — consistent with "the profile status will
still be FAIL if any genuine FAIL remains ... or BLOCKED if not."

## Finding 2 — determination: candidate-preparation gap, confirmed

Evidence: `tools/project_checks/structure.py:_inventory` (`_inventory`,
line ~140) counts `SKILL.md` files under `configs/claude/skills` (a symlink
to `.apm/skills`, per this repo's own `CLAUDE.md`). `.apm/skills` is
**generated and gitignored** (`.gitignore:89`, confirmed via `git ls-files`
returning only 2 tracked paths under it — a `README.md`/`.metadata.json`
pair, not the skill tree). A disposable Git-worktree candidate built from
history therefore never materializes it, so `_inventory` counts zero and
prints `FAIL: expected exactly 122 skills, found 0` — a real symptom with a
fake cause. Its sibling check `structure.skill-paths`
(`tools/project_checks/structure.py:_skill_paths`) already treats the same
missing directory correctly, raising `BlockedError(".apm/skills is
unavailable")` — proof the check body itself agrees this is an
unavailable-input condition, not a structural defect, when written
honestly.

The design doc settles which fix is correct:
`docs/superpowers/specs/2026-09-08-shared-checks-ci-design.md:56-57`:
"Candidate preparation is separately declared in that registry for
deterministic, non-networked generated inputs such as the ignored
`.apm/skills` mirror." This names the exact fix — a declared
`candidate_preparation`, not a BLOCKED patch inside the check body.

Fix: added `prepare.skill-mirror` to `candidate_preparations` in
`config/project-checks.json`, running
`bash configs/claude/scripts/generate_skill_mirror.sh --root .` (a
deterministic, no-network, no-install script that copies
`plugins/*/skills/*` — already present in the candidate's git history — into
the gitignored `.apm/skills` mirror), declared for the `structure` and
`lint` groups (both consume the mirror: `structure.inventory`,
`structure.skill-paths`, `generated.cursor` are `structure`;
`hook.check-cursor-rules-drift` is `lint`). Its tool entry uses the
established `python-wrapper` no-argument idiom (`expected_version: "ok"`)
rather than pinning `bash`'s local version — pinning would have been
non-portable and was in fact the first version I tried, copied from the
pre-existing (and apparently untested-in-practice) `hook.validate-bootstrap`
tool entry, whose `expected_version: "command:bash=UNPINNED"` never matches
any real `bash --version` output. That existing entry is a latent bug I did
not touch (its check is `NOT_APPLICABLE` under `--base HEAD~1` here so it
has never actually been exercised) — flagged under Concerns.

Result: `structure.inventory` is `PASS` in the after-run (0 → 122, matching
`skill_policies.yml`'s `expected_total`). This is the "preparation gap"
branch, not a real inventory discrepancy — once the mirror exists, the count
matches exactly.

## ruff

```
uvx ruff@0.15.20 check <touched .py files>       -> All checks passed!
uvx ruff@0.15.20 format --check <touched files>  -> 5 files already formatted
  (one file needed `ruff format` once during authoring; reformatted, then
  re-verified clean and tests re-run green after the reformat)
```

## Whole-suite tail

```
3 failed, 3104 passed, 16 skipped in 383.91s
FAILED test_native_adapter_integration.py::test_local_native_cli_probe_reports_blocked_absence[cursor]
FAILED test_native_adapter_integration.py::test_local_native_cli_probe_reports_blocked_absence[devin]
FAILED test_state.py::test_state_module_imports_in_a_fresh_interpreter
```

All three match the documented environmental baseline exactly (cursor/devin
CLIs absent, fresh-interpreter import path). 3104 passed vs. the stated
baseline of 3100 — the +4 are this task's new tests (2 in
`test_check_runner_status_contract.py`, 2 in
`test_check_registry_semantics.py`). The known load-sensitive
`test_cancellation_reaps_process_family_and_emits_no_success_receipt` flake
did not appear. No `__pycache__`, `.pytest_cache`, or `pytest-of-*` left
under the repo root (checked and cleaned after each run).

## Concerns

1. `hook.validate-bootstrap`'s tool entry
   (`config/project-checks.json`) has `expected_version:
   "command:bash=UNPINNED"` against a `command-version` probe that returns
   real version strings (`command:bash=5.3.15` here) — this can never match.
   That check is `NOT_APPLICABLE` under the reproduction's base, so the bug
   is latent, not currently visible in this run's Counter. Not touched here
   (pre-existing, unrelated to either assigned finding); worth a follow-up
   ticket since it will silently `BLOCKED` (`tool version mismatch`,
   preflight-level) the moment that check does have applicable changed
   paths.
2. `generated.cursor`'s own candidate-mutation detection ("candidate identity
   changed") fired in one of the two clean after-runs but not the other,
   with identical inputs and identical registry — a pre-existing flake in
   that check body's interaction with the candidate identity/preparation
   machinery, unrelated to the FAIL/BLOCKED mapping fixed here (both outcomes
   it produces, PASS and BLOCKED, are honest; FAIL never recurred). Flagging
   for awareness, not fixing — out of scope for this task's two findings.
3. `test_check_runner.py` was already 680 lines against the file's 500-line
   constitution ceiling before this branch; I did not add to it (see RED
   evidence above) specifically to avoid compounding that pre-existing
   violation — the new tests live in a new, single-responsibility file
   instead.
