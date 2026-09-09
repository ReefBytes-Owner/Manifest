# Shared Checks and CI Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose shared quick/full/security/release check profiles through the existing Manifest CLI and migrate local/CI callers without losing current controls or truncating project graphs.

**Architecture:** A versioned project JSON registry feeds a deterministic coordinator command. The portable Bash pr-smoke plugin remains independent. Verification operates on an explicit candidate, produces bounded results, and is promoted into authoritative CI only after parity and actual execution prove coverage.

**Tech Stack:** Existing Python >=3.11, Click, jsonschema, PyYAML, pytest, Git, Bash 3.2+, existing analyzers and package tools. No new task runner or service.

**Spec:** `docs/superpowers/specs/2026-09-08-shared-checks-ci-design.md`

**Preservation oracle:** `docs/superpowers/specs/2026-09-08-shared-checks-preservation-map.md`

## Global Constraints

- Preserve 0 PASS, 2 FAIL, 3 BLOCKED; when both occur, return 2 and retain both.
- No shell string evaluation or arbitrary environment expansion.
- A group result is partial and cannot certify a whole profile.
- Ordinary profile execution never downloads or installs.
- No diff filter may truncate tests, types, lock resolution or build dependency graphs.
- Actual command execution still requires the host's established sandbox; JSON validation is not that boundary.
- No baseline updates, broad exclusions, tool installation, host changes, publication, staging in the original worktree, or commits are authorized by this plan.

This is a planning deliverable. User preference for sub-agent implementation is
already recorded; do not ask again which execution style to use. Before execution,
confirm the candidate and source instructions have not changed. Preserve the five
Phase 1 files and all unrelated shared-worktree changes. Record uncommitted task
snapshots and review packages; the generic skill's commit steps do not apply.

## Read first and execution environment

Read `src/manifest_agent/cli.py`, `process.py`, root `pyproject.toml`, both source
workflows, pre-commit config, and only the scripts each task invokes. Existing
`CommandRunner.run` inherits ambient environment and has no timeout; do not reuse
it for these checks without a compatible separately tested API. Reuse its
`redact_text` for diagnostics where applicable, with both stdout/stderr covered.

Tests run in the isolated worktree with an allowlisted environment and existing
Python dependencies. Example narrow command after inspecting conftest/imports:

```bash
env -i PATH=/Users/charlemagne/agentic-workstreams/internal/Manifest/.venv/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin \
  HOME=/private/tmp/manifest-enforcement-phase1.aQmOnk/check-home TMPDIR=/private/tmp \
  LC_ALL=C GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 \
  python -m pytest tests/python/manifest_agent/test_check_registry.py -q
```

The executable environment must load the candidate's `src` and local model-policy
source, not editable imports from the shared original checkout. Test and report
`manifest_agent.__file__` before acceptance. HOME/UV flags provide hygiene, not
OS isolation. A missing enforceable execution boundary is BLOCKED for running
untrusted code; source inspection and fixtures do not waive this.

## File responsibilities and interfaces

| File (new unless stated) | Responsibility |
|---|---|
| `schemas/project-checks.schema.json` | Strict version-1 registry structure |
| `src/manifest_agent/checks/models.py` | Frozen check, selection, result and candidate records |
| `src/manifest_agent/checks/registry.py` | Schema/semantic validation and profile/group closure |
| `src/manifest_agent/checks/candidate.py` | Full candidate materialization and before/after identity |
| `src/manifest_agent/checks/preparation.py` | Candidate-local preparation locking, execution and atomic identity receipts |
| `src/manifest_agent/checks/process.py` | Allowlisted child environment, bounded capture, deadlines; introduced for preparations in Task 3 and extended for checks in Task 4 |
| `src/manifest_agent/checks/runner.py` | Check prerequisites, ordering, status and timing |
| `src/manifest_agent/checks/cli.py` | Check/list and aggregate command adapters |
| `src/manifest_agent/checks/aggregate.py` | Exact current-run required-result validation |
| `src/manifest_agent/cli.py` (modify) | Register command group without changing lifecycle commands |
| `config/project-checks.json` | Canonical project check definitions and profiles |
| `config/check-preservation.json` | Reviewed immutable old-step/hook scope/version mapping |
| `tools/project_checks/structure.py` | Existing inline structure/YAML/path checks |
| `tools/project_checks/generated.py` | Isolated generated-output comparison |
| `tools/project_checks/hooks.py` | Non-mutating equivalents of mapped hook controls |
| `tools/project_checks/packages.py` | Offline lock/build/release validation |
| `tools/project_checks/ci_context.py` | Read-only current-run job/artifact identity collection |
| `docs/SHARED_CHECKS.md` | Commands, profiles, setup, status, and coverage limits |

Add package `__init__.py` files as part of the first task that needs them.
Keep tests under `tests/python/manifest_agent/` so existing pytest conventions apply.

```python
# Shared records in checks/models.py; tuples are immutable.
@dataclass(frozen=True)
class CheckSpec:
    id: str
    category: str
    group: str
    argv: tuple[str, ...]
    cwd: str
    inputs: tuple[str, ...]
    dependencies: tuple[str, ...]
    timeout_seconds: float
    selection: str  # "changed" or "project"
    tool: str
    version: str

@dataclass(frozen=True)
class CheckResult:
    id: str
    status: str  # PASS, FAIL, BLOCKED, NOT_APPLICABLE
    returncode: int | None
    duration_seconds: float
    diagnostics: str
    selected_inputs: tuple[str, ...]

@dataclass(frozen=True)
class PreparationSpec:
    id: str
    argv: tuple[str, ...]
    cwd: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    groups: tuple[str, ...]
    timeout_seconds: float
    tool: str
    version: str

@dataclass(frozen=True)
class Candidate:
    root: Path
    source_root: Path
    head_sha: str
    base_sha: str
    tree_sha: str
    source_digest: str
    changed_paths: tuple[str, ...]
    preparation_receipt: Path

# Required public interfaces, introduced in their owning task.
load_registry(path: Path) -> dict
resolve_checks(registry: dict, profile: str, group: str | None) -> tuple[CheckSpec, ...]
materialize_candidate(source: Path, base_sha: str, destination: Path) -> Candidate
candidate_digest(source: Path) -> str
prepare_candidate(candidate: Candidate, preparations: tuple[PreparationSpec, ...],
                  env: dict[str, str]) -> tuple[CheckResult, ...]
execute_check(check: CheckSpec, candidate: Candidate, env: dict[str, str]) -> CheckResult
run_profile(registry: dict, profile: str, group: str | None, candidate: Candidate,
            env: dict[str, str]) -> dict
aggregate_results(registry: dict, profile: str, receipts: list[dict], context: dict) -> dict
```

## Task 1: Freeze the control inventory and execution contract

**Files:** create `config/check-preservation.json`, `tests/python/manifest_agent/test_check_preservation.py`.
**Interfaces:** JSON `schema_version=1`, `observed_revision`, and a `sources` object
keyed by repository-relative path. Each source value has the immutable Git blob ID
and SHA-256 of the bytes observed at that revision. Tests read those blobs with
`git show <observed_revision>:<path>`; later workflow migration never becomes the
oracle for what the old controls contained.

`source_entries` independently enumerate all 37 hooks and every CI step. Keys are
unique and structural: `hook:<repo-index>:<hook-index>:<id>` or
`workflow:<path>:job:<job-id>:step:<zero-based-index>`. Each entry records its
kind, exact normalized source value, and ordered `component_ids`. A CI step that
combines setup and verification has separate command-level components; no
component may disappear merely because its parent step remains. Hooks record
repository/revision, hook ID, entry/language, `files`, `types`, `types_or`,
`exclude`, `stages`, `pass_filenames`, `args`, `additional_dependencies`, the
global exclusion and base-selection semantics. Every optional field is a
`{"present": boolean, "value": ...}` pair so absent and explicit defaults differ.

`controls` have unique IDs, `source_key`, component ordinal/key, `check_ids`,
`workflow_control_ids`, exact tool pin, platform, group, and `disposition`:
`retained`, `setup`, or `publication`. Retained controls have nonempty check IDs;
setup controls have neither destination; publication controls have nonempty
workflow-control IDs and are intentionally absent from check profiles. No retired
controls exist. All objects reject unknown fields in the Task 1 test loader.

- [ ] Read the preservation map and source files. Extract all 37 hook IDs and
  each CI check/setup step into the JSON artifact before writing the new registry.
- [ ] Add test fixtures containing one omitted hook, altered exclude, missing CI
  step, omitted composite-step component, altered args/stages/tool pin/global
  exclude/base semantics, and setup/publication misclassification. Require each
  to fail comparison with the frozen observed Git blobs.

```python
def test_missing_existing_hook_is_rejected(preservation):
    assert "hook.detect-private-key" in preservation.required_ids
    broken = preservation.without("hook.detect-private-key")
    assert broken.compare_observed_sources().missing == ("hook.detect-private-key",)
```

`preservation` is a test-only fixture exposing the JSON's independently computed
required IDs, `without(id)`, and comparison result; it must not read the new
project registry. Implement it inside this test module.
- [ ] Run `python -m pytest tests/python/manifest_agent/test_check_preservation.py -q`;
  observe intended RED, populate the exact inventory, then GREEN.
- [ ] Independently review every scope/pin against its old source. Proposed pin
  convergence requires verified tool availability and approved provisioning;
  differing unavailable pins remain explicit BLOCKED results.

## Task 2: Validate and resolve project profiles

**Files:** models/schema/registry above; `tests/python/manifest_agent/test_check_registry.py`.
**Interfaces:** implement `load_registry` and `resolve_checks`. Registry top-level
fields: `schema_version`, `checks`, `tools`, `profiles`, `coverage_pending`. Profile values
list check IDs; release includes the union of full/security/package obligations.
`tools` maps stable tool names to `executable`, `version_argv`, `expected_version`,
and `required_modules`. CheckSpec.tool resolves that key and CheckSpec.version
must equal its expected_version; mismatched references fail validation. Version
matching uses a declared exact parsed version, never substring containment.
`coverage_pending` maps each profile to explicit unresolved obligation strings;
nonempty obligations block that complete profile, while group reports stay partial.
Top-level `candidate_preparations` declares `PreparationSpec` records with
deterministic argv, tool/version, source inputs, expected output paths and the
groups that consume them. It also requires literal `network: false` and
`installs_dependencies: false`; schema/semantics reject any other value and
known network/package-manager executables. These declarations are validation,
not a security boundary: the established runtime sandbox still denies access.
Preparations obey the same traversal, environment and timeout restrictions as checks.

- [ ] Write valid two-check fixtures and reject malformed JSON, unknown keys,
  duplicate IDs, NUL argv, negative/nonfinite timeout, unknown references, dependency
  cycles, cross-group dependencies, absolute/escaping cwd, and changed-only tests/types/builds.

```python
def test_cross_group_dependency_is_rejected(registry_file):
    path = registry_file(checks=[
        {"id": "lint.a", "group": "lint", "dependencies": ["test.b"]},
        {"id": "test.b", "group": "test", "dependencies": []},
    ])
    with pytest.raises(ValueError, match="cross-group"):
        load_registry(path)
```

`registry_file` fills required benign defaults and writes a temporary JSON registry;
define it in the test module, not production. Use `additionalProperties: false`
at every object boundary, then semantic validation after jsonschema validation.
- [ ] Run the test file RED; implement closure with stable topological ordering
  and execution-once deduplication; run GREEN.
- [ ] Prove group selection returns exact IDs. `resolve_checks` retains its tuple
  signature; Task 4 owns the report's `partial: true` marker for group execution.
  A profile with unresolved `coverage_pending` can list checks but cannot PASS.
- [ ] Reject preparation commands with undeclared outputs, empty/unknown consuming
  groups, network/install tools, or paths outside the candidate. A preparation may
  name multiple groups that consume the same generated output; it declares no
  check dependency and creates no cross-group execution edge.

## Task 3: Materialize the complete candidate without touching the original index

**Files:** candidate.py, preparation.py, models.py (add `Candidate`/`CheckResult`),
process.py (introduce the shared bounded argv primitive);
`tests/python/manifest_agent/test_check_candidate.py` and
`tests/python/manifest_agent/test_check_preparation.py`.
**Interfaces:** implement Candidate, `materialize_candidate`, `candidate_digest`,
and `prepare_candidate`; expose `CandidateBlockedError` for conditions that must
map to exit 3 in Task 5. `prepare_candidate` returns `CheckResult` records and
writes an atomic versioned receipt at `Candidate.preparation_receipt` containing
candidate tree/source identities, preparation-spec digest, and exact input/output
byte-mode-link identities. The receipt lives under destination `.git`, never in
candidate source or the original repository.

- [ ] Create real temporary Git fixtures with base A, HEAD B, an unchanged-since-B
  file, staged edit, unstaged edit, deletion, untracked file, executable and symlink.
  Snapshot original index bytes and status before materialization.

```python
def test_current_head_content_is_not_replaced_by_old_base(git_candidate, tmp_path):
    source, base = git_candidate
    before = (source / ".git/index").read_bytes()
    candidate = materialize_candidate(source, base, tmp_path / "candidate")
    assert (candidate.root / "head-only.txt").read_text() == "from current HEAD\n"
    assert (source / ".git/index").read_bytes() == before
    assert (candidate.root / "untracked.txt").is_file()
```

- [ ] Run RED; implement destination-local Git initialization/object import,
  full HEAD materialization and working-tree overlay. Disable hooks and inherited
  Git config during internal Git commands. Copy files without hardlinks; preserve
  symlink targets/modes; never recursively follow directory symlinks.
- [ ] Stage only in destination, preserve submodule gitlinks, check required
  submodules match pins, and store tree SHA. Reject unresolved conflicts, unsafe
  input symlinks, disappearing/unreadable files, and mutation during copy as BLOCKED.
- [ ] Run GREEN and prove `git ls-files`/`git archive` see the full candidate,
  including deletions and untracked additions. Fingerprint bytes, modes, link
  targets and gitlinks; timestamps alone do not establish identity.
- [ ] After materialization and before the execution fingerprint, run only the
  registry-declared candidate preparations in the disposable candidate. Add a
  clean-checkout fixture proving `.apm/skills` is generated there, its source and
  output identities are recorded, failures are BLOCKED, and original bytes/index
  remain unchanged. Preparations execute once per candidate; concurrent attempts
  use a destination-local lock and a completed identity receipt.
- [ ] Require every preparation output to be Git-ignored and reject source or
  undeclared-output mutation. The host sandbox remains the boundary against writes
  outside the candidate. Introduce the Task 4 process primitive now: argv only,
  explicit cwd/environment, bounded stdout/stderr, monotonic timeout, POSIX process
  group termination/reaping, and no ambient Git configuration. Task 4 adds the
  broader check-runner fixtures rather than replacing this implementation.
- [ ] Keep Git materialization/fingerprinting in `candidate.py` and preparation
  locking/receipt policy in `preparation.py`. Split their tests on the same seam;
  neither production module may exceed the Python constitution ceiling.

## Task 4: Execute checks with bounded resources and honest results

**Files:** checks/process.py, runner.py; tests `test_check_process.py`, `test_check_runner.py`.
**Interfaces:** `execute_check` and `run_profile`; JSON report fields include
schema_version, profile, group, partial, candidate/tree/config identities,
required_ids, results, status, and duration. No reusable success cache in Phase 2.

- [ ] Add harmless child fixtures: exit 0, exit 1, missing executable, excessive
  output, timeout with sleeping child, and changed input during execution.

```python
def test_mixed_failure_and_missing_tool_do_not_pass(check_fixture, candidate):
    report = run_profile(check_fixture("exit-one", "missing-tool"), "full", None,
                         candidate, {})
    assert report["status"] == "FAIL"
    assert {r["status"] for r in report["results"]} == {"FAIL", "BLOCKED"}
```

- [ ] Run RED; use argv subprocesses with explicit cwd/env, POSIX process groups,
  monotonic deadlines, bounded streaming capture (64 KiB per stream), redaction
  before report storage, and deterministic truncation markers. Reap children;
  unsupported required platform lifecycle control is BLOCKED.
- [ ] Add an explicit cancellation fixture that interrupts a running parent with
  a child, proves both are reaped, and proves no success receipt is emitted.
- [ ] Validate executable and Python module availability separately from an
  executed checker finding. Tool-version mismatch is BLOCKED. Missing required
  input is BLOCKED; zero applicable changed paths is NOT_APPLICABLE with evidence.
- [ ] Block dependent checks after prerequisite failure. Continue independent
  checks and report both FAIL and BLOCKED. Validate source/candidate identities
  after execution; source mutation invalidates success. Run GREEN.

## Task 5: Expose the shared CLI without coupling the portable plugin

**Files:** checks/cli.py, root cli.py (modify), schema package inclusion in
pyproject.toml (modify), `tests/python/manifest_agent/test_check_cli.py`.
**Interfaces:** `manifest check PROFILE --project-config PATH --base SHA`, optional
`--group`, `--list`, `--json`, `--output PATH`. Output must be outside candidate source.
`--base` is required for execution; listing needs only a configuration and must
not materialize a candidate or probe/execute any tool.

- [ ] Add Click CliRunner tests for missing explicit config, malformed config,
  read-only `--list`, 0/2/3 propagation, unknown profile, and partial group reporting.

```python
def test_listing_never_executes_commands(runner, configured_project):
    result = runner.invoke(cli, ["check", "quick", "--project-config",
                                str(configured_project.config), "--list", "--json"])
    assert result.exit_code == 0
    assert not configured_project.execution_marker.exists()
```

- [ ] Run RED; wire existing CLI to registry/candidate/runner. Keep lifecycle
  commands unchanged and avoid importing optional tool packages at CLI startup.
- [ ] Run GREEN plus existing `tests/python/manifest_agent/test_cli.py`.
  Build/install-wheel smoke must prove schema inclusion and command registration;
  missing preprovisioned build requirements are reported BLOCKED.
- [ ] Assert malformed registry/configuration exits 3 while Click usage errors
  exit 2; include a changed filename containing a newline in candidate/CLI tests
  to prove NUL-safe selection end to end.

## Task 6: Extract existing checks and remove verification mutations

**Files:** tools/project_checks/{structure,generated,hooks,packages}.py;
`tests/python/manifest_agent/test_project_check_bodies.py`.
**Interfaces:** each module exposes `main(argv: list[str] | None = None) -> int`;
explicit check-ID argument, required `--root`, optional `--output-dir` outside root.
Registry commands invoke these bodies by argv. They never invoke `manifest check`.

- [ ] For each preservation-map ID, port the existing body with exact scope.
  Use separate subcommands for symlinks, inventory, YAML and generated outputs;
  do not replace distinct controls with a generic successful placeholder.
- [ ] Create golden fixtures with one violation per extracted check plus valid
  fixtures. At minimum prove YAML parse error, missing symlink, wrong target,
  case collision, incorrect skill count, missing shell, drifted/new generated file,
  formatting mutation, missing wheel member, and mismatched lock fail.

```python
def test_generator_new_file_is_detected(generator_fixture):
    result = generator_fixture.run_with_new_output("rules/new.mdc")
    assert result.returncode == 2
    assert result.original_bytes == result.final_original_bytes
```

- [ ] Run RED; use native check-only flags when present. If a legacy fixer has
  no check-only mode, run its pinned existing implementation only in an additional
  disposable copy, compare all outputs, then discard that copy. Never return PASS
  because a fixer repaired the candidate. Preserve original selector semantics.
- [ ] For package checks, use provisioned backends/offline locks and distinct
  output directories. Inspect installed tool help for exact supported flags;
  absence of an offline/no-install route is BLOCKED rather than fallback to download.
- [ ] Run GREEN. Record each migrated ID and its golden negative test against
  the independent preservation oracle.

## Task 7: Register every project profile and enforce parity

**Files:** config/project-checks.json, preservation JSON, tool-version configuration
within project-checks.json; `tests/python/manifest_agent/test_check_profile_parity.py`.
**Interfaces:** concrete argv for each ID from Tasks 1/6; profile/group closures
consume Task 2 schema. Exact selectors/exclusions remain tied to the frozen inventory.

- [ ] Fill every mapped existing ID, all required inputs, actual tool probes,
  finite timeouts, and group. Use existing job ceilings (lint 1200 s, test 1800 s,
  structure 900 s) as maximum group budgets, with per-check deadlines beneath them.
- [ ] Add independent set-equality and selector/version tests, including a
  deliberate dropped hook, narrowed exclusion, and changed-only graph consumer.

```python
def test_registry_cannot_drop_existing_controls(preservation, registry):
    declared_ids = {check["id"] for check in registry["checks"]}
    for control in preservation["controls"]:
        if control["disposition"] == "retained":
            assert control["check_ids"]
            assert set(control["check_ids"]) <= declared_ids
```

`preservation` loads the independent JSON mapping, while `registry` loads the
candidate registry. Also assert each retained mapping target belongs to the
appropriate resolved profile/group; mere presence in the registry is insufficient.
Setup mappings are checked against declared CI provisioning or candidate
preparation IDs. Publication mappings are checked only against the separate
release-workflow control inventory and must not appear in a profile closure.
- [ ] Run RED then GREEN. Resolve tool pins from reviewed existing configs and
  verify provisioning reproducibly supplies them; do not change pins to whatever
  happens to be installed locally. Explicitly record unresolved platform tools.
- [ ] Quick selects changed authored files; full keeps whole graphs; security
  includes existing secret controls; release unions full/security/package. Keep
  pending Phase 3 coverage machine-readable so incomplete profiles exit 3.
- [ ] Amendment 2026-09-09 — thin probes only. Delete
  `tools/project_checks/tool_probe_process.py`, `tool_probe_metadata.py`,
  `tool_probe_provenance.py` and `tests/python/manifest_agent/test_tool_probe_*.py`
  (Phase 4 re-derives supervision/provenance from the spec). In `tool_versions.py`
  replace `_run_probe` with one `subprocess.run(argv, timeout=, start_new_session=True,
  capture_output=True)`; on timeout `os.killpg` then BLOCKED. Strip `python=<local>`
  and `file:<script>=<sha>` components from every `expected_version` in
  `config/project-checks.json`; keep `distribution:<name>=<pin>` resolved from
  `pyproject.toml`/`.pre-commit-config.yaml`. Green on macOS and Linux.

## Task 8: Validate CI producer evidence and aggregate results

**Files:** checks/aggregate.py, checks/cli.py (modify), tools/project_checks/ci_context.py;
`tests/python/manifest_agent/test_check_aggregate.py`.
**Interfaces:** `manifest check-aggregate PROFILE --project-config PATH --results-dir DIR
--context PATH --json`; `aggregate_results` from shared interface above.
Context fields: repository, workflow identifier, run_id, run_attempt, tested_sha,
expected producer job IDs and their authoritative conclusions/artifact identities.

- [ ] Add receipts with missing/extra/duplicate check IDs, wrong group, old run
  attempt, wrong SHA/config, canceled/skipped/failed job, and malformed JSON.

```python
def test_old_attempt_receipt_is_rejected(registry, receipt, ci_context):
    receipt["run_attempt"] = ci_context["run_attempt"] - 1
    report = aggregate_results(registry, "full", [receipt], ci_context)
    assert report["status"] == "BLOCKED"
```

- [ ] Run RED; validate exact required-ID sets and current-run artifact identities
  before accepting results. Unknown extra producer data is rejected. Pending
  coverage and non-success required jobs prevent aggregate PASS.
- [ ] Treat local `--context` JSON as untrusted assertion, not server proof.
  CI wrapper obtains job/artifact identities via read-only current-run API and
  trusted workflow inputs; no repository-controlled file supplies the authority.
  Fetch failure is BLOCKED. Document workflow-mutation threat until Phase 5.
- [ ] Run GREEN using mocked API response fixtures; live API probes are read-only
  and separately reported. No credentials go into child check environments.

## Task 9: Wire local callers and CI with a reversible migration

**Files:** .github/workflows/ci.yml, .github/workflows/manifest-release.yml,
.pre-commit-config.yaml (modify); docs/SHARED_CHECKS.md;
`plugins/manifest-code-quality/skills/project-verify/SKILL.md` and
`plugins/manifest-workspace/skills/pr-smoke/SKILL.md`; generated derivatives.
**Tests:** `tests/python/manifest_agent/test_shared_check_workflow.py`.

- [ ] Assert that proposed workflow groups invoke exactly the shared command,
  have finite timeouts/read-only credentials, upload current-attempt receipts,
  and aggregate with `always()` while rejecting unsuccessful upstream conclusions.
- [ ] Add a shadow migration job first; retain legacy required checks until the
  new path proves exact coverage and runtime success. Reuse setup, separate
  provisioning from verification, and preserve existing dependency ordering.
- [ ] Replace local hook bodies with the shared entry only after preservation
  checks pass; do not call pre-commit recursively. Keep explicit human formatting
  commands separate from check profiles. Add invocation-count recursion test.
- [ ] Keep portable Bash behavior untouched. Guidance must clearly distinguish
  the standalone subset from explicitly selected project checks. Regenerate all
  changed skill catalogs/mirrors via inspected generators and verify drift.
- [ ] Keep publishing separate from release verification and retain existing tag,
  release-lookup, draft and duplicate-version checks. Do not execute publication.
- [ ] Compare migrated callers to the immutable Task 1 oracle and separately
  compare current caller steps to the new registry/setup/publication destinations.
  Do not rewrite `observed_revision`, source blob IDs, source hashes or normalized
  old values when these three current source files change.
- [ ] Run workflow tests GREEN and shadow verification. Promote shared commands
  only when full/security/release obligations are resolved by Phase 3 and every
  required producer passes. Phase 5 separately enables protected required statuses.

## Task 10: Verify the complete candidate and hand off evidence

**Files:** add report under this plan's ignored SDD workspace; no source change
unless independently reviewed defect remediation is required.

- [ ] Run all new registry/candidate/process/CLI/check-body/parity/aggregate tests,
  existing lifecycle CLI tests and Phase 1 contracts. Run Bash 3.2 and Linux tests
  where available. Confirm all negative cases fail for the intended reason.
- [ ] Invoke `manifest check` for quick/full/security/release using the candidate
  registry. Capture every required check; a missing tool or coverage obligation
  remains BLOCKED and prevents a claim of complete Phase 2 migration.
- [ ] Run actual full project graphs once dependencies are provisioned and
  authorization permits execution. Narrow unit fixtures cannot certify these.
- [ ] Record exact commands, tool versions, tested HEAD/index/candidate/config
  digests, before/after status, failures/debt, unavailable settings, and review
  disposition. Independently review the total diff against both source inventory
  and design. Preserve source hashes after final checks.
- [ ] Handoff uncommitted changes with rollout/rollback instructions; do not
  merge, publish, deploy or activate branch protection based on passing checks.

## Spec coverage and acceptance boundaries

| Requirement | Tasks / authoritative proof |
|---|---|
| Shared CLI and explicit registry trust | 2, 5; real CLI tests and explicit selection |
| Preserve existing CI/hook controls | 1, 6, 7, 9; independent mapping and golden violations |
| Complete candidate and graphs | 2, 3, 7, 10; Git fixtures and actual full runs |
| Non-mutating, bounded checks | 3, 4, 6; byte comparisons and child cleanup |
| Same-candidate CI aggregation | 8, 9; current-run context and missing-result tests |
| Portable plugin independence | 5, 9; existing standalone plugin suite |
| Release verification without publishing | 6, 7, 9; artifact integrity tests and workflow boundaries |
| Independent protection/exception ownership | Phase 5; not claimed by local tests |
| Complete type/security/baseline coverage | Phase 3; pending coverage prevents premature promotion |

Tasks 1–8 and shadow/local integration can be implemented before Phase 3;
authoritative migration completion requires Phase 3 coverage. This dependency is
explicit so the executor cannot finish the goal by shipping a passing subset.
Model/client mapping is needed for Phase 4, not for the Phase 2 deterministic core.

Self-review: design requirements mapped above; shared signatures defined once;
existing no-commit boundary retained; dependency/provisioning/activation approvals
separated from local implementation; required negative and final-candidate proof
specified. This plan does not claim any proposed test has run.
