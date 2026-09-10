# Shared Checks

> The `manifest check` / `manifest check-aggregate` shared-check entry: commands, profiles, status vocabulary, and what is not authoritative yet.

**Last Updated**: 2026-09-09

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
