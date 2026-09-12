# Shared Checks

> The `manifest check` / `manifest check-aggregate` shared-check entry:
> commands, profiles, status vocabulary, and what is not authoritative yet.

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
- **First-time environment attestation**: an explicit `Manifest CI`
  `workflow_dispatch` job materializes the
  four environment bundles and npm cache on `linux-x64`. Its JSON outcomes
  include each computed `digest`, even while the corresponding lock field is
  `null`; checks remain BLOCKED until a maintainer reviews the artifact and
  commits those values to `config/toolchain.lock.json`. Binary bundles still
  refuse a missing `exe_sha256`.
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

```text
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
| `lint.shell.scripts`, `lint.shell.bootstrap`, `hook.shellcheck` | `distribution:shellcheck-py=0.11.0.1;command:shellcheck=0.11.0` | `command:shellcheck=0.11.0` | **Attested for `linux-x64` and `darwin-arm64` (C7) from the pinned release archives; passes once `manifest provision` has populated the store.** Body resolves `store:shellcheck/bin/shellcheck` via `tools/project_checks/toolchain_resolve.py`; the registry-level version probe (drift detection only, not a security control) is unchanged. |
| `hook.shfmt` | `"ok"` (unpinned placeholder) | `command:shfmt=3.13.1`; body argv is `-d` (check-only), never `-w` | **Attested for both platforms (C7); BLOCKED only until `manifest provision` runs.** Body resolves `store:shfmt/bin/shfmt`. |
| `lint.yaml.config`, `hook.yamllint` | `distribution:pyyaml=6.0.2;distribution:yamllint=1.38.0;command:yamllint=1.38.0` | `distribution:yamllint=1.38.0` | **Environment attested for both supported platforms; BLOCKED only until `manifest provision` runs.** Body resolves `store:python-env/bin/yamllint`; the registry version probe now runs under `store:python-env/bin/python`. |
| `hook.markdownlint-cli2` | `command:markdownlint-cli2=0.23.0` | `command:markdownlint-cli2=0.23.2` | **The local pin tracks markdownlint-cli2-action v24.2.0's bundled engine; the updated environment is attested for both supported platforms.** Direct-argv check; registry `tool.executable` and `check.argv[0]` are both `store:node-env/bin/markdownlint-cli2` (same generic runner rewrite `test.bats` already used — no wrapper body needed). |
| `test.bats` | `./node_modules/.bin/bats` | `command:bats=1.11.1`; argv is `store:node-env/bin/bats` | Yes (unchanged from before this chunk). |
| `test.bundle-partition` | `"ok"` + hardcoded BLOCKED (`npx` control) | `command:bats=1.11.1`; runs `tests/bats/bundle_partition.bats` | **Environment attested for both supported platforms; BLOCKED only until `manifest provision` runs.** Body resolves `store:node-env/bin/bats`; the preflight now names `--executable store:node-env/bin/bats` explicitly (C7c). |
| `hook.gitleaks` | `command:gitleaks=8.30.0` | `command:gitleaks=8.30.1` | **Attested for `linux-x64` and `darwin-arm64` (C7) from the pinned release archives; passes once `manifest provision` has populated the store.** Body resolves `store:gitleaks/bin/gitleaks` via `tools/project_checks/toolchain_resolve.py`; C2b left this on `shutil.which("gitleaks")` (out of that chunk's explicit scope, created by C6b afterwards), which C2c's audit caught and closed. |
| `dependency.lock.config`, `dependency.lock.delegate`, `dependency.lock.root`, `package.coordinator`, `package.config`, `package.release-archive`, `package.release-manifest` | `command:uv=0.12.6` | unchanged | **Attested for both platforms (C7); BLOCKED only until `manifest provision` runs.** Body resolves `store:uv/bin/uv` via `packages.py::_uv`; the preflight now names `--executable store:uv/bin/uv` explicitly (C7c) instead of trusting whatever `uv` was first on `PATH`. |
| `dependency.lock.node`, `package.node-runtime` | `"ok"` | unchanged | **`node` attested for both platforms (C7); `store:node/bin/npm` still has no store entry of its own** — `package.node-runtime`'s own isolated-copy flow shells `npm` from the tracked project, unaffected by C7b (which extracts `npm-cli.js` only for `node-env` materialization, not as a general store executable — see C7b above). Body resolves `store:node/bin/node` / `store:node/bin/npm` via `dependency_checks.py`. |
| `hook.ruff`, `hook.ruff-format` | `distribution:ruff=0.15.20` | unchanged | **Environment attested for both supported platforms; BLOCKED only until `manifest provision` runs.** Direct-argv checks; `tool.executable`/`check.argv[0]` are `store:python-env/bin/ruff`; the registry version probe runs under `store:python-env/bin/python`. |
| `hook.eslint` | `command:eslint=9.18.0` | unchanged | **Environment attested for both supported platforms; BLOCKED only until `manifest provision` runs.** Direct-argv check; `tool.executable`/`check.argv[0]` are `store:node-env/bin/eslint`. |
| `test.python`, `test.hooks` | `distribution:pytest=8.3.4` | unchanged | **Environment attested for both supported platforms; BLOCKED only until `manifest provision` runs.** Direct-argv checks; `tool.executable`/`check.argv[0]` are `store:python-env/bin/pytest`; the registry version probe runs under `store:python-env/bin/python`. |
| `hook.check-yaml`, `hook.check-json`, `hook.check-added-large-files`, `hook.check-case-conflict`, `hook.check-merge-conflict`, `hook.check-executables-have-shebangs`, `hook.check-shebang-scripts-are-executable`, `hook.detect-private-key`, `hook.check-ast`, `hook.debug-statements` | `distribution:pre-commit-hooks=6.0.0` | unchanged | **Environment attested for both supported platforms; BLOCKED only until `manifest provision` runs.** Direct-argv checks; each `tool.executable`/`check.argv[0]` is `store:python-env/bin/<console-script>` and its version probe runs under the same environment's Python interpreter. |
| `hook.trailing-whitespace`, `hook.end-of-file-fixer`, `hook.mixed-line-ending` | `distribution:pre-commit-hooks=6.0.0` | unchanged | **Environment attested for both supported platforms; BLOCKED only until `manifest provision` runs.** Wrapper body: `hooks.py::_pinned_fixer` delegates to `tools/project_checks/hook_fixers.py::run`, which resolves `store:python-env/bin/<console-script>` and then re-applies the same pinned-distribution/entry-point/source-provenance checks as before against the resolved executable. |

**Honesty caveat (updated, C2c)**: every engine-bearing check body under
`tools/project_checks/` now resolves its engine through the hash-verified
toolchain store — C2b landed 8 (plus the `uv`/`node` family), C2c closed the
remaining gap: `hook.ruff`/`hook.ruff-format`, `hook.eslint`,
`test.python`/`test.hooks`, the ten bare pre-commit-hooks console scripts
(`check-yaml` … `debug-statement-hook`), the three pinned fixers
(`trailing-whitespace`, `end-of-file-fixer`, `mixed-line-ending`, moved into
the new `tools/project_checks/hook_fixers.py`), and `hook.gitleaks`
(`gitleaks_check.py`, left on `shutil.which` by C2b/C6b and caught by C2c's
own audit of `tools/project_checks/*.py`). Every migrated check is
`BLOCKED: toolchain: <tool> unattested for <platform>` (or `not provisioned`,
for the provisioner-kind gaps above) until C7 fills in real hashes and
implements the `python-env`/`node-env` kinds — that is the intended, honest
result: a receipt that used to say PASS about an unverified tool now says
BLOCKED about the same tool, truthfully.

**C7 (binary hash-fill, both platforms).** Every `binary`-kind tool in
`config/toolchain.lock.json` — `gitleaks`, `shfmt`, `shellcheck`, `uv`,
`node` — is now attested for **both** `linux-x64` and `darwin-arm64`. Each
`sha256` is the digest of the archive at the entry's own `url`, cross-checked
against the publisher's checksum manifest or GitHub's release asset digest;
each `exe_sha256` is the digest of the file extracted at `path_in_archive`
(for the raw-binary `shfmt` download the two coincide by construction). A
real `manifest provision --platform <p>` download was run for both platforms:
all five provision, `--offline` reports `complete`, and the extracted
darwin-arm64 executables report exactly the pinned versions. **This corrects
C7-partial**, which had recorded the locally installed Homebrew builds of
`gitleaks`/`shellcheck` (same version string, different bytes from the
release artifacts) and had reused the binary hash as the archive `sha256`, so
a real download would have BLOCKed on "digest mismatch"; the reviewed pins
themselves did not change. `tests/python/manifest_agent/test_toolchain_c7_attestation.py`
pins the invariant (archive digest ≠ executable digest for tar entries) and,
when a Homebrew `gitleaks 8.30.1` with different bytes is present, proves
`--import` of that same-version-different-build is still BLOCKed.

**C7b (`python-env`/`node-env` provisioner kinds).** `_IMPLEMENTED_KINDS` now
includes `python-env` and `node-env`. `config/toolchain/pyproject.toml` +
`config/toolchain/uv.lock` and `config/toolchain/package.json` +
`config/toolchain/package-lock.json` pin the exact tool versions the
registry names (`ruff==0.15.20`, `pytest==8.3.4`, `pre-commit-hooks==6.0.0`,
`yamllint==1.38.0`+`pyyaml==6.0.2`, owner-confirmed
`semgrep==1.176.1`, `pip-audit==2.10.1`, and `pyright==1.1.414`,
`eslint==9.18.0`, `markdownlint-cli2==0.23.2`, `bats==1.11.1`). A pyright
bump also moves the `types.python` ratchet baseline, so it belongs in a
reviewed PR with that baseline re-measured; automation must not advance it.
The lock's `python-env`/`node-env` `url` points at the lockfile itself
(`file://config/toolchain/uv.lock` / `file://config/toolchain/package-lock.json`);
`sha256` is that lockfile's own digest.

Materialization uses only store-attested engines, never anything ambient:
`store:uv/bin/uv sync --locked --no-dev --project config/toolchain`
(`UV_PROJECT_ENVIRONMENT` redirected into the store) for `python-env`;
`store:node/bin/node <npm-cli.js> ci` for `node-env`, where `npm-cli.js` and
its own bundled dependencies are extracted straight out of the SAME
hash-verified `node` archive the `node` bundle already trusts (the "extract
an additional path from an already-downloaded archive" option Correction 3
offered, rather than adding a schema `extra_paths` field and a general
`bin/npm` store entry — simpler and equally sound, at the cost of `npm`
existing only inside the `node-env` materialization, not as its own store
executable). The venv's own interpreter (`bin/python`) is the ambient
`python3` — out of scope for pinning by design (Correction 3, rule 2) — so
`bin/python` joins `python-env`'s `console_scripts` and every
`distribution-version` probe for a python-env tool now runs as
`store:python-env/bin/python -I tools/project_checks/tool_versions.py
distribution-version <dist>` instead of the ambient interpreter, so the
version actually reported is the one the check body will really run.

**The trust anchor for env kinds is the installed DISTRIBUTION SET's digest,
not any one console script's bytes** (Correction 3, rule 3): a generated
launcher's bytes embed an absolute, store-location-dependent path and can
never match a value committed ahead of provisioning. `exe_sha256` is the
sha256 over every installed distribution's `*.dist-info/RECORD` (python-env)
or the canonical JSON of `node_modules/.package-lock.json`'s `packages`
object (node-env) — computed by `toolchain_env.distribution_set_digest`, a
pure function of on-disk bytes with zero network/lock/store-manifest
dependency, which is what makes it independently testable against a
hand-built fake env. RECORD entries pip/uv generate OUTSIDE site-packages
(`../../../bin/<name>,sha256=...,size` — the console-script launchers
themselves) are excluded from the digest for the same reason `bin/python`'s
bytes are out of scope: including them made the digest non-reproducible
across two otherwise-identical materializations at different store
locations, which was caught and fixed during this session's real
attestation run (see below). `toolchain.resolve()` for an env bundle now
checks, in order: (a) store staleness (existing `source_sha256` check); (b)
the recomputed distribution-set digest against the lock's `exe_sha256` →
`digest mismatch` on any difference; (c) the requested console script exists
under the env's `bin/`; (d) its launcher (a `#!` shebang, or the real target
of a symlink — e.g. every `node_modules/.bin/*` entry `npm ci` writes)
resolves to somewhere inside the store → anywhere else is `digest mismatch`.
`bin/python` itself is exempt from (d) — it is legitimately a symlink to the
ambient/uv-managed interpreter by design; every OTHER console script's
shebang names `bin/python`, which IS inside the store, so this exemption
cannot smuggle an outside launcher past the check for anything else.

**Real attestation, this session (darwin-arm64 only; network available).** A
real `store:uv/bin/uv sync` and a real `store:node/bin/node <npm-cli.js> ci`
were run through the actual provisioner against the committed lockfiles;
`python-env`'s `exe_sha256` is `71f38158fabe4ca940f0a9638eeac43bf9f0d3e87d8442f675e821e81af329a3`,
`node-env`'s is `a6158fdd7343c327dd071389818dc4fbcc5381aae7116107909edeeb176288fd`.
Both were independently re-derived from a SECOND, differently-rooted
materialization and matched exactly (after the RECORD-launcher-exclusion
fix above), confirming location-independence rather than assuming it. A real
`manifest provision --lock config/toolchain.lock.json --store <tmp>
--platform darwin-arm64` provisions all seven bundles (five binaries plus
both env kinds) in one pass; `--offline` against that same store reports
`complete`. **`linux-x64` env hashes stay `null`** — attesting them requires
a real materialization on that platform, which this session cannot do; the
procedure is: run the same `manifest provision --platform linux-x64`
sequence in CI, read the `exe_sha256` values `distribution_set_digest`
computed there out of the store (or a small script calling it directly), and
commit them in a reviewed PR — never copy darwin's values across platforms.

**C7c (preflight targets the store engine, not `PATH`).** A handful of
checks whose body itself resolves a store engine (`hook.shfmt`,
`hook.gitleaks`, `hook.shellcheck`/`lint.shell.scripts`/`lint.shell.bootstrap`,
`test.bundle-partition`, and the seven `uv`-driven `python-wrapper` checks —
`dependency.lock.{root,config,delegate}`,
`package.{coordinator,config,release-archive,release-manifest}`) kept
`tool.executable` as the repo-owned `python3` wrapper, so
`toolchain.resolve_for_preflight` never resolved a `store:` reference for
them at all — the version PREFLIGHT silently trusted whatever `uv`/`shfmt`/
`gitleaks`/`shellcheck`/`bats` happened to be first on `PATH`, even though
the check BODY was already correctly store-resolved. `version_argv` now
names the engine explicitly (`--executable store:uv/bin/uv` /
`--executable store:shfmt/bin/shfmt` / etc.); `resolve_for_preflight`
resolves every distinct `store:` token found in `tool.executable` OR
`version_argv` (not just `tool.executable`), BLOCKs the whole preflight if
any of them fails to resolve, and merges their bin dirs into the child
`PATH` (store first, then `os.defpath` — never the caller's `PATH`).
`tool_versions.py::_resolved_executable` now accepts an absolute
`--executable` path (only ever supplied pre-rewritten by
`toolchain.rewrite_argv` from a hash-verified store reference) and never
falls back to a `PATH` search when that explicit executable is missing.
Proven with a real PATH impostor in
`tests/python/manifest_agent/test_toolchain_c7c_preflight.py`: a fake `uv`
reporting a wrong version sits first on `PATH`, and the probe still reports
the real store `uv`'s version.

**C2c's registry guard (non-reopenable).**
`tests/python/manifest_agent/test_toolchain_registry_guards.py` now asserts
two properties so this gap cannot silently reopen: (1) every real-registry
`tools[].executable` is either a `store:` reference or on the narrow
`is_legal_plain_executable` allow-list (`python3`, `bash`, repo-relative
scripts — plus the two dormant, zero-input `hook.cargo-*` checks, pinned
separately by `test_registry_dormant_cargo_checks_never_select_inputs`); (2)
an AST scan of every `tools/project_checks/*.py` source file finds zero
`shutil.which(...)` call sites outside an explicit, justified four-entry
allow-list (`generated.py::_cursor_preflight` and
`structure.py::_shell_syntax` — always-present `bash`/`python3`;
`dependency_checks.py::_which` — used only by the disabled, unwired
`dependency.audit.*` bodies (C8); `tool_versions.py::_resolved_executable` —
the shared version-probe adapter, which never opens a second PATH because it
always runs inside whatever PATH the caller already restricted). A check
body added later that imports `shutil` and calls `.which("some-new-engine")`
fails test (2) immediately, by name, without needing any registry knowledge.

**C7d (`python3` means the runner's interpreter; caches redirected outside
the candidate).** C7c's honest child PATH (store bin dirs + `os.defpath`)
exposed two latent defects once a real store let bodies actually run: (1) a
bare `python3` token resolved to whatever interpreter `os.defpath` found
first (macOS system Python 3.9), not the interpreter running `manifest
check` itself (the project's 3.11+ venv) — bodies importing `manifest_agent`
died on a missing stdlib symbol (`datetime.UTC`); (2) CPython wrote
`__pycache__` into the candidate for every body that imported a module from
it, and the runner's strict identity check correctly reported "candidate
identity changed" for the resulting mutation. Fix, in `toolchain.py` /
`toolchain_cache.py`: `resolve_interpreter_argv` rewrites every literal
`python3` token (in a tool's `executable`, a check's `argv[0]`, or any
`version_argv` token) to `sys.executable` — never a `PATH` search; `bash`
is unaffected. `run_profile` wraps every run in `run_cache_directory()`, one
`tempfile.mkdtemp()`-created directory outside the candidate and the
repository checkout, removed unconditionally (`try`/`finally`) even if a
check raises; `cache_environment` overrides (never merely forwards)
`PYTHONDONTWRITEBYTECODE`, `PYTHONPYCACHEPREFIX`, `XDG_CACHE_HOME`,
`RUFF_CACHE_DIR`, `UV_CACHE_DIR`, `npm_config_cache`, and appends
`-p no:cacheprovider` to `PYTEST_ADDOPTS` — the caller's own values for these
keys are never honored, because the identity check must not depend on
whatever the caller's ambient environment happened to forward. The identity
check itself is untouched and stays strict: a body that writes a real file
into the candidate still BLOCKs "candidate identity changed"
(`tests/python/manifest_agent/test_toolchain_c7d_interpreter_and_cache.py`).

Measured (`manifest check full --base HEAD~1`): without a store,
`Counter({'BLOCKED': 41, 'PASS': 22, 'NOT_APPLICABLE': 12})`, zero FAIL,
zero identity-changed (previously 5–9 on this same tree). With a freshly
provisioned `darwin-arm64` store, `lint.shell.*` and the other C7c-fixed
checks PASS cleanly and identity-changed is 0 across every group *except*
`test.bats`: that check's own body runs `git status`/`git diff` inside the
candidate as part of the bats suite, which causes git to rewrite
`.git/index`'s stat-cache bytes (same length, different content) with no
logical change — a pre-existing flake (documented in the C7d ledger entry as
the same mechanism as an earlier 5–9-count flake) that predates this chunk
and was simply never reachable before, because `test.bats` was BLOCKED
"unattested" until C7 provisioned a store. It is out of C7d's scope
(interpreter + cache redirection only) and is reported here rather than
silently fixed. Excluding `test.bats`, a full store-backed run reports
`Counter({'PASS': 45, 'NOT_APPLICABLE': 12, 'FAIL': 9, 'BLOCKED': 8})`: the
FAILs are real tool findings now reachable for the first time
(`types.python`, `security.semgrep`, `hook.shfmt`/`hook.check-yaml`/
`hook.check-json`/`hook.check-executables-have-shebangs`/
`hook.markdownlint-cli2` all flagging real issues in `docs/SHARED_CHECKS.md`
and elsewhere), and the BLOCKEDs are separately-scoped provisioning gaps
(`package.node-runtime`/`dependency.lock.node` need a `bin/npm` store entry
noted under C7b; `lint.markdown.keydocs` needs the action-pin confirmation;
`test.smoke.lite`/`generated.cursor`/`hook.check-cursor-rules-drift` are
unrelated pre-existing gaps).

**C7e (candidate `.git` identity compares `index` logically, not by raw
bytes).** The `test.bats` flake C7d reported but left out of scope: its body
runs `git status`/`git diff`, which rewrites `.git/index`'s on-disk stat
cache (ctime/mtime/ino/size/flags) with no logical change to what is staged.
Both places the candidate's `.git` identity was checked byte-compared that
file wholesale, so the refresh alone reported "candidate identity changed"
for `test.bats` — and because `_identity_error` re-validates the stored
digest before *every* check, one such run degraded the entire 75-check
profile to BLOCKED. Fix: `.git/index`'s identity is now the logical content
of `git ls-files --stage -z` (mode, blob sha, stage, path) — a body that
adds, removes, stages or unstages a path still changes this value and BLOCKs;
a stat-cache-only refresh does not. Nothing else is relaxed: every other file
under `.git` (refs, `HEAD`, `objects`, any file a body writes there directly)
and the entire working tree stay byte-compared. Applied in both places `.git`
identity is established: `candidate.py::_snapshot` (feeds
`candidate_digest`, stored in `.git/candidate-state.json` and re-checked by
`runner._identity_error` before and after every check) now derives its
`entries` field from `git ls-files --stage -z` instead of also hashing the
raw `.git/index` file bytes (the two were redundant — `entries` already
captured the logical content; only the raw-bytes duplicate was stat-cache
sensitive), and `runner.py`'s per-check before/after walk of `candidate.root
/ ".git"` now goes through the new `candidate.git_dir_snapshot`, which walks
`.git` byte-for-byte except substituting `index`'s entry with the
`ls-files --stage -z` hash. Proven with real subprocesses, real git and a
real disposable candidate
(`tests/python/manifest_agent/test_toolchain_c7e_git_identity.py`): a body
running `git status`/`git diff` is not identity-changed, and does not BLOCK
a later check in the same profile run; a body that `git add`s a new path,
`git rm --cached`s a tracked path, mutates a tracked file's bytes, or writes
any other file under `.git` still BLOCKs "candidate identity changed"; and
`candidate_digest` is byte-identical before and after a `git status` inside
the candidate.

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

Five new checks (`config/project-checks.json`). At the time this chunk
landed, **none of them could PASS locally**: pyright, semgrep, and pip-audit
were not installed, and the toolchain store was unprovisioned
(`exe_sha256: null`, C7). `pyright` and `semgrep` are now attested for
`darwin-arm64` via `node-env`/`python-env` (C7b) — `types.python` and
`security.semgrep` can PASS on either supported platform once `manifest
provision` has populated the store. `pip-audit`'s two audit checks remain
outside the required `full` profile (C8). `hook.pyright` (PATH, unpinned) is
removed the same change that adds `types.python`.

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

## C7g: provision failure modes and one more debt-ratchet fix

Two defects the controller's clean-checkout measurement of C7f caught, closed
by C7g:

1. **`tools/project_checks/packages.py` and `src/manifest_agent/checks/
   path_filters.py` were themselves new debt** (C7f had pushed the former to
   459/500 lines and left a 17+-line literal suffix/type table in the
   latter). `path_filters.py`'s `VALID_PATH_TYPES` / suffix-tag table now
   loads from `config/path-types.json` (schema-checked against
   `config/path-types.schema.json`), and `packages.py`'s uv/build-backend
   resolution seam — `uv()`, `build_python()`, `backend()`,
   `build_destination()`, `revalidate_destination()`, `wheel_contains()` —
   moved to `tools/project_checks/packages_build.py`. Neither module's own
   `BlockedError` type forked: `packages.py` aliases it from
   `packages_build`, so `except BlockedError` in `main()` still catches
   both seams with one handler.
2. **`manifest provision` must BLOCK on a missing/unreadable `file://` lock
   source or a failed materialization subprocess, never crash.**
   `toolchain_provision.py::_read_source_bytes` and
   `toolchain_materialize.py`'s `_run()`/node-env project-file reads used to
   let a raw `OSError`/`subprocess.TimeoutExpired` escape as a traceback —
   exactly what happened on a fresh checkout before
   `config/toolchain/package-lock.json` was committed. Every one of those
   paths now raises a typed error the caller turns into
   `ProvisionOutcome(bundle, "blocked", reason)`, so the CLI still exits 3
   with the reason in the JSON report.
   `test_every_file_url_lock_source_is_git_tracked`
   (`tests/python/manifest_agent/test_toolchain_registry_guards.py`) asserts
   every `file://` source `config/toolchain.lock.json` names is
   `git ls-files --error-unmatch`-tracked, so an ignored lock source cannot
   silently recur the way `package-lock.json` did.

Also folded in from the controller's own clean-store measurement at
`b26d1495`: `tools/project_checks/*.py` are launched exclusively as
`python3 <path>` by the registry (never executed directly), so their
shebang lines — stale from before that convention was enforced, and the
reason `hook.check-shebang-scripts-are-executable` FAILed once C7f made
candidate file modes honest — were removed rather than chmod'd +x; and
`configs/claude/scripts/agents/orchestrator.py`'s Gemini credit-check path
now `assert genai is not None` before use (`genai` is `ModuleType | None`
at its `config.py` import site; `HAS_GENAI`/`HAS_GENAI_NEW` are the runtime
guarantee pyright cannot follow across the module boundary without an
explicit narrowing assert — never a `# type: ignore`).

**Not completed in this pass** (scope beyond the above two defects):
selector `types`/`exclude` parity for `hook.ruff`/`hook.ruff-format`/
`hook.check-ast`/`hook.debug-statements`/`hook.shfmt`/`hook.check-yaml`/
`hook.check-json`; `bin/npm` as a store-attested extra executable; and the
`generated.cursor`/`hook.check-cursor-rules-drift` 20-second timeouts. A
direct `types.python` run (`store:node-env/bin/pyright` against this
checkout's real dependency set) also surfaced roughly 700 findings across
files this chunk never touched — far more than the single
`orchestrator.py:672` finding reported upstream — which needs the
controller's own re-measurement to reconcile before anyone treats
`types.python` as close to green.

## C7h: node's `bin/npm`, and the test group's project environment

Two more C7g leftovers closed, one carried forward.

**`node` gains `bin/npm` (`extra_executables`, schema-checked).** Extracted
from the SAME hash-verified node archive as `bin/node` (no second download),
independently hashed against `npm-cli.js`'s own sha256 — never against
`node`'s `exe_sha256` — and recorded with `node`'s own `bin/node` as its
`interpreter`, so `npm`'s resolved child `PATH` always carries the store's
node first. `dependency_checks.py`'s `_npm()`/`_node()` already called
`store:node/bin/npm`; both were BLOCKED "unattested" until this landed. A
real `manifest provision` + `store:node/bin/npm` run with the store's own
node invoked `npm-cli.js` end to end (reported version `11.6.0`).

**`generated.cursor` / `hook.check-cursor-rules-drift`'s 20-second timeout.**
Root cause, isolated by measuring the runner's three cache env vars
individually, in pairs, and together against the unmodified generator:
`PYTHONDONTWRITEBYTECODE` and `PYTHONPYCACHEPREFIX` TOGETHER (never alone,
and `XDG_CACHE_HOME` is inert) roughly triple `generate_cursor_rules.sh`'s
wall time, because its per-skill loop (~123 skills) launched up to two
`python3` processes each. `cursor_rules_model_guidance.py` now does that
same work in ONE `python3` process; the shell script probes python3/pyyaml
once and reads the batch result from a `mktemp -d` (one file per skill —
not an associative array, since the script targets bash 3.2). Proved
byte-identical output regenerating the real repo's rules before/after;
real timings dropped from ~30s to ~2.5s under the runner's cache env, and
`manifest check` with a provisioned store now PASSes both checks in
~2.5s each.

**Carried forward: the test group's project environment (Correction 7).**
`project-env` (root `uv.lock`, `--no-install-project` so a check always
tests the CANDIDATE's own `src/` via `PYTHONPATH`, never a baked-in copy)
and `config-env` (`configs/claude/uv.lock`, installed for real so its
`bin/manifest` entry point exists) are attested for darwin-arm64 and
resolve through `toolchain.resolve()` exactly like every other `python-env`
bundle. Finding a workable materialization mechanism also surfaced a real
launcher-provenance gap: a store living under a long path (this repo's own
`pytest tmp_path`, CI runners) makes pip/uv emit a `#!/bin/sh` polyglot
trampoline instead of a plain shebang for `bin/manifest`, which
`launcher_target` didn't recognize and failed CLOSED on — fixed with a
narrowly-scoped parser for that specific shape, plus three regression
tests (resolves correctly, still rejects a trampoline pointing outside the
store, an ordinary `#!/bin/sh` script is untouched). **Not done**: wiring
`test.python`/`test.hooks`/`test.bats`/`test.smoke.lite` in the registry to
these two bundles, the `path_prepend` mechanism `test.bats` needs to put
`store:project-env/bin` first on its child `PATH`, the `test.bats` wall-time
budget measurement, and the PATH=empty / impostor-`python3` / stale-lock
functional tests against the real check bodies (the offline tests added
here cover the stale-lock invariant at the `resolve()` layer only).

## C7j: reproducible env digests, effective preparations, evidence-derived budgets

Three C7h/C7i leftovers closed.

**`distribution_set_digest` is now checkout-path independent.** `project-env`/
`config-env` are synced against the live checkout (Correction 7), and their
two local path dependencies (`manifest-model-policy`, `manifest-runtime`)
install editable — pip/uv's `direct_url.json`, `uv_cache.json`, and a
top-level `*.pth` file each embed the checkout's absolute path. Measured by
materializing both bundles twice on two real `git worktree`-detached
checkouts and diffing `RECORD` byte-for-byte: those three files (never a
payload line) were the only difference outside the already-excluded launcher
lines. `distribution_set_digest` now excludes them too; re-attested
darwin-arm64 `exe_sha256` for both bundles
(`project-env` `b1213d5a…`, `config-env` `d0844818…`) from a fresh
materialization, and proved `toolchain.resolve("store:project-env/bin/python", …)`
returns a `ResolvedTool` from a THIRD fresh store.

**`prepare.skill-mirror` now runs for the `test` group.** Its declared
`groups` were `["structure", "lint"]` — never `"test"` — so `_prepare_for_checks`'
group-intersection selection never ran it for a `--group test` invocation,
leaving `.apm/skills` empty for `test.bundle-partition`/`test.hooks` even
though the preparation existed. Added `"test"` to its groups. The
BLOCK-on-failed-preparation path (a check whose group has a failed
preparation is BLOCKED with the preparation's own diagnostics, never left to
FAIL on stale/empty output) already existed in `runner._run_check`; pinned
with a regression test, plus a test that runs the real preparation against a
real candidate and confirms the mirror is actually populated.

**Budgets and ceilings now share one evidence chain.** Each `test.*` check's
`timeout_seconds` is 2× its measured wall time under the runner, rounded up
to the minute, with a `budget_evidence` string (new optional schema field)
naming the measurement — `test.bats` reuses the controller's 456.8s/457.1s
bats-suite measurement (→ 960s); `test.python`/`test.hooks`/`test.smoke.lite`/
`test.bundle-partition` were each measured twice via runner-driven
single-check execution against a fresh darwin-arm64 store on 2026-09-10 (→
780s/60s/60s/60s). The group sums to 1920s, over the `test` CI job's prior
30-minute ceiling, so that job moved to 35 minutes on the same evidence;
`shadow-checks-test` becomes `ceil(1920/60)+5` = 37 minutes.
`test_timeouts_are_finite_and_fit_existing_group_ceilings` no longer compares
against hand-copied constants — it parses `ci.yml` and reads each group's
producer job's own `timeout-minutes` as the ceiling (`lint`/`test`/`validate`
→ `lint`/`test`/`structure` groups), so the registry and `ci.yml` cannot
silently drift apart.

**Real findings surfaced by a runner-driven `--group test` run against a
fresh store, once the above three were fixed** (not fixed here — reported):
`test.python` passes internally but the RUN is reported BLOCKED
"candidate identity changed" — a nested `materialize_candidate(REPO_ROOT, …)`
call inside a test that runs under `test.python` itself resolves `REPO_ROOT`
to the running candidate (the pattern predates this chunk, e.g.
`test_toolchain_c7i_functional.py`); `test.smoke.lite` BLOCKED the same way,
with a real finding underneath — several `delegate` smoke cases fail
"trusted Manifest runtime has an invalid manifest-model-policy distribution"
inside the candidate. `test.hooks` and `test.bundle-partition` are real,
clean PASSes. `test.bats` is a real FAIL (not BLOCKED) at 502s — the
digest/mirror/identity problems that used to mask its result are gone;
whatever `not ok` lines remain now are genuine.

## Related Documents

- [SHARED_CHECKS_HOOKS.md](SHARED_CHECKS_HOOKS.md) — native hook adapters
  (`manifest hook <client> <event>`), split out here since this file is
  already over its line cap
- [SHARED_CHECKS_TELEMETRY.md](SHARED_CHECKS_TELEMETRY.md) — run telemetry
  (`runs.jsonl`) and `measure_report.py`'s derived metrics, split out for the
  same reason
- [config/project-checks.json](../config/project-checks.json) — the check registry
- [config/check-preservation.json](../config/check-preservation.json) — the immutable Task 1 oracle
- [.github/workflows/ci.yml](../.github/workflows/ci.yml) — legacy jobs + shadow path
- [README.md](README.md) — documentation index
