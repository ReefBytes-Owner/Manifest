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
itself): it never raises. `checks/cli.py`'s `check` command and
`hooks/core.py`'s `process_event` call it unconditionally right before
reporting their own result — an unwritable telemetry directory changes
nothing about the check's exit code or the hook's `AdapterOutcome`
(`tests/python/manifest_agent/test_check_cli_telemetry.py::
test_telemetry_write_failure_does_not_change_the_exit_code_or_report`).

Writes are append-only (`open(..., "a")`, `fcntl.flock`-serialized the same
way `hooks/receipt.py`/`preparation.py` already serialize writes) and
confined to `telemetry.telemetry_dir()` — resolved from `XDG_STATE_HOME`,
never inside the repository or a candidate checkout, and never a network
call or paid telemetry service.

## Two sources per hook run

A `manifest hook` invocation that actually executes a check produces **two**
records: the inner `manifest check` subprocess's own (profile-level) record,
and the adapter-level one `hooks/telemetry.py` writes, which is the only one
carrying `runtime.client`. `hooks/runner.py` forwards `XDG_STATE_HOME` to the
inner subprocess precisely so both land in the same file.

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
