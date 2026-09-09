# Task 10 — Verify the complete candidate and hand off evidence

## Executive disposition: INCOMPLETE / BLOCKED for authoritative Phase 2 migration

Every explicit-registry invocation of `manifest check` (quick/full/security/release) against
the candidate at HEAD `6eae412c` ends **BLOCKED or FAIL**, driven entirely by checks the
registry itself annotates as `coverage_pending: ... provisioning pending Phase 3` (44–99
pending items per profile) plus environment-only gaps (missing pinned tool binaries, no
network/install authorization). No legacy CI job was touched, removed, or replaced; the shadow
path added in Task 9 remains `continue-on-error` and non-required. This matches the plan's
explicit boundary (brief lines 35–37): Phase 3 has not happened, so Phase 2 migration is not
complete and this report does not claim it is. What follows is the evidence a later completion
claim will need.

## Tested identity

- Repo: `/private/tmp/manifest-enforcement-phase1.aQmOnk/worktree`
- Branch: `wip/enforcement-first-shared-checks`, HEAD `6eae412c16a71f51aac5235e6fe66940218bfd2d`
  (`fix(checks): fix round 1 — aggregate self-match, job-level shadow safety, drift, dynamic
  recursion proof`)
- `git status --short`: clean at start and end (no uncommitted changes produced by this task
  besides this report)
- Branch commit range under review: `7741d4aa..6eae412c` (9 commits, oldest to newest:
  `f4269588 5cef332f 5cbec319 864a96b9 b4d2eae8 77e041ce bc35cf75 53ec353c 6eae412c`)
- `config/project-checks.json` sha256: `1709c515b02db89177f2d4e17acdb01ddb3f2823f7b5f036564e80cb103bbf88`
- `config/check-preservation.json` sha256: `ff9ed02dbb4efb57bfd47cacb0ebbdc76e92d4ef1de099794e210ef29244d17d`
- Candidate digest observed on every profile invocation at `--base HEAD`:
  `c6cf9cada53438e9d466c7c0e0afc84261a38a17575cae20c1748322da85112c` (stable across all 4 profile
  runs — corroborates deterministic candidate materialization)
- Config digest observed on every profile invocation: `490c40b4390d84440c81e27b35e73e39811f2e2fba148292798dc993f9f628dc`
- Tool versions observed: Python 3.14.3 (host `python3`), Python 3.11.15 (uv-managed pytest
  interpreter, per pytest banner), uv 0.11.14 (Homebrew), Bats 1.13.0 (`/usr/local/bin/bats`)

## Evidence — test suites

### Targeted new/changed test modules (registry/candidate/process/CLI/check-body/parity/aggregate + Phase 1 contract + regression watch)

Command:
```
uv run --project configs/claude --with-requirements tests/requirements-ci.txt \
  --with jsonschema --with click --with pyyaml python -B -m pytest -q -p no:cacheprovider \
  tests/python/manifest_agent/test_check_registry.py \
  tests/python/manifest_agent/test_check_registry_evaluators.py \
  tests/python/manifest_agent/test_check_registry_semantics.py \
  tests/python/manifest_agent/test_check_candidate.py \
  tests/python/manifest_agent/test_check_process.py \
  tests/python/manifest_agent/test_check_cli.py \
  tests/python/manifest_agent/test_project_check_bodies.py \
  tests/python/manifest_agent/test_check_profile_parity.py \
  tests/python/manifest_agent/test_plugin_parity_live_workflow.py \
  tests/python/manifest_agent/test_check_aggregate.py \
  tests/python/manifest_agent/test_check_aggregate_cli.py \
  tests/python/manifest_agent/test_check_aggregate_receipt_contract.py \
  tests/python/manifest_agent/test_ci_context.py \
  tests/python/manifest_agent/test_ci_context_cli.py \
  tests/python/manifest_agent/test_cli.py \
  tests/python/test_pr_regression_contract.py \
  tests/python/test_plugin_runtime_paths.py
```
Result: **346 passed** in 44.91s.

Second batch (modules present in the diff but missed on the first pass; caught during the
independent `git diff --stat` review, see Findings below):
```
uv run --project configs/claude --with-requirements tests/requirements-ci.txt \
  --with jsonschema --with click --with pyyaml python -B -m pytest -q -p no:cacheprovider \
  tests/python/manifest_agent/test_check_path_filters.py \
  tests/python/manifest_agent/test_check_preparation.py \
  tests/python/manifest_agent/test_check_preservation.py \
  tests/python/manifest_agent/test_check_runner.py \
  tests/python/manifest_agent/test_shared_check_recursion.py \
  tests/python/manifest_agent/test_shared_check_workflow.py \
  tests/python/manifest_agent/test_tool_version_security.py \
  tests/python/manifest_agent/test_tool_versions.py
```
Result: **186 passed** in 131.69s.

Combined targeted total: **532 passed, 0 failed**.

### Full suite

Command:
```
uv run --project configs/claude --with-requirements tests/requirements-ci.txt \
  --with jsonschema --with click --with pyyaml python -B -m pytest -q -p no:cacheprovider tests/
```
Result: **3100 passed, 3 failed, 16 skipped** in 361.75s.

Failures (all reproduce independently of this branch's changes, per environment, not logic):
- `test_native_adapter_integration.py::test_local_native_cli_probe_reports_blocked_absence[cursor]`
  — cursor CLI not present on this host
- `test_native_adapter_integration.py::test_local_native_cli_probe_reports_blocked_absence[devin]`
  — devin CLI not present on this host
- `test_state.py::test_state_module_imports_in_a_fresh_interpreter` — `ModuleNotFoundError:
  manifest_agent` when re-invoking `sys.executable` outside the project's installed
  environment; environmental, not branch-introduced

These are exactly the three known environmental failures called out in the task brief and the
SDD ledger's prior "3061 passed" baseline note (Tasks 1–8) and the "3100 passed / 3 failed"
controller run recorded for Task 9 at the same HEAD. The load-sensitive flake
(`test_check_process.py::test_cancellation_reaps_process_family_and_emits_no_success_receipt`)
did **not** recur in this run either — consistent with the ledger's acceptance of it as a
known, non-blocking, load-sensitive flake rather than a real defect.

No `__pycache__`, `.pytest_cache`, or `pytest-of-*` artifacts were left under the repo root by
either run (`-B -p no:cacheprovider` honored throughout); `git status --short` confirmed clean
after both runs.

### Bats suite

Command: `bats tests/bats/` (`/usr/local/bin/bats`, version **1.13.0**).

**Caveat**: CI pins Bats **1.11.1**; this host has **1.13.0**. The two-minor-version gap is
recorded per the task brief's explicit instruction; no attempt was made to install/pin a
matching version (no install authorization). Behavioral divergence between 1.11.1 and 1.13.0
was not separately audited — if Phase 3 or later work needs Bats-version parity as a fact, that
still needs to be pinned down.

**Result: PARTIAL / UNVERIFIED — capped, not completed.** The full-suite `bats tests/bats/` run
was started at ~15:23 PDT and had not produced a single result line after 13+ minutes of wall
time (only the `Bats 1.13.0` version banner had flushed; `bats`'s default pretty formatter
buffers all per-test output until the run finishes, so no partial pass/fail counts are
recoverable from stdout). Process inspection (`ps -o pid,etime,command`) at the 11m31s mark
showed the run still inside `tests/bats/deploy_runtime_state_e2e.bats` (a heavy e2e suite,
alphabetically roughly a third of the way through the ~140-file `tests/bats/` directory). The
run was deliberately capped and killed rather than left open-ended, per explicit instruction not
to let an unfinished bats run block this handoff. **No bats pass/fail counts are reported here**
— this is an honest gap, not a claimed result.

Even had it completed, the result would only have been advisory: CI pins Bats **1.11.1**
(per the task brief) while this host runs **1.13.0** — a two-minor-version gap not otherwise
audited for behavioral parity — so a local full-suite bats result was never going to be
authoritative for the CI-pinned version regardless of whether it finished in this session.

This report's disposition does not depend on the bats result: the executive BLOCKED/INCOMPLETE
verdict rests entirely on the `manifest check` profile runs and the `coverage_pending`
accounting, which are already conclusive and independent of the bats suite.

## Evidence — negative cases (fail for the intended reason, not incidentally)

All five ran green as *tests* (verifying the underlying behavior asserts the specific stated
reason, not just a status code):

1. **Forged/mismatched receipt** —
   `test_check_aggregate.py::test_wrong_head_sha_is_rejected` and the stricter
   `test_receipt_head_sha_must_match_tested_sha_not_just_be_consistent`: two receipts that agree
   with each other on a wrong SHA are still rejected against the run's `tested_sha` from
   `context`, not merely checked for mutual consistency. Assertion:
   `report["status"] == "BLOCKED"` and diagnostic text contains
   `"does not match the current run's tested_sha"` (counted exactly twice, once per receipt).
2. **NOT_APPLICABLE on a whole-project check** —
   `test_check_aggregate.py::test_not_applicable_on_project_selection_check_is_blocked`: a
   `project`-selection check (`test.a`, no path filters, can never legitimately have zero
   applicable inputs) reporting `NOT_APPLICABLE` forces `report["status"] == "BLOCKED"` with a
   diagnostic naming the check ID and `NOT_APPLICABLE` explicitly — contrasted directly against
   the sibling `test_not_applicable_on_changed_selection_check_aggregates_cleanly`, which proves
   the same status is legitimate (and does not block) on a `changed`-selection check.
3. **Timeout → BLOCKED** — `test_check_process.py::test_timeout_kills_parent_and_sleeping_child`:
   confirms the whole process group (parent + a deliberately sleeping child) is reaped on
   deadline and the result is `BLOCKED`, not a partial pass or a hang.
4. **Undeclared shell command** —
   `test_check_registry_evaluators.py::test_posix_shell_short_option_clusters_are_rejected`
   (argv-based POSIX-shell `-c`-style eval-flag detection rejects a clustered short option
   hiding `-c`) plus `test_check_process.py::test_child_receives_only_explicit_environment_and_literal_argv`
   (the executed child receives a literal argv list and an explicit environment only — no shell
   string is ever interpolated or evaluated).

All 7 parametrized/plain cases passed (`7 passed in 0.29s`), each asserting the specific
diagnostic text or status transition described above rather than only an exit code.

## Per-profile status (candidate registry, `--base HEAD`, real execution — not `--list`)

Every profile run reused the same candidate/config digests shown above (materialization is
deterministic across repeated invocations of the same HEAD).

| Profile | Status | Results breakdown | Coverage pending | Required IDs |
|---|---|---|---|---|
| quick | **BLOCKED** | PASS 2, NOT_APPLICABLE 29 | 44 | 31 |
| full | **FAIL** | PASS 12, NOT_APPLICABLE 31, BLOCKED 18, FAIL 4 | 88 | 65 |
| security | **BLOCKED** | BLOCKED 2, NOT_APPLICABLE 2 | 7 | 4 |
| release | **FAIL** | PASS 12, NOT_APPLICABLE 33, BLOCKED 21, FAIL 5 | 99 | 71 |

FAIL takes precedence over BLOCKED in the aggregate status when both occur, per the design's
"Preserve 0 PASS, 2 FAIL, 3 BLOCKED; when both occur, return 2 and retain both" rule — so `full`
and `release` show as FAIL rather than BLOCKED even though most of their non-PASS results are
BLOCKED, not FAIL.

### Named reasons for every non-PASS/non-NOT_APPLICABLE result (`full` profile; `security`/`release` deltas below)

- `structure.inventory` — **FAIL**: `expected exactly 122 skills, found 0`. This is a
  **candidate-provisioning gap, not a registry/logic defect**: `structure.inventory` is itself
  listed in `coverage_pending` ("deterministic candidate prerequisite provisioning pending Phase
  3 for full") because the materialized candidate does not yet provision the `.apm/skills`
  mirror the `configs/claude/skills` symlink resolves to. Confirmed by re-checking the same
  generator directly against the real (non-materialized) worktree — see Findings below.
- `syntax.yaml.config`, `lint.shell.scripts`, `lint.shell.bootstrap`, `lint.yaml.config`,
  `test.bats`, `test.python`, `test.hooks`, `hook.pyright`, `dependency.lock.config`,
  `dependency.lock.delegate`, `package.coordinator`, `package.config` — **BLOCKED**: `tool
  version mismatch` / `tool version probe failed`. No pinned tool binary is provisioned in this
  environment and no install/network was authorized; these are legitimate environment BLOCKED
  results, not silently-passed gaps.
- `structure.bundle-references` — **BLOCKED**: `73 pre-existing violation(s) held at the
  baseline (bundle_link_baseline.json)`. `tools/bundle_link_baseline.json` was last touched at
  commit `a3ede138` (outside this branch's `7741d4aa..6eae412c` range) — the 73 violations are
  pre-existing debt, not introduced by this work, and the check correctly reports them as a held
  baseline rather than a fresh FAIL.
- `test.smoke.lite` — **BLOCKED**: `[Errno 2] No such file or directory:
  'configs/claude/.venv/bin/manifest'`. The materialized candidate does not copy the
  (gitignored) `.venv`; this is the same class of Phase-3 candidate-provisioning gap as
  `structure.inventory` and is listed in `coverage_pending`.
- `lint.markdown.keydocs` — **FAIL**: `markdownlint-cli2 executable matching action pin
  21c1be1b93ad9ed58fa840aacc3f279cde2a72ff is not provisioned` (tool not installed; reported as
  FAIL rather than BLOCKED per this check's own body — worth Phase 3 scrutiny for status-code
  consistency with the otherwise-BLOCKED tool-absence pattern, but not something this task has
  authority to change).
- `test.bundle-partition` — **FAIL**: `offline bats executable for the npx control is not
  provisioned`.
- `generated.commands-doc`, `generated.plugin-views`, `generated.capability-inventory`,
  `hook.check-cursor-rules-drift` — **BLOCKED**: `candidate identity changed` (these compare
  materialized-candidate output against a freshly generated copy; the candidate's own identity
  invalidated between generation steps in this constrained environment — consistent with
  `coverage_pending` Phase 3 provisioning, confirmed clean when run directly against the real
  worktree, see Findings).
- `generated.cursor` — **FAIL**: `Cursor skill inputs unavailable` (same candidate-provisioning
  class).

`security` profile adds: `hook.check-credentials` BLOCKED (`candidate identity changed`),
`hook.gitleaks` BLOCKED (`tool version mismatch`); `hook.detect-private-key` and
`hook.terraform_trivy` NOT_APPLICABLE (`zero applicable changed paths` — legitimate, `--base
HEAD` selects no changed paths).

`release` profile adds beyond `full`: the `security` group's four checks above, plus
`package.release-archive` and `package.release-manifest` BLOCKED (`tool version mismatch`).

**None of these were worked around, silenced, or had their scope narrowed.** No tool was
installed; no exclusion was broadened; no baseline was refreshed.

## What Phase 3 still owes (from `coverage_pending` + design doc)

- Deterministic candidate prerequisite provisioning for whole-project structure/test/smoke
  checks (`.apm/skills` mirror, `.venv` equivalent, generated-artifact regeneration inside the
  materialized candidate) — currently the single largest source of BLOCKED/FAIL results (44–99
  `coverage_pending` entries per profile).
- Approved security/package/type-coverage checks referenced by the design's profile table
  (`full`/`security`/`release` "whole-graph checks as introduced in Phase 3").
- Pinned-tool provisioning in whatever environment eventually runs these profiles for real
  (markdownlint-cli2, gitleaks, pyright, shellcheck/shfmt, bats offline binary, etc.) — this is
  an environment/CI-setup concern, not a registry defect.
- Fresh, provenance-bound receipts (design doc: "Phase 3 extends this into reusable,
  provenance-bound receipts").
- Only after all of the above is a `full`/`security`/`release` profile expected to reach PASS
  against the real candidate, at which point Phase 5 (administrator-owned branch protection /
  required-status activation) becomes the next gate — explicitly out of scope for this task and
  for Phase 2 generally.

## Findings from the independent total-diff review (`git diff 7741d4aa..6eae412c`)

Total diff: 63 files changed, 26813 insertions(+), 39 deletions(-) across 9 commits.

1. **Process gap caught, no residual defect**: my first test pass (item 1 above) missed 8 of the
   16 new/changed `tests/python/manifest_agent/test_*.py` modules
   (`test_check_path_filters.py`, `test_check_preparation.py`, `test_check_preservation.py`,
   `test_check_runner.py`, `test_shared_check_recursion.py`, `test_shared_check_workflow.py`,
   `test_tool_version_security.py`, `test_tool_versions.py`) because I built the initial command
   list from the brief's category names rather than from `git diff --stat`. Running `git diff
   --stat` against the full commit range and diffing it against the file list I'd already run
   caught the gap before this report was finalized. All 8 modules pass (186 tests, folded into
   the "combined targeted total" above). This is exactly the class of defect the brief warned
   about (a task's own scoped verification missing something the full diff would show) —
   surfaced here, not left latent.
2. **Generator `--check` sweep — no drift found.** Ran every generator referenced by
   `tools/project_checks/generated.py` directly against the real (non-materialized) worktree,
   the same class of check that caught the `docs/COMMANDS.md` drift regression in Task 9's
   review round:
   - `generate_commands_doc.py --check` → exit 0
   - `tools/generate_plugin_views.py --check --repo-root .` → exit 0 (only reproducible with the
     `uv run ... --with jsonschema --with click --with pyyaml` environment; a bare `python3` or
     the committed `configs/claude/.venv` both fail with `ModuleNotFoundError` because neither
     has `manifest_agent`/`jsonschema` installed — an environment-setup fact, not a code defect)
   - `tools/vendor_bundle_dependencies.py --check` → exit 0 (`vendored PyYAML 6.0.3 is current`)
   - `tools/render_capability_inventory.py --check` → exit 0
   - `tools/render_plugin_capability_matrix.py --check --inspection
     tests/fixtures/plugin_capability_inspection.json` → exit 0
   - `configs/claude/scripts/generate_cursor_rules.sh --dry-run` → `0 created, 0 updated, 123
     unchanged, 0 removed` / `mcp.json: unchanged` / `0 created, 0 updated, 15 unchanged, 0
     removed` — no drift
   No downstream generated artifact was left stale by this branch's commits.
3. **Preservation-to-registry coverage — complete, no silent drops.** Cross-referenced every
   `check-preservation.json` control's `check_ids` (disposition `retained`, 71 unique IDs across
   75 controls) against `config/project-checks.json`'s registry (71 check IDs): **zero missing**.
   The 35 `setup`-disposition and 5 `publication`-disposition controls were confirmed correctly
   excluded from becoming profile checks — none of the 5 `manifest-release.yml` publication
   steps (`release.inspect-existing`, `release.ensure-tag`, `release.create-draft`,
   `release.upload-assets`, `release.publish`) appear as a `check_ids` entry anywhere, matching
   the design's "Publication-only controls have a separate destination class ... never
   masquerade as release-profile checks."
4. No other instance of the "task changed X, downstream artifact Y never re-verified" defect
   class was found in this pass. I did not do a line-by-line read of every check body in
   `tools/project_checks/*.py` (hooks.py/structure.py/packages.py/tool_versions.py are ~2,300
   combined lines); the targeted-plus-full test suites (532 + 3100 tests) and the generator
   `--check` sweep are the coverage this review relied on instead of a manual line read.

## Preservation integrity (`config/check-preservation.json`)

`observed_revision`: `7741d4aa588ed57791af15ea0eedd8862305ff0c` (the branch's own starting
commit, i.e. the oracle was frozen before any Phase 2 registry/CI work began). File **not
modified** by this task — `git status --short config/check-preservation.json` empty throughout.

Independently recomputed blob IDs and sha256 for all 3 recorded sources against git at
`observed_revision`, and compared against `git rev-parse HEAD:<path>` for current drift:

| Source | Recorded blob @ observed_revision | Verified independently | Current HEAD blob | Changed since observed_revision? |
|---|---|---|---|---|
| `.pre-commit-config.yaml` | `30f69dd6...` | match (sha256 `5ad81b25...` recomputed via `git show \| shasum -a 256`) | `30f69dd6...` | No |
| `.github/workflows/ci.yml` | `a8d1c79f...` | match | `bd57790e...` | **Yes** — expected: Task 9 added the shadow-checks jobs to this file |
| `.github/workflows/manifest-release.yml` | `c74aca77...` | match | `c74aca77...` | No |

All three recorded hashes correspond exactly to what they claim at `observed_revision`; the one
file that has since diverged (`ci.yml`) diverged for the documented, reviewed reason (Task 9's
shadow-path wiring), not silent drift. This is precisely the relationship the brief describes:
the oracle is a frozen point-in-time record, not a live mirror, so a source file changing after
`observed_revision` is expected and does not itself indicate a preservation violation — the
question the oracle actually answers ("does the *migrated* check set still match what CI/hooks
declared at the frozen revision") is answered by the `check_ids` coverage check above (Finding
3), which is clean.

## Rollout / rollback

**Rollout (not performed; instructions only):**
1. Current state: 4 shadow jobs (`shadow-checks-structure`, `shadow-checks-lint`,
   `shadow-checks-test`, `shadow-checks-aggregate`) run in `.github/workflows/ci.yml`,
   job-level `continue-on-error: true` on all four, `needs:` wiring the aggregate job to the
   three producers and rejecting any non-`success` producer conclusion before aggregating. They
   are purely observational — a shadow job going red never reddens the required `lint`/`test`/
   `validate` jobs, which are untouched.
2. To observe shadow output on a real PR: push the branch, open a PR, let CI run, inspect the
   `shadow-checks-aggregate` job log (aggregate verdict currently goes only to the job log — no
   `GITHUB_STEP_SUMMARY` line or artifact yet, a Task 9 "minor (deferred)" item worth adding
   before promotion).
3. Before promoting any shared check to required status: (a) close every `coverage_pending`
   item via Phase 3 candidate provisioning and approved security/type checks; (b) confirm all
   four profiles reach PASS against the real candidate in the actual CI environment (not just
   this local worktree); (c) get Phase 5 administrator approval to flip branch-protection
   required-status settings — this task has no authority to do that and did not attempt it.
4. No push, merge, branch-protection change, or `gh` mutation was performed by this task.

**Rollback (not performed; instructions only):**
- This branch is entirely local and unpushed; discarding it (`git branch -D
  wip/enforcement-first-shared-checks` from a location that isn't currently checked into it, or
  simply not merging) fully reverts all Phase 1/2 work with no live-system impact.
- If any individual commit needs reverting after a partial merge, revert in reverse commit
  order (`6eae412c` → `f4269588`) since later commits fix issues found in earlier ones
  (`bc35cf75`, `53ec353c`, `6eae412c` are fix-rounds on top of `864a96b9`/`b4d2eae8`/`77e041ce`).
- `config/check-preservation.json` must never be hand-edited to "fix" a rollback; if the oracle
  itself is wrong, that is a separate reviewed correction, not part of a code rollback.

## Review disposition of prior tasks (from the SDD ledger)

Per `.superpowers/sdd/2026-09-08-shared-checks-ci-implementation/progress.md`:
- **Tasks 1–8**: COMPLETE in git history (7 commits, final whole-branch review clean, full
  suite 3061 passed / 3 known environmental failures at that point).
- **Task 9**: implemented at `53ec353c` (DONE_WITH_CONCERNS), reviewed — spec review found 4
  Important findings (aggregate job self-match, `continue-on-error` surface, `docs/COMMANDS.md`
  drift, recursion-test static-vs-invocation-count) + 4 minors; fix round 1/5 addressed all 4
  Important findings (commits `53ec353c..6eae412c`), scoped re-review approved. Deferred minors:
  stale docstring in `ci_context_cli.py`, shadow group set hard-coded in 3 places with no
  cross-tying test, aggregate verdict has no step-summary/artifact. Controller's independent
  full-suite run at `6eae412c`: 3 failed / 3100 passed / 16 skipped, flake did not recur —
  matches this task's own full-suite run exactly.
- **Task 10 (this task)**: dispatched at BASE `6eae412c`; produces this evidence report with an
  explicit INCOMPLETE/BLOCKED disposition rather than a completion claim, per the ledger's
  Task 10 ruling and plan lines 35–37.

## Caveats and unverifiable items

- **Bats suite: PARTIAL / UNVERIFIED, capped after 13+ minutes** (see Evidence above) — no
  pass/fail counts were recoverable; the run was inside `deploy_runtime_state_e2e.bats`
  (roughly a third of the way through) when killed. Whoever picks this up next should re-run
  `bats tests/bats/` to completion (likely 20-40+ minutes given the observed rate) in a session
  budgeted for it, ideally on a host pinned to Bats 1.11.1 to match CI.
- **Bats version mismatch**: CI pins 1.11.1; this host runs 1.13.0. Even a completed local run
  would only have been advisory — behavioral parity between the two Bats versions was not
  audited.
- **`manifest_agent` package availability outside `uv run`**: several generator/check bodies
  only succeed inside the `uv run --project configs/claude --with jsonschema --with click --with
  pyyaml` environment; the committed `configs/claude/.venv` and a bare host `python3` both lack
  `manifest_agent`/`jsonschema`. This is consistent with the check registry's own `tool version
  mismatch` BLOCKED results and is not a defect in this branch, but it does mean **no full
  project graph (whole-package build/type-check/test graph) was run in this session** beyond
  what the `full`/`release` profile invocations already attempted and reported BLOCKED/FAIL for
  missing tools — per the task's explicit no-install/no-network constraint, this could not be
  resolved locally. **Narrow unit fixtures did not and cannot certify these whole-project
  graphs**; that certification remains a Phase 3 deliverable, run in a properly provisioned
  environment (likely CI itself once the shadow path graduates).
- **`lint.markdown.keydocs` and `test.bundle-partition` report FAIL rather than BLOCKED** for
  what is, on inspection, a missing-tool condition (`markdownlint-cli2`/offline `bats`
  executable "not provisioned"). This may be worth Phase 3 attention for status-code consistency
  with the otherwise-uniform BLOCKED-on-missing-tool pattern seen everywhere else in this run;
  flagged here as an observation, not fixed, per this task's no-source-change-without-report
  constraint.
- No installation, network access, or host/branch-protection mutation was performed. No file
  under `config/check-preservation.json` was modified. This report and its containing directory
  are the only new/changed paths produced by this task.

## Addendum (final fix wave, head `e3a52ffb`) — superseded observations

The per-profile table above (["Per-profile status"](#per-profile-status-candidate-registry---base-head-real-execution--not---list)),
and the "FAIL rather than BLOCKED" observations that follow it, describe
commit `6eae412c` and were true at the time they were written. They are
**not rewritten here** — they remain an accurate record of that commit's
behavior. Head `e3a52ffb` (see
[`task-10-fix-report.md`](task-10-fix-report.md)) changed the FAIL/BLOCKED
mapping so that repo-owned `honors_status_contract` bodies exiting 3 are
correctly recorded as BLOCKED instead of FAIL, which eliminated every FAIL
in the `full` profile reproduction:

> Before/after Counter (reproduction command, caches cleared): `profile
> BLOCKED; Counter({'BLOCKED': 39, 'PASS': 15, 'NOT_APPLICABLE': 11})` — zero
> `FAIL`, controller-verified.
> — [`task-10-fix-report.md`](task-10-fix-report.md), "Before/after Counter"

The `full` profile now reports **BLOCKED** (not FAIL) with `BLOCKED=39,
PASS=15, NOT_APPLICABLE=11`. Treat the original table's `full FAIL 4` and
the "FAIL rather than BLOCKED" prose above as describing pre-fix behavior
only; consult `task-10-fix-report.md` for the current mapping and root
cause.
