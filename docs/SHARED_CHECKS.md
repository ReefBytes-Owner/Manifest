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
  (`config/project-checks.json` → `profiles`).
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
| `BLOCKED` | `3` | At least one check could not be verified (missing tool, unavailable candidate, unresolved `coverage_pending` obligation). |

`BLOCKED` means **"could not be verified"** — it is never a pass, even when
no check explicitly failed. When `FAIL` and `BLOCKED` coexist in the same
run, the report returns exit `2` (`FAIL`) and retains both sets of
diagnostics; a fail is never hidden behind a blocked result. A group result
is always partial and cannot certify a whole profile.

## The shadow CI path

`.github/workflows/ci.yml` runs `shadow-checks-structure`,
`shadow-checks-lint`, and `shadow-checks-test` alongside (never instead of)
the pre-existing `lint`/`test`/`validate` jobs. Each shadow job first resolves
a base revision (the PR base SHA for `pull_request` events, or a merge-base
against the default branch for `push` events) then invokes the shared command
exactly once (`uv run manifest check full --group <group> --project-config
config/project-checks.json --base <resolved-sha> --json --output ...`) and
uploads its report as a run-attempt-scoped artifact
(`shadow-receipt-<group>-${{ github.run_attempt }}`) so a workflow re-run
cannot mix evidence from a prior attempt. `shadow-checks-aggregate` runs with
`if: always()`, rejects any producer whose per-step outcome is not exactly
`"success"` (an allow-list check, so a skipped or cancelled producer is
rejected the same as an explicit failure), then builds the current-run
context (`tools/project_checks/ci_context_cli.py`, read-only via `gh api`)
and calls `manifest check-aggregate`, writing its own receipt
(`shadow-receipt-aggregate-${{ github.run_attempt }}`) and publishing the
verdict to the job summary (`$GITHUB_STEP_SUMMARY`) so it is visible without
opening step logs.

The rejection step reads each producer's `steps.shadow.outcome` **job
output**, not `needs.<job>.result`. All three producer jobs set job-level
`continue-on-error: true`, and GitHub reports a job that failed only because
of that job-level setting as `result: "success"` in the `needs` context of a
downstream job — so a `needs.*.result` check can never observe a failed
producer. Each producer job therefore exports its check step's `outcome`
(which continue-on-error does not rewrite) as a job output, and the aggregate
job reads that instead.

All four jobs (`shadow-checks-structure`, `shadow-checks-lint`,
`shadow-checks-test`, `shadow-checks-aggregate`) set job-level
`continue-on-error: true`. This is deliberately at the **job** level, not
just on individual steps: a step-only `continue-on-error` still lets an
unrelated step (checkout, `uv` install, context build, receipt download)
fail and redden the whole job — and therefore the workflow's overall
conclusion — which job-level `continue-on-error` prevents. Combined with the
aggregate job's `if: always()` and read-only `permissions:`, the shadow path
is **never a required status** and cannot gate a merge, and it cannot turn
the workflow conclusion red either. Branch protection is untouched; that is
a separate, later phase (Phase 5).

`manifest check-aggregate full` itself is also expected to report `BLOCKED`
today for a second, independent reason beyond `coverage_pending`: `full`'s
registry closure spans five groups (`structure`, `lint`, `test`, `security`,
`package`), but only three (`structure`, `lint`, `test`) have a shadow
producer job — `security` and `package` have none. `check-aggregate` reports
missing producer groups as `BLOCKED`, so a `BLOCKED` aggregate report is the
expected shape until a producer exists for every group `full` requires, not
just until `coverage_pending` clears.

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
| `lint.shell.scripts`, `lint.shell.bootstrap`, `hook.shellcheck` | `distribution:shellcheck-py=0.11.0.1;command:shellcheck=0.11.0` | `command:shellcheck=0.11.0` | No — `shutil.which("shellcheck")` against ambient `PATH`. |
| `hook.shfmt` | `"ok"` (unpinned placeholder) | `command:shfmt=3.13.1`; body argv is `-d` (check-only), never `-w` | No — `shutil.which("shfmt")` against ambient `PATH`. |
| `lint.yaml.config`, `hook.yamllint` | `distribution:pyyaml=6.0.2;distribution:yamllint=1.38.0;command:yamllint=1.38.0` | `distribution:yamllint=1.38.0` | No — `shutil.which("yamllint")` against ambient `PATH`. |
| `hook.markdownlint-cli2` | already `command:markdownlint-cli2=0.23.0` | unchanged (already engine-only, not touched this chunk) | No — `shutil.which("markdownlint-cli2")` against ambient `PATH`. |
| `test.bats` | `./node_modules/.bin/bats` | `command:bats=1.11.1`; argv is `store:node-env/bin/bats` | **Yes** — the only one of these checks whose executable is a `store:` reference. |
| `test.bundle-partition` | `"ok"` + hardcoded BLOCKED (`npx` control) | `command:bats=1.11.1`; runs `tests/bats/bundle_partition.bats` via `shutil.which("bats")`, no more `npx` | No — `shutil.which("bats")` against ambient `PATH`, unchanged trust class from before this chunk (it gained a real body, not store resolution). |
| `hook.gitleaks` | `command:gitleaks=8.30.0` | `command:gitleaks=8.30.1` (unified with CI's checksum-verified install and the lock's `binary` entry) | No — `shutil.which("gitleaks")` against ambient `PATH`. |

**Honesty caveat**: of the 9 engine-bearing checks this chunk's engine-pin
table touches, only **`test.bats`** actually resolves its engine through the
hash-verified toolchain store (3a). The other 8 still trust whatever
`shutil.which(...)` finds on `PATH`, gated only by the version-string probe
(exactly the pre-3a trust model — a launcher swapped for one that reports the
same version string still passes). This is not new, more `PATH` reliance:
these checks trusted `PATH` before this chunk too. It is disclosed here
because the engine-pin table above could otherwise read as "these checks are
now store-verified," which is true for exactly one of them.

**Why the other 8 are not `store:`-wired yet — the real blockers, not an
architecture limit.** Two things, both fixable, neither insurmountable:

1. `registry.py`'s `_validate_tool_reference` requires
   `check["argv"][0] == tool["executable"]` exactly. Checks wrapped through
   `python3 tools/project_checks/{structure,hooks}.py <id> --root .` have
   `argv[0] == "python3"`; migrating only `tool.executable` to `store:...`
   breaks that invariant (caught immediately by `test_check_registry.py`).
   For the direct-argv checks (`hook.shellcheck`, `hook.yamllint`,
   `hook.markdownlint-cli2`, `hook.gitleaks`) this is not a hard blocker —
   their argv is compared against `hooks.py`'s own `TASK7_DISPOSITIONS`
   table (a live Python dict this codebase owns and edits every chunk, most
   recently by this one for `hook.shellcheck`/`hook.yamllint` — see below),
   not against `config/check-preservation.json`'s frozen oracle. Rewiring
   them to `store:` form was mechanically possible within this chunk; it was
   deferred, not architecturally prevented.
2. **The deferral is deliberate, not an oversight**: every entry in
   `config/toolchain.lock.json` currently has `exe_sha256: null`
   (unattested — real hashes are C7, needs network). Routing a check through
   `toolchain.resolve()` against an unattested lock entry makes it
   `BLOCKED: toolchain: <tool> unattested for <platform>` unconditionally —
   converting a check that works today (PATH + version string) into one that
   can never pass until C7 lands. Store-wiring these 8 checks now would
   verify nothing while breaking them; it becomes meaningful the moment C7
   fills in real hashes. Tracked for that chunk, not this one.

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

- [config/project-checks.json](../config/project-checks.json) — the check registry
- [config/check-preservation.json](../config/check-preservation.json) — the immutable Task 1 oracle
- [.github/workflows/ci.yml](../.github/workflows/ci.yml) — legacy jobs + shadow path
- [README.md](README.md) — documentation index
