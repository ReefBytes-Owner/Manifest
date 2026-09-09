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
the pre-existing `lint`/`test`/`validate` jobs. Each shadow job invokes the
shared command exactly once (`uv run manifest check full --group <group>
--json --output ...`) and uploads its report as a run-attempt-scoped artifact
(`shadow-receipt-<group>-${{ github.run_attempt }}`) so a workflow re-run
cannot mix evidence from a prior attempt. `shadow-checks-aggregate` runs with
`if: always()`, rejects any producer whose result is not exactly `"success"`
(an allow-list check, so a skipped or cancelled producer is rejected the same
as an explicit failure), then builds the current-run context
(`tools/project_checks/ci_context_cli.py`, read-only via `gh api`) and calls
`manifest check-aggregate`.

Every shadow job and the aggregate job set `continue-on-error: true` (or run
in `always()` mode) and hold read-only `permissions:` — the shadow path is
**never a required status** and cannot gate a merge. Branch protection is
untouched; that is a separate, later phase (Phase 5).

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
