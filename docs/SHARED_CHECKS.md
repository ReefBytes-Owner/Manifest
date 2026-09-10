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
