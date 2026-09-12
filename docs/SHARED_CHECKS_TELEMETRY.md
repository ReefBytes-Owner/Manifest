# Shared Checks — run telemetry and measurement

> One append-only record per `manifest check` / `manifest hook` / CI run, and
> a read-only report deriving attempts, repair cycles, check duration, review
> time, and cost per accepted change. Split out of
> [SHARED_CHECKS.md](SHARED_CHECKS.md), which is already over its line cap.
> Implements phase-3-5-decisions.md section 5c (chunk C11).

## What this is

`src/manifest_agent/checks/telemetry.py` writes one JSON line per run to
`$XDG_STATE_HOME/manifest/telemetry/runs.jsonl` (local) — or a CI run
artifact, since CI producer jobs invoke `manifest check` the same way and can
upload the same file. `src/manifest_agent/hooks/telemetry.py` adds one more
record per `manifest hook` invocation that actually ran a check, carrying the
adapter's client identity. `tools/project_checks/measure_report.py` reads the
JSONL back (plus `gh api`, injected, for review time) and renders the
derived metrics. Nothing here changes a verdict: telemetry is an observer.

## The record

```json
{"schema": 1, "ts": "2026-09-10T12:00:00+00:00", "profile": "full",
 "receipt_key": "…", "head_sha": "…", "candidate_lineage": "482",
 "attempt": 3, "status": "FAIL", "duration_seconds": 412.3,
 "model_id": "unknown", "runtime": {"client": "claude_code", "version": "unknown"},
 "cost": {"status": "unknown"}}
```

- `candidate_lineage` is a PR number when `GITHUB_REF` is a merge ref
  (`refs/pull/<n>/merge`), else the branch name (`GITHUB_REF_NAME` in CI, or
  the source checkout's current branch locally), else `null` — never
  fabricated (`telemetry.resolve_lineage`).
- `attempt` is 1 + the count of prior records sharing the same
  `candidate_lineage` in the same JSONL (`telemetry.count_prior_attempts`).
  A `null` lineage cannot be tracked across attempts and is always `1`.
- Free-text fields (`head_sha`, `candidate_lineage`) are passed through the
  shared `manifest_agent.process.redact_text` helper before the record is
  built — the same redaction receipts and diagnostics already use, not a new
  implementation.

## Unknown is never zero

`model_id`, `runtime.version`, and `cost` default to the literal string
`"unknown"`. Adapters fill them **only** from fields the client actually
supplies — never inferred, never guessed. `cost.status` is `"known"` only
with a provider-reported usage figure (`telemetry.CostInfo`); there is no
zero default. `measure_report.py`'s rendering never coerces an unknown value
into `0`/`$0.00` — see the headline tests in
`tests/python/manifest_agent/test_measure_report.py`.

## Write path never changes a verdict

`telemetry.record_run` is the single safety boundary for the whole write
path (lineage resolution, attempt counting, record assembly, the append
itself): it never raises. `checks/cli.py`'s `check` command calls it right
before reporting its own result -- unconditionally for a direct invocation,
skipped only when `MANIFEST_HOOK_ACTIVE` marks this as the inner process of
a hook-driven run (see "One record per hook run" below); `hooks/core.py`'s
`process_event` calls the adapter-level write unconditionally. Either way an
unwritable telemetry directory changes nothing about the check's exit code
or the hook's `AdapterOutcome`
(`tests/python/manifest_agent/test_check_cli_telemetry.py::
test_telemetry_write_failure_does_not_change_the_exit_code_or_report`). A
write failure is reported on stderr only, never stdout (protocol purity),
and never changes the exit code
(`tests/python/manifest_agent/test_check_telemetry.py::
test_a_write_failure_is_reported_on_stderr_never_stdout`).

Writes are append-only (`open(..., "a")`, `fcntl.flock`-serialized the same
way `hooks/receipt.py`/`preparation.py` already serialize writes) and
confined to `telemetry.telemetry_dir()` — resolved from `XDG_STATE_HOME`,
never inside the repository or a candidate checkout, and never a network
call or paid telemetry service.

## One record per hook run

A `manifest hook` invocation that actually executes a check writes exactly
**one** record: the adapter-level one `hooks/telemetry.py::record_hook_telemetry`
writes, carrying `runtime.client` (the identity the inner subprocess has no
way to know). `hooks/runner.py` still forwards `XDG_STATE_HOME` to the inner
`manifest check` subprocess so it writes to the same sink as a direct
invocation would -- but that inner process also inherits
`MANIFEST_HOOK_ACTIVE` (the same recursion marker `hooks/runner.py::RECURSION_ENV_VAR`
sets for the child), and `checks/cli.py::_record_check_telemetry` treats its
presence as "an adapter already owns this run's telemetry" and skips its own
write. Without this, attempts and repair cycles -- the two headline metrics
this chunk exists to produce -- double for every hook-driven run
(`tests/python/manifest_agent/hooks/test_hooks_telemetry.py::
test_a_hook_run_writes_exactly_one_record_total_not_a_second_inner_one`,
asserted over *all* records in the file, not a client-filtered subset).

## Derived metrics (`measure_report.py`)

Read-only over the JSONL. The one network-shaped input (PR review time) goes
through an injectable `gh api` fetcher (`make_fetch_review_time`), exactly
like `tools/project_checks/ci_context_cli.py` — tests supply fixtures,
`main()` shells out only when both `--repository` and `--review-pr` are given.

| Metric | Definition | Unknown handling |
|---|---|---|
| Attempts | Record count per `candidate_lineage` | n/a (always known) |
| Repair cycles | FAIL → re-run transitions per lineage (adjacent pairs by `attempt`) | n/a |
| Check duration | p50/p95 of `duration_seconds` across the corpus | `"unknown"`/`"unknown"` on an empty corpus |
| Review time | PR opened → first approval, via `gh api` | `"unknown"` unless a fetch was performed and both timestamps existed |
| Cost per accepted change | Sum of known costs across **all** attempts (including failed ones) for a lineage that reached `PASS` at least once | `known == total` renders the numeric sum; any uncosted attempt renders `"unknown (n of m attempts costed)"` — never a total that silently omits it |

```bash
python3 tools/project_checks/measure_report.py --runs-file \
  "$XDG_STATE_HOME/manifest/telemetry/runs.jsonl"
python3 tools/project_checks/measure_report.py --runs-file runs.jsonl --json
python3 tools/project_checks/measure_report.py --runs-file runs.jsonl \
  --repository owner/repo --review-pr 482
```

## Related Documents

- [SHARED_CHECKS.md](SHARED_CHECKS.md) — the parent document
- [SHARED_CHECKS_HOOKS.md](SHARED_CHECKS_HOOKS.md) — native hook adapters
- `src/manifest_agent/checks/telemetry.py` / `src/manifest_agent/hooks/telemetry.py` — implementation
- `tools/project_checks/measure_report.py` — the report CLI
