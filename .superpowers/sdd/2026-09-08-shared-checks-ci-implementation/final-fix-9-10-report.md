# Final fix wave — Tasks 9-10 shared-check review (one pass, no second round)

Base HEAD for this wave: `e3a52ffb` (branch `wip/enforcement-first-shared-checks`).
One Critical + four Important findings addressed below, plus two Minors.
Committed as a single commit after this report.

## Finding 1 (Critical) — shadow CI path could not run at all

**File:lines**: `.github/workflows/ci.yml` — the three `shadow-checks-*` producer
jobs' "Run shared checks" steps (originally lines 607, 641, 675, before this
wave's edits shifted line numbers).

**What changed**: each producer job (`shadow-checks-structure`,
`shadow-checks-lint`, `shadow-checks-test`) gained a new "Resolve base
revision" step (`id: base`) that computes a base SHA from `env:`-supplied
GitHub context (`EVENT_NAME`, `PR_BASE_SHA`, `DEFAULT_BRANCH` — never spliced
into the script body) and writes it to `GITHUB_OUTPUT`:

```bash
if [ "${EVENT_NAME}" = "pull_request" ]; then
  base_sha="${PR_BASE_SHA}"
else
  base_sha="$(git merge-base HEAD "origin/${DEFAULT_BRANCH}")"
fi
echo "sha=${base_sha}" >> "${GITHUB_OUTPUT}"
```

The "Run shared checks" step then reads that output via `env: BASE_SHA:`
and the invocation becomes, for each of the three groups:

```
uv run manifest check full --group structure --project-config config/project-checks.json --base "${BASE_SHA}" --json --output shadow-structure.json
uv run manifest check full --group lint      --project-config config/project-checks.json --base "${BASE_SHA}" --json --output shadow-lint.json
uv run manifest check full --group test      --project-config config/project-checks.json --base "${BASE_SHA}" --json --output shadow-test.json
```

The `shadow-checks-test` job's checkout also gained `fetch-depth: 0` (it
previously only set `submodules: true`), since `git merge-base` needs full
history and that job's checkout was still shallow.

**Why this approach**: `--project-config` is `required=True` and `--base` is
enforced by `raise click.UsageError(...)` unless `--list` is used
(`src/manifest_agent/checks/cli.py:326-342`), so both are genuinely mandatory
for a real (non-`--list`) run. `pull_request` and `push` need different base
resolution (PR base SHA is directly available in the event payload; `push`
has no such field, so a merge-base against the default branch is the
closest non-`${{ }}`-spliced equivalent) — both branches are handled, matching
the "do not hardcode one event type" requirement. All values flow through
`env:`, never into the script body via `${{ }}`, per the workflow's no-splice
rule.

**Test-strengthening** (`tests/python/manifest_agent/test_shared_check_workflow.py`):
1. Extended `test_invokes_shared_command_exactly_once` with two new regex
   assertions requiring `--project-config <non-empty>` and `--base
   <non-empty>` to be present in the extracted command text.
2. Added a new test, `test_command_parses_with_the_real_cli`, that extracts
   the job's `run:` command, splits it into argv, substitutes a syntactically
   valid placeholder (`HEAD`) for the runtime-only `"${BASE_SHA}"` token, and
   feeds it to the **real** Click command's `make_context("check", argv,
   resilient_parsing=False)`. This parses (but never executes/invokes) the
   actual CLI, so it fails on a genuine missing/invalid required option
   (`click.UsageError`) without running any check body, materializing a
   candidate, or writing files.

**RED evidence** (observed directly, not claimed from memory) — running the
new parse-check against **today's original command** (before this wave's fix):

```
$ python3 -c "
import sys; sys.path.insert(0, 'src')
import click
from manifest_agent.checks.cli import check as check_command
argv = ['full', '--group', 'structure', '--json', '--output', 'shadow-structure.json']
try:
    check_command.make_context('check', argv, resilient_parsing=False)
    print('PARSED OK (unexpected)')
except click.UsageError as e:
    print('UsageError (expected for old command):', e.format_message())
"
UsageError (expected for old command): Missing option '--project-config'.
```

This reproduces the same failure mode the finding describes
(`Error: Missing option '--project-config'`), confirming the new test would
have failed against the pre-fix workflow.

**GREEN evidence**: after the fix,
`tests/python/manifest_agent/test_shared_check_workflow.py` — 34 passed in
0.43s (this file alone); full targeted list below all green.

## Finding 2 (Important) — aggregate verdict effectively unobservable

**File:lines**: `.github/workflows/ci.yml`, `shadow-checks-aggregate` job,
"Aggregate shadow checks (informational only)" step.

**What changed**:
1. The aggregate step now writes `--output shadow-aggregate.json` in addition
   to `--json` (the `check-aggregate` CLI already supports `--output`,
   `src/manifest_agent/checks/cli.py:392`).
2. A new "Upload shadow aggregate receipt (current attempt)" step uploads
   that file as `shadow-receipt-aggregate-${{ github.run_attempt }}`
   (matching the producers' run-attempt-scoped naming convention).
3. A new "Publish aggregate verdict to job summary" step (`if: always()`)
   reads the `status` field out of `shadow-aggregate.json` with a small
   inline `python3 -c` and appends it to `$GITHUB_STEP_SUMMARY`, falling back
   to an explicit `UNKNOWN (no aggregate receipt was written)` line if the
   file is missing/empty rather than silently producing no summary at all.

**Why this approach**: `$GITHUB_STEP_SUMMARY` is visible on the run's summary
page without opening step logs, and the artifact gives a durable receipt
consistent with how the three producers already publish evidence.

## Finding 3 (Important) — producer-rejection guard was inert

**File:lines**: `.github/workflows/ci.yml`, `shadow-checks-aggregate` job,
"Reject unsuccessful shadow producers" step; each producer job's `outputs:`
block.

**What changed**: each producer job's `outputs:` now also exports
`shadow_outcome: ${{ steps.shadow.outcome }}` (the check step's own
`id: shadow`). The aggregate job's rejection step now reads
`needs.<job>.outputs.shadow_outcome` instead of `needs.<job>.result`.

**Why this approach**: job-level `continue-on-error: true` on the three
producers causes GitHub to report `result: "success"` in the `needs` context
of a downstream job even when the job's steps actually failed — only the
per-step `outcome` (`steps.<id>.outcome`) is unaffected by job-level
`continue-on-error`. Exporting that per-step outcome as a job output is the
one channel that survives the job-level swallow. The existing `!= "success"`
allow-list check and the "must not be step-level continue-on-error" wiring
were left untouched; only the input source changed.

**Docs correction**: `docs/SHARED_CHECKS.md` — the "shadow CI path" section
previously said the aggregate "rejects any producer whose *result* is not
exactly `success`"; corrected to describe the `steps.shadow.outcome` job
output and explain why `needs.<job>.result` cannot work here (added a new
paragraph explaining the job-level `continue-on-error` interaction).

## Finding 4 (Important) — Task 10 report stale relative to head

**File:lines**: `.superpowers/sdd/2026-09-08-shared-checks-ci-implementation/task-10-report.md`,
new "Addendum (final fix wave, head `e3a52ffb`)" section appended at the end
of the file (after the existing "Findings"/observations section, before
nothing — it is now the last section).

**What changed**: added an addendum, **not a rewrite** of the original
per-profile table or "FAIL rather than BLOCKED" observations (both are left
intact and explicitly marked as accurate for the commit they describe,
`6eae412c`). The addendum:
- States the original table/observations were true for `6eae412c` and are
  superseded, not wrong.
- Points to `task-10-fix-report.md` (already present in the same directory)
  for the root cause and mechanism of the fix.
- Quotes the fix report's own verified Counter: `profile BLOCKED; Counter({
  'BLOCKED': 39, 'PASS': 15, 'NOT_APPLICABLE': 11})` — zero `FAIL`,
  controller-verified — matching the `full` profile run performed
  independently in this wave (see "Post-fix profile verification" below,
  identical Counter).

## Finding 5 (Important) — undeclared exit codes from contract-honoring bodies

**File:lines**: `src/manifest_agent/checks/runner.py:140-153` (original,
pre-extraction) — `_executed_status`, called from `execute_check`.

**What changed**: `_executed_status` now returns `(status, diagnostic_note)`
instead of just `status`. A `honors_status_contract` body exiting with
anything outside `{0, 2, 3}` (e.g. 1 from an uncaught traceback) now maps to
`BLOCKED` with a diagnostic note naming the observed exit code, instead of
falling through to `FAIL`. Exit 3 still maps to `BLOCKED` (pre-existing
behavior), exit 2 (and any non-honoring body's exit) still maps to `FAIL`
(pre-existing behavior) — only the previously-unhandled "other" exit codes
from contract-honoring bodies changed.

**File extraction (line-ceiling)**: adding this logic would have pushed
`runner.py` from 497 to ~519 lines, over the 500-line Constitution ceiling.
Per the task's explicit instruction ("extract rather than suppress — no
`constitution: exempt` markers"), the status-mapping concern was extracted
into a new module, `src/manifest_agent/checks/status.py` (71 lines):
`diagnostics`, `bounded_text`, `blocked`, and `executed_status` (renamed from
their private `_`-prefixed forms; `runner.py` imports them back under their
original private names via `from .status import X as _x` so every existing
call site in `runner.py` is unchanged). `runner.py` is now 464 lines,
`registry.py` is 497 lines (see Minor below) — both under the 500-line
ceiling with no exempt markers.

**Test added** (`tests/python/manifest_agent/test_check_runner_status_contract.py`):
`test_contract_honoring_body_exiting_outside_0_2_3_is_blocked_not_fail` — adds
an `exit-one.py` fixture script (`raise SystemExit(1)`), runs it through
`execute_check` with `honors_status_contract=True`, and asserts
`status == "BLOCKED"`, `returncode == 1`, and that the diagnostics contain
both `"contract violation"` and the observed code `"1"`.

**RED evidence (logical, not re-run against old code)**: the pre-fix
`_executed_status` was:
```python
if result.returncode == 0: return "PASS"
if check.honors_status_contract and result.returncode == 3: return "BLOCKED"
return "FAIL"
```
For `honors_status_contract=True, returncode=1`: first condition false,
second false (`1 != 3`), falls through to `"FAIL"` — the exact defect the
finding describes. This is a direct trace of the removed code, not an
assumption; it was not independently re-executed against a reverted copy
given the single-fix-wave, one-commit constraint, but the code path is
unambiguous and the new test's assertions (`BLOCKED`, `returncode == 1`,
diagnostic containing `"contract violation"`) could not pass against that
removed logic.

**GREEN evidence**: `test_check_runner_status_contract.py` — 3 passed in
2.92s (includes the two pre-existing tests, still green).

## Minors

- **`registry.py:82-93` (`honors_status_contract` argv validator)** — fixed.
  Previously matched `tools/project_checks/*.py` anywhere in `argv`, so
  `["markdownlint-cli2", "tools/project_checks/x.py"]` would have
  incorrectly qualified. Now matches only `argv[0]` (direct execution) or
  `argv[0:2]` as an interpreter (`python`/`python3`/`python3.NN`, matched by
  basename) + script pair. Kept intentionally terse (comment, not a
  docstring) specifically to stay under the 500-line ceiling without
  suppressing the fix — see Finding 5's extraction note; `registry.py` is
  497 lines after this change.
- **`runner.py` at 497/500 lines** — addressed via extraction, not
  suppression; see Finding 5.
- **`.github/workflows/ci.yml:625`** (the `shadow-checks-structure` run line)
  is 157 characters, 7 over the repo's `.yamllint` warning threshold (150,
  `level: warning`). `.github/workflows/ci.yml` is not currently in the set
  of files the repo's own `yamllint` CI step lints (that step only targets
  `configs/claude/config/*.yml`), so this is a non-blocking cosmetic note,
  left as-is rather than reformatted into a backslash-continued multi-line
  command (which would have broken the new argv-parsing test's naive
  whitespace `.split()`).

## Constraints verified

- Status contract preserved: 0 PASS / 2 FAIL / 3 BLOCKED; `full` profile
  returns `BLOCKED` (not FAIL) when only BLOCKED+PASS+NOT_APPLICABLE are
  present, and the pre-existing "FAIL wins over BLOCKED when both occur"
  rule in `aggregate.py`/`cli.py` (`STATUS_EXITS`) was not touched.
- No `${{ }}` spliced into any workflow script body; all new dynamic values
  (`EVENT_NAME`, `PR_BASE_SHA`, `DEFAULT_BRANCH`, `BASE_SHA`,
  `RESULT_STRUCTURE`/`RESULT_LINT`/`RESULT_TEST`) flow through `env:`.
- No `config/check-preservation.json` change.
- No legacy job (`lint`, `test`, `validate`) touched; verified by
  `test_legacy_required_jobs_untouched_by_name` and
  `test_shadow_job_not_gating` (both pre-existing, still passing).
- No promotion to required status; branch protection untouched.
- Ordinary profile execution performed no installs/network/`~/` writes: the
  post-fix `manifest check full` verification below ran with `uv run
  --project . --with jsonschema --with click --with pyyaml`, no separate
  install step, output written to `/private/tmp` (not `/tmp`, which the
  output guard correctly rejects as a symlink).

## Test results

### Targeted list (as specified)

```
tests/python/manifest_agent/test_shared_check_workflow.py
tests/python/manifest_agent/test_shared_check_recursion.py
tests/python/manifest_agent/test_ci_context_cli.py
tests/python/manifest_agent/test_check_runner.py
tests/python/manifest_agent/test_check_runner_status_contract.py
tests/python/manifest_agent/test_check_registry.py
tests/python/manifest_agent/test_check_registry_semantics.py
tests/python/manifest_agent/test_check_cli.py
tests/python/manifest_agent/test_check_aggregate.py
```
Result: **212 passed** (0 failed, 0 skipped) in 141.35s.

### Whole suite

```
uv run --project configs/claude --with-requirements tests/requirements-ci.txt \
  --with jsonschema --with click --with pyyaml python -B -m pytest -q -p no:cacheprovider
```
Result tail:
```
FAILED tests/python/manifest_agent/test_check_process.py::test_cancellation_reaps_process_family_and_emits_no_success_receipt
FAILED tests/python/manifest_agent/test_native_adapter_integration.py::test_local_native_cli_probe_reports_blocked_absence[cursor]
FAILED tests/python/manifest_agent/test_native_adapter_integration.py::test_local_native_cli_probe_reports_blocked_absence[devin]
FAILED tests/python/manifest_agent/test_state.py::test_state_module_imports_in_a_fresh_interpreter
4 failed, 3107 passed, 16 skipped in 401.32s (0:06:41)
```
All 4 failures are on the pre-approved list: the 3 environmental failures
named in the task baseline (`test_native_adapter_integration[cursor]`,
`[devin]`, `test_state.py::test_state_module_imports_in_a_fresh_interpreter`)
plus the known load-sensitive `test_cancellation_reaps_process_family_and_emits_no_success_receipt`
flake, which the task instructions say to note rather than chase. Passed
count (3107) is higher than the stated 3104 baseline because this wave adds
4 new test functions (3 parametrized `test_command_parses_with_the_real_cli`
cases + 1 `test_contract_honoring_body_exiting_outside_0_2_3_is_blocked_not_fail`).
**No failure outside the approved set occurred.**

Note on process: the first two attempts at both the whole-suite run and the
`manifest check full` verification below were piped through `| tail -N`,
which silently buffers and reports a misleading `[exited with code 0]` when
the upstream process is killed mid-run (the reported exit code is `tail`'s,
not the real command's) — this was caught before finalizing (the
`manifest check full` run had produced no output file at all despite
"EXIT:0"), and both commands were re-run with output redirected directly to
a file so real progress and real completion could be verified.

### Post-fix `manifest check full` verification

```
uv run --project . --with jsonschema --with click --with pyyaml \
  manifest check full --project-config config/project-checks.json \
  --base HEAD~1 --json --output /private/tmp/postfix.json
```
Result: `status: BLOCKED`, `profile: full`, zero FAIL.
```
counter: {'PASS': 15, 'BLOCKED': 39, 'NOT_APPLICABLE': 11}
FAIL ids: []
```
Matches the Counter cited in `task-10-fix-report.md` and reproduced in the
Finding 4 addendum above.

### Lint

```
$ uvx ruff@0.15.20 check src/manifest_agent/checks/registry.py src/manifest_agent/checks/runner.py \
    src/manifest_agent/checks/status.py tests/python/manifest_agent/test_check_runner_status_contract.py \
    tests/python/manifest_agent/test_shared_check_workflow.py
All checks passed!

$ uvx ruff@0.15.20 format --check <same files>
5 files already formatted

$ yamllint .github/workflows/ci.yml
.github/workflows/ci.yml
  625:151   warning  line too long (157 > 150 characters)  (line-length)
```
(Only a warning, on a file the repo's own yamllint CI step does not target;
see Minors.)

### Repo hygiene

`__pycache__` directories under the repo root (outside `.venv`/
`configs/claude/.venv`) were removed after the test runs; `.pytest_cache` and
`pytest-of-*` directories were searched for and none remained outside the
venvs. `git status --short` at commit time shows only the intended file
changes plus the new `status.py` module.

## Files changed

- `.github/workflows/ci.yml`
- `docs/SHARED_CHECKS.md`
- `src/manifest_agent/checks/registry.py`
- `src/manifest_agent/checks/runner.py`
- `src/manifest_agent/checks/status.py` (new)
- `tests/python/manifest_agent/test_check_runner_status_contract.py`
- `tests/python/manifest_agent/test_shared_check_workflow.py`
- `.superpowers/sdd/2026-09-08-shared-checks-ci-implementation/task-10-report.md`
- `.superpowers/sdd/2026-09-08-shared-checks-ci-implementation/final-fix-9-10-report.md` (this file)
