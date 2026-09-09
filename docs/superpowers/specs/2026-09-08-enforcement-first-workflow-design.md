# Enforcement-first engineering workflow

Status: design direction approved in conversation on 2026-09-08; implementation
planning authorized. Repository administration, host configuration, dependency
installation, publication, and paid/provider activity require separate approval.

## Objective and scope

Convert objectively checkable rules into deterministic checks shared by local
development and protected CI. Keep architectural intent and judgment in the
existing engineering contract. Measure correctness, escaped defects, feedback
latency, and cost per independently accepted change.

The first implementation phase repairs result integrity in the existing runner.
Later phases unify check selection, introduce reviewed debt controls, test native
adapters, and activate administrative protections. Each phase must have its own
reviewable implementation plan; this document does not authorize activation.

## Observed state

Audit HEAD: `a3ede138db5ad6a9926e562c237e8ecfe1252d23`, plus a changing dirty
working tree. Other work became staged during inspection. No engineering checks
were executed; neither this document nor the audit is a clean-tree certificate.

| Evidence | Observation |
|---|---|
| Root and `configs/claude/pyproject.toml`, `plugins/manifest-delegate/pyproject.toml` | Python packages, uv locks, Hatchling, local model-policy dependencies |
| `bootstrap/`, `configs/claude/scripts/`, plugin runtimes | Bash and Python are primary implementation languages |
| `plugins/stitch-design/runtime/node/package.json` and tracked `package-lock.json` | npm, esbuild, Babel and Puppeteer; TS/JS runtime code |
| `.pre-commit-config.yaml`, `pyproject.toml` | Ruff, ShellCheck, shfmt, Markdown/YAML checks, Gitleaks, manual Pyright |
| `.github/workflows/ci.yml` | Existing lint, test, structure, package and generated-artifact gates |
| `tests/bats/`, `tests/python/`, skill-local hook tests, `smoke-catalog/` | Existing test infrastructure to reuse |
| `plugins/manifest-workspace/skills/pr-smoke/scripts/run_pr_regression.sh` | Quick mode checks whitespace only; missing tools become WARN; shell syntax passes a glob to one Bash invocation |
| `plugins/manifest-code-quality/skills/project-verify/SKILL.md` | Generic exit-1-as-warning classification cannot represent pytest correctly |
| `configs/claude/scripts/constitution/baseline.py` | Per-file/rule counts cannot identify new findings with unchanged counts |
| `configs/claude/scripts/constitution/cli.py` | Unreadable inputs can be skipped during collection |
| `.gitleaks.toml` | Custom rules do not extend the default rule set |
| `.claude/CLAUDE.md`, runtime guides | Some skill-source and hook-capability statements are stale |

Read-only GitHub queries on 2026-09-08 found no repository rulesets. Main branch
protection requires one approval and CODEOWNER review, dismisses stale reviews,
but has no required status checks and does not enforce restrictions on admins.
The `manifest-plugin-live` environment query returned HTTP 404: existence and
protection could not be established with the available access.

## Architecture and compatibility constraint

Keep `run_pr_regression.sh` as the portable entry point. Do not migrate Bash to
Python wholesale or introduce another task runner. The installed plugin must
remain usable without Manifest's coordinator, bootstrap tree, or assistant homes.
`tests/bats/workspace_plugin_runtime.bats` explicitly guards this boundary.

Repository-specific check definitions belong to the repository; the shared runner
may consume an explicitly selected, reviewed project configuration in a later
phase. It must not infer permission to execute arbitrary configuration merely
because a file exists. The exact configuration interface is a phase-2 design
decision, with a standalone-plugin compatibility test required before adoption.

The eventual command profiles are quick file checks, full verification, security,
and release verification. Agent hooks, Git hooks, and CI must select these same
underlying checks. Release verification never publishes anything.

## Engineering contract

1. Preserve Bash 3.2 compatibility and the standalone plugin boundary.
2. No installation, network activity, host writes, or provider calls in ordinary checks.
3. Required findings fail; unavailable or incomplete checks are BLOCKED, never PASS.
4. No agent-approved exception, weakened exclusion, or removed meaningful test to obtain green output.
5. No merge or deployment authorization follows from passing checks.

Full analysis must preserve package/import graphs. Changed-file selection is
appropriate for formatting and local lint feedback, not for truncating type
checking, dependency resolution, or distribution inputs. Formatting verification
must not rewrite input files.

Result contracts are tool-specific. For the existing regression runner, preserve
0 for PASS and 2 for executed-check FAIL; introduce 3 for BLOCKED. Exit 1 is a
legacy WARN contract and is not a successful mandatory verification result. If
failures and blocked checks coexist, report both and return 2. A future machine
report must preserve both counts and every component status.

## Enforcement requirements

| Rule | Tool/config and scope | Trigger | Failure | Exception | Proof |
|---|---|---|---|---|---|
| Formatting | Ruff format/shfmt, authored Python/Bash | Quick/full | Required check fails | Provenance-bound generated/vendor bytes | Bad format rejected, bytes unchanged |
| Lint | Ruff/ShellCheck/Bats guards, authored source | Quick/full | Rule finding fails | Narrow reviewed debt | Invalid and valid fixtures |
| Configuration | JSON/YAML parsers and schemas | Relevant edit/full | Parse/schema/read error blocks | None for required malformed config | Wrong-type and missing-input cases |
| Types | Pyright over all Python package roots; approved TypeScript compiler for actual Node runtime | Full | New diagnostics fail | Finding-specific baseline | Cross-package type error |
| Dead code | Ruff unused symbols initially | Quick/full | Enabled rule fails | Declared public/plugin entry points | Unused import versus registered entry point |
| Structure | Existing constitution checker | Quick/full | New finding or incomplete scan blocks | Protected fingerprint baseline | Same-count replacement finding |
| Tests | pytest, Bats, Lite and hook suites | Full | Failure/collection error/missing required suite blocks | Native/licensed suites reported separately | Failing assertion and missing suite |
| Secrets | Gitleaks, reviewed extension of defaults | CI/release | Finding or scanner error blocks | Exact false-positive record | Synthetic shapes, redacted output |
| Source security | One approved local Semgrep ruleset | Security | High-confidence finding blocks | Expiring reviewed record | Harmless rule fixtures |
| Dependency integrity | Locks, drift checks; approved pip-audit/npm audit | Dependency change/release | Drift fails; unavailable feed blocks | Reviewed time-limited advisory record | Mismatch and controlled feed response |
| Generated/package integrity | Existing generators, uv/Hatchling and Node build check | Full/release | Drift/build/missing file blocks | No hand-edited generated exemption | Modified output and missing package member |
| Policy integrity | Ownership, protected baseline/config comparison, required CI | PR | Missing independent review blocks | Audited human emergency process | Policy mutation plus approval-state fixtures |
| Access | Native permissions plus OS/container isolation | Session/check launch | Unsupported required boundary blocks operation | Explicit human authorization | Harmless denied file/network probes |
| Freshness | Candidate/config/tool-version digest receipt | Full/handoff | Input changes invalidate result | None | Edit after success and concurrent edit |

No new analyzers for dormant languages. Do not add a second Python security
analyzer or whole-project dead-code scanner without measured incremental benefit.

## Native adapters and host settings

Models and clients are separate axes. The Fable/Astra/Opus/Sol client mapping is
unconfirmed. Local installation metadata showed Codex 0.153.4, Claude Code
2.1.263, and Gemini CLI 0.29.6; it does not identify this session's hosting build.
Cursor CLI was not found on PATH. Repository Fable-tier retirement comments are
historical configuration, not proof of the user's current model availability.

Read native schemas, then test the actual pinned client version. Current upstream
references reviewed in the audit:

| Runtime | Reference | Essential constraint |
|---|---|---|
| Codex | <https://developers.openai.com/codex/hooks> | Sources accumulate; non-managed hooks require trust; event-specific output and tool coverage |
| Claude Code | <https://code.claude.com/docs/en/hooks> | Command failures/timeouts generally fail open; post-tool events cannot undo changes |
| Cursor | <https://prod.cursor.com/docs/hooks> | Trusted workspace; source precedence; explicit failClosed support is version-dependent |
| Gemini CLI | <https://geminicli.com/docs/hooks/reference/> | Millisecond timeouts; event-specific blocking and structured output |

Adapters parse bounded JSON structurally, validate field types, normalize paths,
and invoke argument arrays. Output only protocol JSON on stdout; redact and cap
diagnostics. Deduplicate repeated events and serialize receipt writes. A child
deadline terminates its process group; a timeout never creates PASS evidence.
Stop hooks have native recursion guards and at most one continuation per unchanged
failing state. Unsupported coverage is reported, not emulated with an LLM gate.

Local Codex configuration states read-only/never while this session exposes
workspace-write and escalation. Verify effective settings separately from source
configuration. A sed editing convention is not a security boundary. Neither
changing HOME nor setting NO_PROXY establishes credential/network isolation.

## Protection, baseline and guidance

Extend CODEOWNERS to the contract, checks, schemas, locks, baselines, exceptions,
adapters and generators. Require independent review of the latest changes. Agents
must not possess self-approval or bypass privileges. Make an aggregate CI result
required and bind its expected source. Failed/cancelled/unexpectedly skipped jobs
must not yield successful aggregate status. Protect the current target candidate;
add merge-group triggers only if a merge queue is adopted.

Run untrusted PR code on disposable, least-privileged workers with no production
credentials, privileged host mounts, Docker socket, or writable release cache.
Keep publication separate from untrusted builds and tie artifacts to verified
revisions. Verify environment protection through host-side evidence before use.

Replace count-only debt allowances with reviewed identities, compare against the
protected base, reject new/expired exceptions, and prevent restored debt. Do not
baseline actual secrets. Preserve short judgment guidance in the existing Code
Constitution and use small runtime-native entry files; generated facts get drift
tests. Repository-wide policy changes require review, not a prose instruction to
an agent to inspect itself repeatedly.

## Delivery phases

| Phase | Deliverable | Gate before progression |
|---|---|---|
| 1 | Honest runner statuses, explicit quick scope, complete shell syntax iteration | Isolated positive/negative fixture tests; no claim of CI equivalence |
| 2 | Shared check profiles/configuration and CI consumers; corrected generic verification guidance | Exact check-set parity; full graphs; no optional mandatory tools |
| 3 | Reviewed finding baseline, type/security/package coverage, fresh receipts | Finding replacement, stale verification, package and scanner failure tests |
| 4 | Thin native adapters and effective-host verification | Confirmed model/client matrix; trusted isolated version-specific probes |
| 5 | Required checks/ownership activation and operational measurement | Administrator approval, independent reviewer ownership, observed CI status |

Only phase 1 is sufficiently bounded for an executable plan now. The later rows
are delivery boundaries, not permission to improvise implementation or activate
controls. In particular phase 2 resolves the portable/project interface before
changing CI, and phase 4 requires the missing client mapping.

## Rollout, rollback and measures

Establish valid and invalid candidate behavior before enabling required checks.
Pilot adapters per client/version. Roll back only the faulty adapter or reviewed
implementation, preserving CI requirements. Emergency exceptions are scoped,
time-limited, human-owned and auditable; do not broaden exclusions during outages.

Initial proposed p95 targets: 2 seconds edit feedback, 30 seconds quick checks,
15 minutes full CI. These are targets, not measurements or implementation promises.
Record model ID, runtime version, attempts, repair cycles, accepted revision,
check duration, review time and available usage cost. Count failed attempts in
cost per accepted change. Missing cost telemetry is unknown, never zero.

Outstanding decisions: actual clients for the four model labels; independent
reviewer/admin ownership; source and dependency-metadata upload restrictions;
approval of additional packages/services; latency and debt policy acceptance.
Default remains no new paid service or source upload.
