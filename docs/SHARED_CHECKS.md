# Shared Checks

> The `manifest check` / `manifest check-aggregate` shared-check entry: commands, profiles, status vocabulary, and what is not authoritative yet.

**Last Updated**: 2026-09-10

## What this is

A single, explicitly configured registry of project checks
(`config/project-checks.json`) that replaces per-job inline check logic with
one shared command. It is being migrated in from the pre-existing CI jobs and
`.pre-commit-config.yaml` hooks **without removing them** — see
[Coverage limits](#coverage-limits) below for why nothing has been promoted
yet.

## Commands

```bash
# List the checks a profile/group would run, without executing them
manifest check quick --list --json

# Run one profile against a disposable candidate checkout
manifest check full --group structure --json --output report.json

# Validate per-group CI producer receipts and emit one aggregate verdict
manifest check-aggregate full \
  --project-config config/project-checks.json \
  --results-dir results/ \
  --context context.json \
  --json
```

- `PROFILE` is one of `quick`, `full`, `security`, `release`
  (`config/project-checks.json` → `profiles`). `security` (C6b) is a
  **named subset with no CI aggregate of its own** — its five hook/scan ids
  (`hook.check-credentials`, `hook.detect-private-key`, `hook.gitleaks`,
  `hook.terraform_trivy`, `security.semgrep`) are also folded into `full`,
  so exactly one aggregate (`full`'s) and one required check can ever exist;
  `security` remains useful standalone for local/hook use and as part of
  `release`'s composition.
- `--group` narrows to one of `structure`, `lint`, `test`, `security`,
  `package` (`config/project-checks.json` → `checks[].group`). A
  group-scoped run is always `"partial": true` and cannot certify a whole
  profile on its own.
- `--base REV` selects checks by changed paths against a git revision.
- `--output PATH` writes the JSON report atomically to `PATH` (outside the
  source checkout); omit it to print to stdout.

## Setup

`manifest check` runs from the repository root via `uv run manifest check
...` — no separate install step beyond the repo's own `uv sync` /
`pip install uv`. It never downloads or installs anything itself: any
missing tool or interpreter it needs is reported as `BLOCKED`, not silently
fetched.

## Status vocabulary

| Status | Exit code | Meaning |
|--------|-----------|---------|
| `PASS` | `0` | Every required check in the (profile, group) closure ran and passed. |
| `FAIL` | `2` | At least one check ran and failed. |
| `BLOCKED` | `3` | At least one check could not be verified (missing tool, unavailable candidate, unresolved `coverage_pending` obligation, or a resolved group with zero checks — see below). |

`BLOCKED` means **"could not be verified"** — it is never a pass, even when
no check explicitly failed. When `FAIL` and `BLOCKED` coexist in the same
run, the report returns exit `2` (`FAIL`) and retains both sets of
diagnostics; a fail is never hidden behind a blocked result. A group result
is always partial and cannot certify a whole profile.

**A resolved group with zero checks is BLOCKED, never PASS (C6b).**
`resolve_checks(registry, profile, group)` can legitimately return an empty
tuple — e.g. `full` x `security` returned zero checks before this chunk
folded the `security`-profile ids into `full`. An empty check set produces
an empty `results`/`coverage_pending` set, which `_report`/`_list_report`
would otherwise read as an honest PASS: a receipt about nothing.
`manifest check <profile> --group <g>` and `--list` both now raise
`group_selection.EmptyGroupError` (`src/manifest_agent/checks/
group_selection.py`) with reason `profile <p> selects no checks in group
<g>` instead. Separately, `manifest check-aggregate` rejects any individual
producer receipt whose `results` list is empty with `stale receipt: empty
results for group <g>` — a receipt that asserts nothing about its group is
never trusted as evidence, even if every other structural check on it
passes.

## The shadow CI path

`.github/workflows/ci.yml` runs `shadow-checks-structure`,
`shadow-checks-lint`, `shadow-checks-test`, `shadow-checks-security`, and
`shadow-checks-package` alongside (never instead of) the pre-existing
`lint`/`test`/`validate` jobs. Each shadow job first resolves a base revision,
then invokes the shared command exactly once (`uv run manifest check full
--group <group> --project-config config/project-checks.json --base
<resolved-sha> --json --output ...`) and uploads its report as a
run-attempt-scoped artifact (`shadow-receipt-<group>-${{ github.run_attempt
}}`) so a workflow re-run cannot mix evidence from a prior attempt.
`checks-aggregate-full` (renamed from `shadow-checks-aggregate`, C6b) runs
with `if: always()`, rejects any producer that
wrote no receipt evidence, then builds the current-run context
(`tools/project_checks/ci_context_cli.py`, read-only via `gh api`) and calls
`manifest check-aggregate`, writing its own receipt
(`shadow-receipt-aggregate-${{ github.run_attempt }}`) and publishing the
verdict to the job summary (`$GITHUB_STEP_SUMMARY`) so it is visible without
opening step logs.

### Base revision (fixed: was degenerate on `push`)

Each shadow job's "Resolve base revision" step reads `github.event.before`
(via `env:`, never interpolated into the script body) for `push` events,
falling back to `HEAD~1` when `before` is the all-zeros SHA GitHub sends for
a new or force-pushed ref, and to the empty-tree object hash
(`4b825dc642cb6eb9a060e54bf8d69288fbee4904`) for a repository's first commit.
`pull_request` events still use `github.event.pull_request.base.sha`. Before
this fix, `push` resolved its base with `git merge-base HEAD
"origin/${DEFAULT_BRANCH}"` — but `push` triggers only on `branches: [main]`,
and after a `fetch-depth: 0` checkout `origin/main` already equals `HEAD`
once the push has landed, so that merge-base was HEAD itself: `git diff
--name-only HEAD HEAD` returns no changed paths, and every path-filtered
check silently reads `NOT_APPLICABLE` instead of running. The `before`-based
resolution above diffs against the ref's actual prior state instead.

### Producer-rejection signal (fixed: was rejecting every real run)

The rejection step reads each producer's `steps.shadow.outputs.receipt_written`
job output, not `steps.shadow.outcome` and not `needs.<job>.result`.
`needs.*.result` is unusable: the producer jobs set job-level
`continue-on-error: true`, which makes GitHub report even a failed producer
as `result: "success"` in the `needs` context. `outcome` looked like a
working substitute but was not: `manifest check` exits `2` (`FAIL`) or `3`
(`BLOCKED`) exactly when a group legitimately produced a receipt, so
`outcome` was `"failure"` both when a producer crashed with **no** receipt
and when it ran cleanly and reported FAIL/BLOCKED **with** one. Gating on
`outcome` therefore rejected every real run once any group returned
FAIL/BLOCKED — which, before Phase 3 clears `coverage_pending`, is every
group — so `manifest check-aggregate` never actually ran and every real run
published `Status: UNKNOWN (no aggregate receipt was written)`.

The fix: the shadow step itself now captures `manifest check`'s exit code,
always exits `0` (the shadow path must never turn the job red on its own),
and separately checks whether the file it declared with `--output` actually
exists, exporting that as the `receipt_written` job output
(`"true"`/`"false"`). The aggregate's rejection step rejects any producer
whose `receipt_written` is not exactly `"true"` — an allow-list check, so a
skipped, cancelled, or crashed-with-no-receipt producer is rejected the same
as an explicit crash. A producer that ran and returned FAIL/BLOCKED **with**
a receipt is accepted at this gate; the receipt's own status is what then
feeds `manifest check-aggregate`'s verdict, not this gate. A producer that
never wrote a receipt at all — the guard's real purpose — is still rejected.

All six jobs (`shadow-checks-structure`, `shadow-checks-lint`,
`shadow-checks-test`, `shadow-checks-security`, `shadow-checks-package`,
`checks-aggregate-full`) set job-level `continue-on-error: true`. This is
deliberately at the **job** level, not just on individual steps: a step-only
`continue-on-error` still lets an unrelated step (checkout, `uv` install,
context build, receipt download) fail and redden the whole job — and
therefore the workflow's overall conclusion — which job-level
`continue-on-error` prevents. Combined with the aggregate job's `if:
always()` and read-only `permissions:`, the shadow path is **never a
required status** and cannot gate a merge, and it cannot turn the workflow
conclusion red either. Branch protection is untouched; that is a separate,
later phase (Phase 5).

### Why the aggregate still reports `BLOCKED`

The fixes above make `manifest check-aggregate` actually *run* on every real
invocation instead of being skipped, but its verdict is still `BLOCKED`
today, for a reason unrelated to either defect: every check in every group
still has at least one open `coverage_pending` obligation (see
[Coverage limits](#coverage-limits)), which alone forces `BLOCKED`.

A `BLOCKED` aggregate report is therefore still the expected, honest shape
today — the fixes mean it is now a *real* `BLOCKED` verdict computed from
actual receipts, not a fabricated `UNKNOWN` (the base/rejection-signal
defects) or a PASS-by-omission (the zero-checks defect, C6b) on the way to
it.

**C6b — the `full` x `security` false green.** Before this chunk, `full`'s
registry closure spanned four groups — `structure`, `lint`, `test`,
`package` — not five: `full`'s profile list contained no `security`-group
check id, so `resolve_checks(registry, "full", "security")` returned zero
checks, and an empty check set with zero `coverage_pending` obligations
computed `_report`'s status as an honest-looking **PASS** — a receipt about
nothing. That the *aggregate* stayed `BLOCKED` (`"producer job references
unexpected group: 'security'"`, since `full` didn't request that group) was
coincidence, not a control: the per-group `manifest check full --group
security` receipt itself was a false green. This chunk closes both ends:
`profiles.full` now includes the five `security`-profile ids
(`hook.check-credentials`, `hook.detect-private-key`, `hook.gitleaks`,
`hook.terraform_trivy`, `security.semgrep`), so `resolve_checks(registry,
"full", "security")` resolves one real check (`security.semgrep`) and the
`shadow-checks-security` producer becomes a legitimate, expected group
instead of a perpetual "unexpected group" diagnostic; and the new
zero-checks rule (above) means a future group with nothing to check can
never silently read as PASS again, in either the per-group or the aggregate
path.

## Toolchain provisioning

`manifest check` never downloads or installs anything; pinned tools are
resolved from a content-addressed **toolchain store**, hash-verified against
`config/toolchain.lock.json` on every preflight, never trusted by name from
`PATH`. Populating the store is a separate, explicitly invoked command:

```bash
# Provision every lock-listed tool for the running platform
manifest provision --lock config/toolchain.lock.json

# Validate the store against the lock without downloading anything
manifest provision --lock config/toolchain.lock.json --offline   # exit 3 if incomplete

# Adopt an existing binary only if its sha256 matches the lock
manifest provision --lock config/toolchain.lock.json --import gitleaks=/usr/local/bin/gitleaks
```

- **Store location**: `$MANIFEST_TOOLCHAIN_STORE`, else
  `$XDG_CACHE_HOME/manifest/toolchain`, else `~/.cache/manifest/toolchain` —
  never inside the repository or the candidate.
- **Registry binding**: `tools[NAME].executable` may be
  `"store:<bundle>/<relative-exe>"` (schema 1.1, additive). Plain bare names
  remain legal only for the always-present interpreter set (`python3`,
  `bash`) or a repository-relative script path; every other bare command
  name is exactly the `PATH`-trust gap this closes (`hooks.py`/`ruff.py`
  etc. migrate to `store:` in a later chunk — see `coverage_pending`).
- **`--offline`**: checks every lock-attested `(bundle, platform)` pair
  against the store's `manifest.json` and re-hashes each executable; exits
  `0` only if every attested tool for the target platform resolves cleanly,
  else `3`. It never contacts the network.
- **Unattested entries always BLOCK.** `config/toolchain.lock.json` is
  committed with real tool/version/platform structure but `"sha256": null`
  wherever a real hash needs a download; an unattested entry can never
  resolve to `PASS` — only `BLOCKED: toolchain: <tool> unattested for
  <platform>`.

Failure semantics (`src/manifest_agent/checks/toolchain.py::resolve`):

| Condition | Status | Reason string |
|---|---|---|
| Lock has no attested entry for this platform | BLOCKED | `toolchain: <tool> unattested for <platform>` |
| Store missing the entry | BLOCKED | `toolchain: <tool> not provisioned (run manifest provision)` |
| sha256 mismatch (executable or interpreter) | BLOCKED | `toolchain: <tool> digest mismatch` |
| Store `manifest.json` lock digest ≠ the registry's lock digest | BLOCKED | `toolchain: store stale (lock changed)` |
| Store mutated between preflight and the check's own run | BLOCKED | `toolchain: store changed during run` |
| Version probe mismatch (existing, unrelated to the store) | BLOCKED | `tool version mismatch` |

Spec row: `rule` pinned tools come only from an attested, hash-verified
store → `tool` `manifest provision`, `config/toolchain.lock.json`,
`schemas/toolchain.lock.schema.json`, `src/manifest_agent/checks/
toolchain.py` → `scope` every `tools[]` entry except the interpreter
allow-list → `trigger` every check/preparation preflight → `failure` BLOCKED
per the table above → `exception` none (an unpinned tool is not a check) →
`test` `tests/python/manifest_agent/test_toolchain.py` (fixture-lock unit
coverage of every row above), `test_toolchain_provision.py` (`--offline`,
`--import`, download-hash-mismatch, unimplemented-kind honesty),
`test_toolchain_cli.py` (the real CLI via `file://` lock URLs),
`test_check_runner_toolchain.py` (end-to-end through `run_profile`,
including the swapped-launcher and store-changed-mid-run cases), and
`test_toolchain_registry_guards.py` (no check/preparation/tool argv ever
contains `"provision"`; the allow-list is exactly `python3` + `bash` +
repo-relative scripts).

## Identity-based debt ratchet

Debt allowances are per-**finding identity**, not per-file/rule count. A
count cannot see a same-count replacement: fix one violation and introduce a
different one in the same file, and the count is unchanged — the swap is
invisible. `src/manifest_agent/checks/debt.py` keys each finding on

```
identity = sha256(check_id | repo_path | anchor | normalized_message | ordinal)
```

`anchor` is the innermost enclosing function/class (`""` at file level;
never a line number, which churns on unrelated edits).
`normalized_message` collapses digits, whitespace runs, and quoted paths, so
"function is 72 lines" and "...73 lines" share an identity while the finding
persists. `ordinal` is the 0-based, line-ordered index among otherwise-equal
findings in one file — this is what catches the same-count replacement: a
genuinely different finding landing in the same "slot" gets a different
identity from whatever occupied that slot before.

**Authority boundary**: `config/debt-baseline.json` (schema v2) is a
**reviewed record, never agent-granted authority**. `--propose-baseline
--output PATH` writes a complete proposal to a path *outside* the source
tree; nothing in `debt.py` or the checks below may write the committed file
in place, and no automatic path may apply a proposal. A human reviews and
commits it, the same as any other source change.

Verdict rules (`debt.py::evaluate`), against the candidate tree `F_cand` and
the **protected base tree** `F_base` (`git archive <base_sha>`, materialized
to a sibling temp dir outside the source worktree — `git archive` failure is
BLOCKED, never an empty pass):

| Condition | Verdict |
|---|---|
| In `F_cand`, not in `F_base`, no baseline entry | FAIL `new debt` |
| Baseline entry's `expires` is before today | FAIL `expired exception` |
| In `F_cand`, not in `F_base`, entry has `retired_base != null` | FAIL `restored debt` — a retired entry can never excuse a reappearance |
| Baseline entry not retired, but its finding is no longer in `F_cand` | FAIL `stale entry: retire it` |
| Baseline entry missing a required field, `expires` more than 180 days after `introduced_base`'s commit date, or a secret-shaped `reason` (`manifest_agent.process.redact_text`) | BLOCKED `invalid baseline` |

`release` runs with `--baseline-from-base`: only the **base tree's**
baseline excuses anything, so a candidate can never ship its own exception —
its own baseline additions are reported under `proposed_exceptions` (data
for a reviewer) but never applied.

| Check | Scope | Trigger | Exception |
|---|---|---|---|
| `debt.constitution` | Code Constitution findings (`configs/claude/scripts/constitution_check.py --format json`) over tracked `*.py`/`*.sh`, non-advisory checks only (same scope as the retired count baseline) | `full`, `security`; `release` also runs `debt.constitution.release` (`--baseline-from-base`) | reviewed `config/debt-baseline.json` entry |
| `debt.bundle-links` | `tools/check_bundle_link_references.py --json` violations | `full`, `security`; `release` also runs `debt.bundle-links.release` (`--baseline-from-base`) | reviewed `config/debt-baseline.json` entry |

Both are `honors_status_contract` (`tools/project_checks/debt_checks.py`,
exit 0/2/3). `configs/claude/scripts/constitution/baseline.py` (count-based)
and `tools/bundle_link_baseline.py` are unchanged and keep gating their
existing fast paths (`hook.constitution-check`'s pre-write path;
`structure.bundle-references`'s own count ratchet) — this chunk adds the
identity ratchet as new, additive project checks rather than rewiring those
existing entry points, so neither baseline file's current behavior
regresses. `config/debt-baseline.json` starts empty: every finding already
present on the immediately-prior base tree is not "new debt" under the
first rule regardless of whether an entry exists, so no entry is needed just
to keep the repository's current state green — an entry is added only for a
finding a human has reviewed and decided to accept.

`constitution_check.py --update-baseline` is retired; propose an update to
the identity ratchet with:

```bash
manifest check debt.constitution --propose-baseline --output /somewhere/outside/the/repo/proposal.json
```

Spec row: `rule` no new/expired/restored/stale structural debt → `tool`
`src/manifest_agent/checks/debt.py`, `tools/project_checks/debt_checks.py`,
`config/debt-baseline.json` → `scope` authored Python/Bash (constitution),
plugin skill docs (bundle links) → `trigger` `full`/`security`/`release` →
`failure` FAIL per the verdict table, BLOCKED on an unavailable base tree or
an invalid baseline entry → `exception` reviewed baseline entry with owner +
≤180-day expiry, never agent- or check-applied → `test`
`tests/python/manifest_agent/test_debt.py` (identity scheme, the
same-count-replacement proof, all five verdict rules, propose-baseline
containment), `tests/python/constitution/test_anchor.py` (Python/Bash
anchor computation), `tests/python/manifest_agent/test_debt_checks_cli.py`
(end-to-end: BLOCKED on an unresolvable base, PASS on a clean repo, and the
propose-baseline containment guarantee from the real CLI).

## Registry hygiene: engines, not wrappers (C2)

**Governing rule**: the registry pins the underlying **engine** a hook runs;
the wrapper that installs or invokes it (a pre-commit `repo:`/`rev:`, an npm
launcher, a GitHub Action) is provisioning detail recorded separately, not a
second identity a check re-verifies on every run.

**Dormant-language checks report `NOT_APPLICABLE`, never `PASS`.** No `.tf`,
`.go`, or Rust source exists anywhere in this repository (`find . -iname
'*.tf' -o -iname '*.go' -o -iname 'Cargo.toml'` — verified before this chunk
landed). `hook.golangci-lint`, the four `hook.terraform_*` checks, and
`hook.cargo-fmt-check`/`hook.cargo-clippy` stay registered with their real
`selection: changed`/`project` + `types_or` filters; when zero changed/project
paths match go/terraform/rust, `runner._selection_outcome` returns
`NOT_APPLICABLE` with diagnostics `"zero applicable ... input selector"`
**before** any tool preflight runs — a check that "passes" because it had
nothing to look at is a false green, so this is the correct terminal state,
not PASS. These five tool families are **excluded from
`config/toolchain.lock.json`** by design: if a `.tf`/`.go`/Rust file ever
lands, the same check goes `BLOCKED` (`toolchain: <tool> not provisioned`)
instead of silently resolving whatever happens to be on `PATH`. Test:
`tests/python/manifest_agent/test_check_dormant_languages.py`.

**Engine pins landed this chunk:**

| Check(s) | Old identity | New identity | Store-resolved? |
|---|---|---|---|
| `lint.shell.scripts`, `lint.shell.bootstrap`, `hook.shellcheck` | `distribution:shellcheck-py=0.11.0.1;command:shellcheck=0.11.0` | `command:shellcheck=0.11.0` | **Yes — BLOCKED unattested until C7.** Body resolves `store:shellcheck/bin/shellcheck` via `tools/project_checks/toolchain_resolve.py`; the registry-level version probe (drift detection only, not a security control) is unchanged. |
| `hook.shfmt` | `"ok"` (unpinned placeholder) | `command:shfmt=3.13.1`; body argv is `-d` (check-only), never `-w` | **Yes — BLOCKED unattested until C7.** Body resolves `store:shfmt/bin/shfmt`. |
| `lint.yaml.config`, `hook.yamllint` | `distribution:pyyaml=6.0.2;distribution:yamllint=1.38.0;command:yamllint=1.38.0` | `distribution:yamllint=1.38.0` | **Yes — BLOCKED unattested until C7 (twice over: `python-env` is also not yet an implemented provision kind).** Body resolves `store:python-env/bin/yamllint`. |
| `hook.markdownlint-cli2` | already `command:markdownlint-cli2=0.23.0` | unchanged | **Yes — BLOCKED unattested until C7.** Direct-argv check; registry `tool.executable` and `check.argv[0]` are both `store:node-env/bin/markdownlint-cli2` (same generic runner rewrite `test.bats` already used — no wrapper body needed). |
| `test.bats` | `./node_modules/.bin/bats` | `command:bats=1.11.1`; argv is `store:node-env/bin/bats` | Yes (unchanged from before this chunk). |
| `test.bundle-partition` | `"ok"` + hardcoded BLOCKED (`npx` control) | `command:bats=1.11.1`; runs `tests/bats/bundle_partition.bats` | **Yes — BLOCKED unattested until C7.** Body resolves `store:node-env/bin/bats`. |
| `hook.gitleaks` | `command:gitleaks=8.30.0` | `command:gitleaks=8.30.1` | No (unchanged from before this chunk — out of C2b's explicit scope; still `shutil.which("gitleaks")` in `gitleaks_check.py`). |
| `dependency.lock.config`, `dependency.lock.delegate`, `dependency.lock.root`, `package.coordinator`, `package.config` | `command:uv=0.12.6` | unchanged | **Yes — BLOCKED unattested until C7.** Body resolves `store:uv/bin/uv` via `packages.py::_uv`. |
| `dependency.lock.node`, `package.node-runtime` | `"ok"` | unchanged | **Yes — BLOCKED unavailable until C7 (twice over: the `binary`-kind provisioner records only `bin/node`, not `bin/npm`, so `npm` needs a provisioner extension too).** Body resolves `store:node/bin/node` / `store:node/bin/npm` via `dependency_checks.py`. |

**Honesty caveat (updated, C2b)**: of the 9 engine-bearing checks the C2
engine-pin table touches, **8 of 9** now resolve their engine through the
hash-verified toolchain store (only `hook.gitleaks` remains PATH-resolved,
deliberately out of this chunk's scope — see `gitleaks_check.py`, landed
separately in C6b). This reverses the C2 deferral recorded below (kept for
history): every migrated check is `BLOCKED: toolchain: <tool> unattested for
<platform>` (or `not provisioned`, for the two provisioner-kind gaps above)
until C7 fills in real hashes and implements the `python-env`/`node-env`
kinds — that is the intended, honest result: a receipt that used to say PASS
about an unverified tool now says BLOCKED about the same tool, truthfully.

**Why the other 8 were not `store:`-wired at first — the original C2
deferral, now reversed.** `phase-3-5-decisions.md` "Corrections 2026-09-10" >
"Correction 2" overturns this section's original reasoning. It is kept
verbatim below for history; C2b's migration described above supersedes it.

1. `registry.py`'s `_validate_tool_reference` requires
   `check["argv"][0] == tool["executable"]` exactly. Checks wrapped through
   `python3 tools/project_checks/{structure,hooks}.py <id> --root .` have
   `argv[0] == "python3"` — **this was never actually a blocker**: C2b's
   in-body resolution (mirroring `analysis_checks.resolve_scanner`, already
   landed for `types.python`/`security.semgrep`) resolves `store:` references
   *inside* the `python3` body, touching neither the registry argv nor this
   invariant. `hook.markdownlint-cli2` (a direct-argv check) migrated by the
   other path: both `tool.executable` and `check.argv[0]` moved to
   `store:node-env/bin/markdownlint-cli2` together, exactly like `test.bats`.
2. **The original deferral's premise — "every lock entry is
   `exe_sha256: null`, so store resolution verifies nothing while turning a
   working check BLOCKED" — was refuted, not merely reconsidered.** Today's
   profile-level BLOCKED is coincidence (unrelated pending obligations), not
   a control; a PATH-resolved PASS is the exact pre-3a trust model this
   system exists to close (a swapped launcher reporting the pinned version
   string still passes). An honest BLOCKED is worth more than a PASS that
   verifies nothing. C7 becomes a pure hash-fill with no further wiring
   changes.

**`hook.gitleaks` scans `base..HEAD`, never `--staged` (C6b).** Before this
chunk the registry's argv was `gitleaks git --pre-commit --redact --staged
--verbose`, which reads the git *index* — on a CI checkout (a clean clone,
nothing staged) that is empty, so the check exits `0` having scanned zero
commits: a secret scanner that never runs is worse than no scanner, because
nobody investigates a pass. `tools/project_checks/gitleaks_check.py` reads
the candidate's base revision from the same `.git/candidate-base-sha`
sidecar `debt_checks.py`/`analysis_checks.py` already use and scans exactly
`gitleaks git --log-opts "<base>..HEAD" --redact --verbose`; when no base
revision is available the run is `BLOCKED "gitleaks: no base revision"`, not
PASS. `tests/python/manifest_agent/test_gitleaks_check.py` proves the old
argv's false green directly (a clean checkout with a secret committed since
base still exits `0` under `--staged`) before pinning the fixed body's
range-scan/no-base/base-equals-head behavior.

**`.gitleaks.toml` default-ruleset defect**: before this chunk, supplying
`[[rules]]` without `[extend] useDefault = true` **replaced** gitleaks' ~150
built-in detectors instead of adding to them — verified locally (gitleaks
8.30.1): a Slack-token-shaped string was missed without `useDefault` and
caught with it, while both custom rules and `useDefault` fire correctly
together. `useDefault = true` is now set; secret detection is additive
again.

**Equivalence records** (`config/check-preservation.json` → `equivalence`,
new list, additive-only — `observed_revision`/`sources`/`source_entries`/
`controls` are untouched): one entry per hook with `hook_id`, `wrapper_rev`,
`engine`, `engine_version`, `wrapper_entry_argv`, `evidence`, and
`fixture_corpus` (`tests/fixtures/equivalence/<hook>/{valid,invalid}.*`, one
passing and one failing input per engine, exercised locally where the engine
is installed — shellcheck 0.11.0, yamllint 1.38.0, bats 1.13.0 (ambient, not
yet the 1.11.1 pin), gitleaks 8.30.1 all matched or reproduced the documented
behavior; shfmt and markdownlint-cli2 are absent locally, so those two
fixture corpora are unverified pending Phase 3 (C7)). `shfmt` and
`markdownlint-cli2-action` explicitly remain **wrapper→engine unconfirmed,
pending C7** (reading the wrapper's `.pre-commit-hooks.yaml`/`action.yml` at
the pinned rev needs network); their `coverage_pending` entries stay.

## Receipt schema v2: no stale evidence (C4)

A receipt (`run_profile`'s JSON report, or `manifest check-aggregate`'s
merged verdict) is evidence, never permission to skip execution — Phase 3
has no success cache, and Phase 4 adapters (`receipt_key` consumers) must
re-run on anything less than an exact match, never degrade a partial match
to PASS. Receipt v2 adds provenance digests on top of the unchanged v1
fields (`src/manifest_agent/checks/receipt.py`, wired into
`runner._report`/`build_report`):

| Field | Derivation |
|---|---|
| `toolchain_digest` | sha256 over the sorted `{tool: sha256(exe)}` mapping of every `store:` tool actually resolved this run (`receipt.resolved_tool_digests` + `receipt.toolchain_digest`) |
| `interpreter_version`, `interpreter_executable_sha256` | `sys.version` / `sys.executable` sha256 recorded in the store's `manifest.json` at provisioning time (`receipt.interpreter_from_store`); empty when no store is reachable |
| `environment_digest` | sha256 of the platform triple (`toolchain.current_platform()`) plus the forwarded environment values, **excluding `HOME`** (`receipt.environment_digest`) |
| `expires_at` | `produced + 24h` for `security`/`release` profiles only; `null` otherwise (`receipt.expires_at`) — advisory feeds are time-sensitive, other profiles do not expire on their own |
| `receipt_key` | `sha256(profile ‖ group ‖ candidate_digest ‖ config_digest ‖ toolchain_digest ‖ environment_digest)` (`receipt.receipt_key`) — the identity a consumer checks for "evidence of exactly this state" |

`schema_version` is `2`. Every profile/group receipt carries these fields;
`aggregate.py`'s `RECEIPT_KEYS`/`RECEIPT_LOCAL_KEYS` were extended to match,
and structurally validate them (non-empty strings; `expires_at` is a string
or `null`) without trusting a receipt's *claimed* `receipt_key` as proof —
`aggregate_results` recomputes what it can and cross-checks the rest:

- **Mixed toolchain across groups is rejected.** If the confirmed receipts
  for one aggregate verdict disagree on `toolchain_digest` (lint resolved
  one `ruff` hash, test resolved another), that is not one verification —
  `aggregate.py::_toolchain_consistency_errors` adds a `"stale receipt:
  toolchain_digest differs across producer groups"` diagnostic and the
  verdict is BLOCKED.
- **Expired `security`/`release` receipts are rejected.** `receipt.is_expired`
  compares each receipt's `expires_at` against the current time;
  `aggregate.py::_receipt_identity_errors` adds `"stale receipt: expired"`
  and the verdict is BLOCKED. A receipt with no `expires_at` (non-expiring
  profile) never triggers this.

A stale or unverifiable receipt is always reported as **BLOCKED "stale
receipt"**, never PASS and never FAIL — there is no exception path.

Spec row: `rule` no stale evidence → `tool` receipt v2 in
`src/manifest_agent/checks/receipt.py` (`runner._report` /
`aggregate.py`) → `scope` every profile/group receipt → `trigger` produce
(`run_profile`) / aggregate (`aggregate_results`) / adapter-consume (Phase 4)
→ `failure` BLOCKED "stale receipt" → `exception` none → `test`
`tests/python/manifest_agent/test_check_receipt.py`: edit-after-success
invalidates `receipt_key`; a tool swap reporting the identical version
string still changes `toolchain_digest`/`receipt_key` (the headline case);
an environment change invalidates `environment_digest` (and `HOME` is
deliberately excluded); mixed `toolchain_digest` across producer groups is
rejected by `aggregate_results`; an expired `security` receipt is rejected
(and a fresh one is accepted).

## Types, source security, node runtime, dependency integrity (C5)

Five new checks (`config/project-checks.json`). **None of them can PASS
locally**: pyright, semgrep, and pip-audit are not installed in this
environment, and the toolchain store is unprovisioned (`exe_sha256: null`,
C7). Every one BLOCKs honestly — that is the correct, verified outcome for
this chunk, not a gap. `hook.pyright` (PATH, unpinned) is removed the same
change that adds `types.python`.

| Rule | Tool / config | Scope | Trigger | Failure | Exception | Test |
|---|---|---|---|---|---|---|
| No new/regressed type errors | `types.python` → `store:node-env/bin/pyright`, `pyrightconfig.json` (`typeCheckingMode: basic`, `reportMissingImports: true`) | `src`, `tools`, `configs/claude/scripts`, `configs/claude/scripts/manifest_model_policy`, `plugins/manifest-delegate`, `tests/python` | `full`, `release` | New pyright `error`-severity diagnostic → FAIL `new debt`; missing pyright/store or missing `pyrightconfig.json` → BLOCKED | Reviewed `config/debt-baseline.json` entry (`debt.py` identities, same file as `debt.constitution`) | `tests/python/manifest_agent/test_analysis_checks.py` (fake-store PASS/FAIL/baseline-excused + real-lock BLOCKED); `tests/fixtures/types/pkg_a.py`+`pkg_b.py` (real cross-package type error) |
| Source security (Python + Bash) | `security.semgrep` → `store:python-env/bin/semgrep`, `config/semgrep/manifest.yml` (9 local rules, ≤15), `.semgrepignore` | Authored Python/Bash | `security`, `release` | ERROR-severity finding → FAIL; missing semgrep/store → BLOCKED | Reviewed, **expiring-only**, `config/debt-baseline.json` entry | `test_analysis_checks.py` (argv contains `--metrics=off`, no `p/...` config, `.semgrepignore` covers exactly the fixture dir); `tests/fixtures/semgrep/<rule-id>/{positive,negative}.*`, one hit each rule (real-semgrep-gated, skips honestly when semgrep is absent) |
| Node runtime builds offline | `package.node-runtime` → copies the whole tracked `plugins/stitch-design` bundle into an isolated dir (never the tracked project dir), runs `npm ci --ignore-scripts --offline` and `node build.mjs --check` there — no `NODE_PATH` | `plugins/stitch-design/runtime/node` | `full`, `release` | `npm ci`/`node build.mjs --check` fails → FAIL; no offline npm cache → BLOCKED | none | `test_dependency_checks.py`: fake npm+node PASS/FAIL/BLOCKED, **plus real `npm`+`node` against a git-tracked fixture with a `file:`-only ESM dependency** (proves the isolated import resolves with zero `NODE_PATH`, and that the tracked project never gains a `node_modules`) |
| Root/node lock integrity | `dependency.lock.root` (`uv lock --check` at repo root, reuses `packages.py::_lock`); `dependency.lock.node` (`npm ci --dry-run --ignore-scripts --offline`) | root `uv.lock`; `plugins/stitch-design/runtime/node/package-lock.json` | `full`, `release` | Lock/manifest mismatch → FAIL; no offline uv/npm cache → BLOCKED | none | `test_project_check_bodies.py::test_dependency_lock_root_check_passes_and_mismatch_fails_without_rewriting_lock` (fake `uv`, root reuses `dependency.lock.config`'s fixture pattern); `test_dependency_checks.py` |
| Dependency advisories | `dependency.audit.python` (`uv export --frozen` → `pip-audit --format json`); `dependency.audit.node` (`npm audit --omit=dev --audit-level=high --json`) | root `uv.lock`; node project lock | **registered, not wired into any profile** (C8) | Known advisory with no valid baseline entry → FAIL `new debt`; feed unreachable → BLOCKED | Time-limited `config/debt-baseline.json` entry, `check: "advisory"`, identity from advisory ID + package + version | `test_dependency_checks.py` (stub `tests/fixtures/advisory/{pip-audit,npm-audit}-stub.json`, no network; BLOCKED-when-unreachable proven with a fake tool printing a network-error diagnostic) |

**Why `dependency.audit.*` stays out of every profile.** Both transmit
dependency metadata (package names/versions) to an external feed — PyPI/OSV
for `pip-audit`, the npm registry for `npm audit` — which is the parent
spec's outstanding "dependency-metadata upload restrictions" decision (open
question 1 in phase-3-5-decisions.md). The bodies and their BLOCKED path are
built and tested here; enabling them in the `security` profile is chunk C8,
gated on that decision plus network (C7-adjacent).

**Why `types.python`/`security.semgrep` cannot use the runner's automatic
`store:` argv rewrite.** Both need custom JSON parsing and
`debt.py`-identity routing, which requires the repo's own status contract
(exit 0/2/3, `honors_status_contract: true`) — and
`registry.py::_validate_status_contract` requires `argv[0]` to be `python3`
(interpreter) invoking `tools/project_checks/*.py`, not a bare `store:...`
reference. `store:node-env/bin/pyright` and `store:python-env/bin/semgrep`
are still the mechanism: `analysis_checks.py::resolve_scanner` calls
`toolchain.resolve()` directly (the same hash-verified store API the
runner's generic preflight uses) from inside the wrapper, so a swapped
scanner with an unchanged version string is still caught, and a missing or
unattested store entry still BLOCKs with the standard `toolchain: <tool>
unattested for <platform>` reason string — proven in
`test_analysis_checks.py` against the real, committed, unattested
`config/toolchain.lock.json` (`exe_sha256: null`), not a fixture stand-in.

**`security.semgrep`'s fixture/production interplay is an explicit,
committed decision, not left to semgrep's own defaults.** The check's argv
(`--no-git-ignore --json --error --severity ERROR .`) only disables
semgrep's automatic `.gitignore` consultation — it says nothing about
whether the nine deliberately-vulnerable
`tests/fixtures/semgrep/<rule-id>/positive*` fixtures are in or out of
scope, and leaving that to chance means either they are silently invisible
(if semgrep happens to default-ignore `tests/`) or they FAIL the production
scan the instant semgrep is provisioned (C7) — a false positive against
this repo's own conformance fixtures, not a real finding. The repo root
`.semgrepignore` (a semgrep-native mechanism, unaffected by
`--no-git-ignore`) excludes exactly `tests/fixtures/semgrep/` and nothing
else under `tests/`; `test_semgrepignore_excludes_only_the_fixture_directory`
pins the pattern list to that one entry.

**Store divergence for `dependency.lock.node`/`package.node-runtime`/
`dependency.audit.*` is disclosed debt, not silent drift.** Unlike
`types.python`/`security.semgrep` above, `dependency_checks.py` resolves
`npm`, `node`, `uv`, and `pip-audit` from ambient `PATH` via `shutil.which`
— the same trust class `packages.py::_uv` already uses for
`dependency.lock.config`/`dependency.lock.delegate`, not a new gap this
chunk introduces. Concretely: `dependency.lock.node`'s PASS on a
provisioned developer host is verification against that host's own `~/.npm`
cache, not a hash-verified store entry the way `types.python`'s pyright
resolution is. Bringing `npm`/`node`/`uv`/`pip-audit` into the store is
tracked for a future chunk; until then this is a known, accepted
inconsistency with the store-resolution table above, not something silently
different from it.

**No TypeScript compiler check — data-backed, not just asserted.** There is
no TS project: `plugins/stitch-design/runtime/node` is `build.mjs`
(esbuild/Babel bundling) with zero `.ts`/`.tsx` sources and no
`tsconfig.json`; the repo's only tracked `tsconfig.json` is a
project-scaffold **template** for other people's future projects, not this
repo's own build. `test_dependency_checks.py::
test_no_tsconfig_json_tracked_means_no_typescript_compiler_check` asserts
`git ls-files '*tsconfig.json'` has no non-template hit and that no
registered check argv names `tsc` — if a real `tsconfig.json` ever lands,
this test starts failing, which is the intended signal to re-evaluate the
decision rather than silently staying green.

## Coverage limits

`config/project-checks.json` still carries a nonempty `coverage_pending` list
for **every** profile and group as of this writing (Phase 3 —
deterministic-candidate provisioning, action/runtime tool-pin equivalence,
and interpreter provenance — has not run). Concretely:

- Running any profile today returns `BLOCKED`, not `PASS`, because every
  check in every group has at least one open `coverage_pending` obligation.
- No shared command has been promoted to a required status, and no legacy CI
  job or pre-commit hook body has been removed or weakened.
- No local pre-commit hook body has been migrated to the shared entry:
  the preservation comparison (`config/check-preservation.json`, the
  immutable Task 1 oracle) maps every existing hook to a `check_id`, but
  since every one of those `check_id`s is currently `coverage_pending`,
  migrating the hook body today would turn a working hook into one that
  always reports `BLOCKED`. Task 9 leaves every `.pre-commit-config.yaml`
  hook body as-is until Phase 3 clears its group's `coverage_pending` list.
- Publication controls (`disposition: "publication"` in
  `config/check-preservation.json`) never appear in any profile's check
  closure — release verification and release publication remain distinct,
  and this repo's automation never executes publication.

Phase 3 is the prerequisite for promoting any shared command past shadow
status. Phase 5 separately enables protected required statuses once
promotion has happened.

## Related Documents

- [SHARED_CHECKS_HOOKS.md](SHARED_CHECKS_HOOKS.md) — native hook adapters (`manifest hook <client> <event>`), split out here since this file is already over its line cap
- [SHARED_CHECKS_TELEMETRY.md](SHARED_CHECKS_TELEMETRY.md) — run telemetry (`runs.jsonl`) and `measure_report.py`'s derived metrics, split out for the same reason
- [config/project-checks.json](../config/project-checks.json) — the check registry
- [config/check-preservation.json](../config/check-preservation.json) — the immutable Task 1 oracle
- [.github/workflows/ci.yml](../.github/workflows/ci.yml) — legacy jobs + shadow path
- [README.md](README.md) — documentation index
