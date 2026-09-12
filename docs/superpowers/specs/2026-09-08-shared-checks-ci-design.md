# Shared checks and CI integration — Phase 2 design

Status: reviewed for implementation planning at the user's request; activation
and implementation acceptance remain separate. The user has authorized work toward phases 2–5;
this document resolves the Phase 2 interface before implementation. No host
activation, branch-protection mutation, publication, or dependency installation
is implied. Phase 1 remains uncommitted in the same isolated worktree.

## Evidence

Base: 7741d4aa588ed57791af15ea0eedd8862305ff0c plus five reviewed Phase 1 files.

- `pyproject.toml` registers `manifest = manifest_agent.cli:main`.
- `src/manifest_agent/cli.py` provides the existing Click command surface.
- `.github/workflows/ci.yml` has lint, test, and validate jobs with inline checks,
  generated-view checks, package builds, lock validation, pytest, Bats, and Lite smoke.
- `.pre-commit-config.yaml` invokes Ruff, shfmt, and Markdown tools in fixing
  modes. A verification profile must not invoke those modes on candidate files.
- CI skips absent Python and smoke directories; a required profile must reject
  missing suites, missing tools, and failed discovery.
- CI and pre-commit disagree on some tool versions: Gitleaks 8.30.1 versus
  8.30.0; current local Ruff is 0.15.0 versus pre-commit 0.15.20. Do not certify
  parity from the tool names alone.
- `plugins/manifest-workspace/skills/pr-smoke/scripts/run_pr_regression.sh`
  is a portable Bash subset. It cannot acquire a coordinator or YAML dependency.
- Root Pyright configuration includes only scripts/tests and suppresses missing
  imports. It is not evidence of full package-graph checking.

## Alternatives and recommendation

1. Extend the existing Manifest CLI with an explicit project-check command and
   one declarative registry. Recommended: reuses the command surface, preserves
   portable plugin independence, supports CI shards without duplicated commands.
2. Put every repository check into the portable Bash runner. Rejected: couples
   an independently installed plugin to this repository and its Python environment.
3. Add a new task runner or external governance service. Deferred: adds setup,
   dependencies, and cost without resolving check-set drift better than option 1.

## Interface and trust

Proposed invocation: `manifest check PROFILE --project-config PATH`, where
PROFILE is quick, full, security, or release. Configuration is selected explicitly;
neither opening a checkout nor a hook event grants permission to execute its code.
CI uses the same command with `--group lint|test|structure|security|package` to
partition execution. A group result is partial and cannot certify a whole profile.
`--list --json` emits the resolved graph without execution for parity tests.

Use a versioned JSON registry and a checked schema. Each check declares a stable
ID, category, argv array, repository-relative cwd, required inputs, dependency
IDs, finite timeout, expected tool/version, selection mode, and execution group.
No shell string evaluation or arbitrary environment expansion. Reject unknown
keys, duplicate IDs, traversal outside the project, cycles, missing inputs,
unknown profiles, and empty required selections. Actual command execution still
requires the host's established sandbox; JSON validation is not that boundary.

Candidate preparation is separately declared in that registry for deterministic,
non-networked generated inputs such as the ignored `.apm/skills` mirror. It runs
only inside the disposable candidate, before its execution fingerprint, records
source/output identities, and cannot install tools or write outside the candidate.

The portable pr-smoke CLI retains its current standalone contract. Its guidance
points to the explicit project command when full repository verification is
requested; it does not automatically discover or execute the project registry.

## Profile definitions

| Profile | Required scope | Execution semantics |
|---|---|---|
| quick | Changed authored-file formatting/lint, syntax and whitespace | Explicit base plus staged/unstaged/untracked candidate selection; NUL-safe paths; check-only tools |
| full | Existing lint, test, structure, generated output, lock and package gates; whole-graph checks as introduced in Phase 3 | Complete package/test graph; no diff restriction on graph consumers |
| security | Existing secret scan plus approved local security rules and dependency checks introduced in Phase 3 | Full declared security scope; unavailable tool/feed is BLOCKED |
| release | Full and security dependencies plus distributable integrity/provenance checks | Builds into isolated output; never publishes or deploys |

Profiles are dependency closures with duplicate checks executed once. Missing
future security/type controls remain explicitly incomplete until Phase 3 lands;
the final objective cannot be met by labeling these profiles successful early.
No new analyzer is selected merely to fill a profile.

## Preserve the CI check set

Companion evidence: `2026-09-08-shared-checks-preservation-map.md` enumerates
existing verification steps, hook IDs, scope rules, pin disagreements, and the
separate release workflow. It must become the independently reviewed parity
mapping before CI extraction; its proposed IDs are not proof of implementation.

Move check logic, not provisioning, out of CI. Inventory-to-registry tests require
every existing check below to have a corresponding stable check ID:

- ShellCheck scripts/bootstrap, empty-array and Bats assertion guards,
  fresh-checkout self-containment, YAML parse/lint, key-doc Markdown, command-doc drift.
- Bundle-local references; plugin views, vendored dependency drift, runtime paths,
  agent frontmatter, capability inventory and matrix; coordinator/config package
  builds; root/config/delegate lock integrity; applicable pre-commit controls.
- Full Bats, non-native repository pytest, separately collected skill-local
  hook tests, Lite smoke. Native tests retain explicit platform requirements.
- Symlink targets, case collisions, shell syntax, exact skill count, required
  script presence, forbidden absolute skill paths, bundle partition,
  cross-skill references, generated Cursor rules/MCP/agents.

Keep environment provisioning in reviewed CI setup steps. Install pinned tools
before invoking checks; ordinary profile execution never downloads or installs.
Replace `npx` auto-install with an already provisioned executable. Preserve Linux
CI and explicit Bash 3.2 validation; do not infer cross-platform parity from one.

## Result and mutation contracts

### Candidate and execution identity

Materialize local candidates into a disposable Git repository with the current
HEAD and the explicit comparison base available. The base determines changed
paths only; all current HEAD files form the starting candidate, including files
unchanged since HEAD but different from the comparison base. Copy the complete
working-tree state, including tracked
deletions, mode bits, symlink targets, and non-ignored untracked files; stage those
bytes only in the disposable index. Never stage the original worktree. The
disposable index tree is the candidate identity consumed by `git archive` and
`git ls-files` checks. Preserve pinned submodule gitlinks and verify populated
submodules match them; dirty or unavailable required submodules are BLOCKED.
Reject unsafe escaping symlinks for execution inputs. Generate ignored mirrors
inside this candidate after materialization, with their source identity recorded.
For CI, use its tested checkout SHA and index; do not substitute a branch name or
PR head SHA for the merge candidate actually checked out.

Group selection includes only assigned checks. Cross-group execution dependencies
are forbidden in schema version 1; each group owns its complete local dependency
closure, with common setup explicitly repeated if needed. Profile dependencies
are unions of groups. The registry resolves exact required check IDs per group;
the aggregate rejects missing, additional, duplicate, or non-success IDs.

CI aggregation must also inspect authoritative job conclusions for all expected
producer jobs. Results are selected only from the current workflow run and attempt,
carry the actual candidate SHA and producer job identity, and are checked against
the expected check-ID inventory. Do not trust result JSON supplied in a PR or
reuse artifacts across runs. Current-run artifact selection and successful job
conclusions are necessary but not protection against malicious changes to the
workflow itself; that boundary requires Phase 5 protected ownership and checks.

### Provisioning and preservation mapping

Before extraction, prepare a reviewed mapping artifact that lists every old CI
verification step and pre-commit hook against its new check ID or setup step,
including exact path scope, exclusions, base-ref semantics, tool version,
platform, and any proposed retirement. No retirement is implicit. Preserve
checksum-verified Gitleaks setup and separate skill-local pytest collection.
Parity tests compare the new registry and CI callers against this independently
reviewed mapping, not against a second view derived from the new registry alone.
The mapping pins immutable Git blobs and their SHA-256 values at the observed
revision, uses workflow/job/step indexes and hook repo/hook indexes as unique
source identities, and splits composite steps into ordered command components.
Publication-only controls have a separate destination class: they remain workflow
controls and never masquerade as release-profile checks or setup.

Provisioning creates the declared locked environments and installs build backends
and tools separately from verification. Verify imports/tool versions before work.
Use `uv run --offline --no-sync --frozen` only against an explicitly selected
preprovisioned environment; lock validation runs offline; builds disable isolated
dependency installation and use preinstalled build backends with isolated output.
Verify exact flags against the installed tool version before implementation.
Unavailable build requirements or offline metadata are BLOCKED, not permission
to resolve, install, or silently omit lock/build checks.

Preserve 0 PASS, 2 FAIL, 3 BLOCKED; when both occur, return 2 and retain both.
Record each check's status, exit code, duration, selected inputs, and bounded
diagnostics in versioned JSON. Missing prerequisites and timeouts are BLOCKED;
executed findings are FAIL. Cancellation never emits success. Terminate child
process groups on deadline and test this with harmless fixtures.

Formatting checks use non-mutating arguments. Generators without check mode run
against an isolated materialized candidate and compare outputs, including newly
created files. Build outputs and receipts live outside candidate source. Include
the uncommitted/untracked candidate content; do not silently test only HEAD.
Record and compare input hashes around execution so concurrent edits invalidate
the result. Phase 3 extends this into reusable, provenance-bound receipts.

CI aggregate checks require every expected group to succeed for the same candidate
and registry identity. Failure, cancellation, unexpected skip, missing group,
duplicate/incompatible receipts, or changed inputs prevents success. Activating
the aggregate as a protected required status belongs to Phase 5 administrator work.

## Enforcement matrix

| Rule | Control/scope | Trigger | Failure | Exception | Proof |
|---|---|---|---|---|---|
| Same checks everywhere | Registry + existing CLI, local/CI groups | Local invocation and PR CI | Missing/extra required ID fails parity | Reviewed registry change | Compare profile closure to CI groups |
| No silent missing checks | Required-input/tool validation | Each check | BLOCKED | Explicit native platform partition | Remove tool/suite fixture |
| Complete graphs | Whole-project selector on tests/types/builds | full/release | Reject changed-only graph selection | None | Cross-package fixture error |
| Non-mutating verification | Check flags or isolated generator comparison | All profiles | Source mutation fails | Declared output outside source | Valid and invalid formatting byte checks |
| Bounded execution | Deadline/process-group lifecycle | Each child | Timeout/cancel BLOCKED | Human changes reviewed deadline | Hanging/child fixture |
| Fresh candidate | Input identity before/after | Profile/aggregate | Stale receipt rejected | None | Modify fixture during/after check |
| Preserve existing gates | Explicit CI inventory mapping | Registry/CI change | Missing group/check fails | Independent reviewed retirement | Delete check from fixture registry |

## Implementation sequence and acceptance

Optional changed-file checks with no applicable inputs report NOT_APPLICABLE,
with the selector and zero-input evidence. They do not count as executed PASS.
A missing required whole-project input remains BLOCKED. Malformed registry
input exits 3 with a structured configuration error; a CLI usage error exits 2.

Git hooks are thin opt-in callers only. Remove duplicated hook bodies after
their check definitions are preserved in the registry; do not invoke pre-commit
from inside its own replacement hook. Until parity, tool availability and all
required full-profile results pass, retain the legacy CI gate alongside the
new candidate gate. Full/security/release with unfinished Phase 3 obligations
must report incomplete/BLOCKED and must not replace a required CI status.

1. Test and implement registry parsing, graph selection, result aggregation,
   deadlines, and CLI integration using stdlib/local fixtures and existing dependencies.
2. Extract existing inline checks into small repository-owned scripts; add
   read-only variants for mutating controls and materialized-candidate generators.
3. Register every existing CI check, unify tool pins, and expose profiles/groups.
4. Replace CI check bodies and local convenience callers with the shared commands;
   retain setup and permissions separately. Add aggregate status verification.
5. Run positive/negative fixtures, parity tests, complete required profile checks,
   candidate mutation checks, and independent review before handoff.

Do not erase debt or broaden exclusions to get a passing run. Record failures
for Phase 3's independently reviewed baseline policy; report unavailable tooling
without installing it absent authorization. The phase is incomplete while any
required profile or parity assertion is missing or only weakly evidenced.

Amendment 2026-09-09: tool-version probes in this phase are thin. A probe is one
bounded `subprocess.run` in its own session; timeout or an unsupported platform
is BLOCKED, never PASS. Process-family supervision, distribution metadata and
RECORD provenance, and launcher-replacement detection move to Phase 4 with the
native adapters; they are not partially shipped here. `expected_version` pins
only components resolved from reviewed configuration (distribution versions,
project Python minor): no self-hash of the check script and no local-interpreter
version.

## Remaining phases and decisions

Phase 3: identity-based debt ratchet compared to protected base; complete Python
and actual TS project coverage; approved security/package checks; fresh receipts.
Baseline creation is proposed data, never agent-approved exception authority.

Phase 4: actual model/client mapping remains unanswered. Read native schemas and
verify installed versions before configuring thin adapters; test malformed JSON,
event coverage, trust/precedence, recursion, concurrency and failure semantics.

Phase 5: protect the contract/registry/baselines/CI/CODEOWNERS with independent
owners; prepare exact required-check and bypass settings for administrator approval;
record model/runtime, attempts, repair/review/check cost and accepted candidate.
Missing cost telemetry is unknown. No new source upload or paid service is assumed.

Rollback: revert only reviewed phase changes and generated derivatives. Never
reset the shared dirty worktree or weaken required protections as a convenience.
