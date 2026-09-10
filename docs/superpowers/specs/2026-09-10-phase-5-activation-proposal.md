# Phase 5 activation proposal — required checks, CODEOWNERS, branch protection

Author: sonnet (implementation role), acting on fable's Phase 3–5 decision document
and the phases 3–5 ledger. Date: 2026-09-10. Branch `wip/enforcement-first-shared-checks`,
HEAD `d9e24157`. This document proposes; it activates nothing. No branch-protection,
CODEOWNERS, or ruleset change has been made to the live repository. Two read-only
`gh api` GET calls were made to record the live repository's *current* settings for
comparison (quoted below); no mutating call was made.

Read alongside: `docs/superpowers/specs/2026-09-08-enforcement-first-workflow-design.md`
("Protection, baseline and guidance"), `/private/tmp/manifest-handoff/phase-3-5-decisions.md`
(§5a–5c), `/private/tmp/manifest-handoff/phases-3-5-ledger.md`.

## 1. What is being proposed

The proposal is to make one CI job — the aggregate that already runs `manifest
check-aggregate full` over five producer groups (structure, lint, test, security,
package) — a required status check on `main`, once a fixed list of preconditions
(§5) is observably true, and to extend CODEOWNERS so the files that define what
"passing" means cannot be edited by the same change that claims to pass. For
contributors, the practical change is that a PR cannot merge until the aggregate
check is green (today nothing blocks a merge on any CI result at all — see §4,
current live settings) and until an owner has reviewed changes to the check
registry, the toolchain lock, the debt baseline, the CI workflow, and the hook
adapters, even when the PR's author already has write access.

The blast radius if this is activated before the preconditions hold is real: every
lock entry in `config/toolchain.lock.json` currently has `exe_sha256: null`, so the
toolchain store verifies nothing yet; most tool-bearing checks still resolve
executables from ambient `PATH`; the `security` producer group has never been
aggregated (the existing job only computes the `full` profile, which contains zero
`security`-group checks); and the profile has never once reached PASS — it has held
`BLOCKED` at every measured chunk boundary. Requiring a check that cannot currently
pass would either block all merges to `main` or force the owner into repeated manual
overrides, which defeats the purpose of requiring it. This document is the basis for
the owner's decision, not a recommendation to proceed now — see §5 and §8.

## 2. The exact required-check set

Verified against `.github/workflows/ci.yml` and `config/project-checks.json` in this
worktree, not against the decision document's abstract description.

| Proposed required check | GitHub check name today | Producing job(s) | Status |
|---|---|---|---|
| Full aggregate | `Shadow Checks Aggregate (non-blocking)` (job `shadow-checks-aggregate`) | Needs `shadow-checks-structure`, `shadow-checks-lint`, `shadow-checks-test`, `shadow-checks-security`, `shadow-checks-package`; runs `uv run manifest check-aggregate full ...` (ci.yml:1034) | Exists today as **non-blocking**: the job and every producer job carry `continue-on-error: true` (9 occurrences in ci.yml). To become required it must be renamed (e.g. `checks-aggregate-full`) and have `continue-on-error` removed from both itself and its producers. |

**Not proposed as required, and why:**

- **A separate `checks-aggregate-security` check does not exist in the workflow
  today and cannot be proposed as required until it is built.** `ci.yml` invokes
  `manifest check-aggregate` exactly once, always with the `full` profile
  (`grep -n check-aggregate .github/workflows/ci.yml` returns one line). The `full`
  profile has 70 checks and **zero** with `group: security` (verified by loading
  `config/project-checks.json` and filtering `profiles.full` against each check's
  `group`); the 7 checks with `group: security`/security-relevant checks
  (`hook.check-credentials`, `hook.gitleaks`, `security.semgrep`, etc.) live only
  in `profiles.security`. The `shadow-checks-security` producer job already runs
  and uploads a receipt, but nothing aggregates it — `aggregate.py:150` emits
  `"producer job references unexpected group"` when a security receipt is fed to
  a `full` aggregation (recorded in the ledger, C6). Building and shadow-running a
  genuine `checks-aggregate-security` job (aggregating the `security` profile,
  not `full`) is a precondition for proposing it as required, not something this
  document can request today.
- **`lint`, `test`, `validate`** (the pre-existing inline jobs, ci.yml:18/213/329) —
  kept required during the transition window per the decision document (5a), not
  retired by this proposal. They stay required until a reviewed PR retires them
  after 20 agreeing runs against the aggregate (§6, item 4).
- **The five shadow producer jobs individually** (`shadow-checks-structure/lint/
  test/security/package`) — not proposed as required checks in their own right.
  Only the aggregate is required, by design: a missing, cancelled, or skipped
  producer cannot silently pass, because the aggregate's "Reject shadow producers
  with no receipt evidence" step (ci.yml:964) fails the aggregate itself when any
  producer's `receipt_written` output is not `"true"`. Requiring the producers too
  would be redundant and would let GitHub's "skipped = neutral" semantics leak
  through a path the aggregate step exists to close.
- **`dependency.audit.python` / `dependency.audit.node`** — registered as check
  bodies (verified in `config/project-checks.json`'s `checks` list) but absent
  from all four profiles (`quick`, `full`, `security`, `release` — verified by
  filtering every profile's id list for `dependency.audit`, zero hits in each).
  They cannot be part of any required aggregate until C8 (the owner's decision on
  whether `pip-audit`/`npm audit` may transmit dependency metadata to PyPI/OSV/npm)
  is made and they are added to a profile.

## 3. CODEOWNERS extension

Current file (`.github/CODEOWNERS`, verified by reading it) covers only
`/.github/workflows/` and itself, owned by `@RB-chrismandich` — no second reviewer
is named anywhere in the repository today. The decision document's `@OWNER
@SECOND` pattern requires a second independent handle that does not yet exist (see
open question in §8, item 2). The lines below use the one confirmed owner and leave
the second column as `@SECOND-REVIEWER-TBD`, a placeholder that must be replaced
with a real handle before this is merged — do not merge a CODEOWNERS line with a
placeholder handle, since GitHub will not resolve it and the rule will silently
fail to gate anything.

Append to `.github/CODEOWNERS` (exact lines to add, after the existing two rules):

```
# Phase 5 extension: policy surfaces that define or enforce what "passing" means.
# Each line pairs the confirmed owner with a second independent reviewer so that
# review is possible even when @RB-chrismandich authors the change.
/config/                                           @RB-chrismandich @SECOND-REVIEWER-TBD
/schemas/                                          @RB-chrismandich @SECOND-REVIEWER-TBD
/src/manifest_agent/checks/                        @RB-chrismandich @SECOND-REVIEWER-TBD
/src/manifest_agent/hooks/                         @RB-chrismandich @SECOND-REVIEWER-TBD
/tools/project_checks/                             @RB-chrismandich @SECOND-REVIEWER-TBD
/tools/*.py                                        @RB-chrismandich @SECOND-REVIEWER-TBD
/configs/claude/scripts/constitution/               @RB-chrismandich @SECOND-REVIEWER-TBD
/configs/claude/config/constitution_baseline.json   @RB-chrismandich @SECOND-REVIEWER-TBD
/tools/bundle_link_baseline.json                    @RB-chrismandich @SECOND-REVIEWER-TBD
/.pre-commit-config.yaml                            @RB-chrismandich @SECOND-REVIEWER-TBD
/.gitleaks.toml /.gitleaksignore                    @RB-chrismandich @SECOND-REVIEWER-TBD
/pyproject.toml /uv.lock                            @RB-chrismandich @SECOND-REVIEWER-TBD
/configs/claude/pyproject.toml /configs/claude/uv.lock @RB-chrismandich @SECOND-REVIEWER-TBD
/plugins/manifest-delegate/pyproject.toml /plugins/manifest-delegate/uv.lock @RB-chrismandich @SECOND-REVIEWER-TBD
/plugins/stitch-design/runtime/node/package*.json   @RB-chrismandich @SECOND-REVIEWER-TBD
/.github/                                           @RB-chrismandich @SECOND-REVIEWER-TBD
/docs/superpowers/specs/                            @RB-chrismandich @SECOND-REVIEWER-TBD
```

Rationale for the set: `/config/` covers `project-checks.json` (the engineering
contract's check registry), `toolchain.lock.json`, `debt-baseline.json`, and
`check-preservation.json` (the preservation oracle) in one prefix rather than
enumerating each — verified all four live directly under `config/` by `ls config/`.
`/src/manifest_agent/checks/` and `/src/manifest_agent/hooks/` are the check
bodies and adapters. `/.github/` widens the existing workflows-only rule to cover
the whole directory (already the case for the existing rule's scope note, now made
literal since `ci.yml` is the enforcement surface itself).

## 4. Bypass and admin settings

Verified live values via `gh api repos/RB-chrismandich/Manifest/branches/main/protection`
(read-only GET, run 2026-09-10) — shown as **current** below, requested as
**proposed**, each with a one-line justification:

| Setting | Current (live, verified) | Proposed | Justification |
|---|---|---|---|
| Required status checks | **None configured** — `required_status_checks` endpoint returns 404 "Required status checks not enabled" | `checks-aggregate-full` (renamed `shadow-checks-aggregate`) + legacy `lint`/`test`/`validate`, **after** §5b holds; "require branches to be up to date" enabled | Nothing currently blocks a merge on any CI outcome; this is the core of the proposal |
| Enforce for admins (`enforce_admins`) | `false` | `true` | The parent spec requires agents/admins not hold bypass privileges over the gates they are also asked to satisfy; leaving this `false` lets any admin (including an agent acting with admin credentials) merge around a red aggregate |
| Allow force pushes | `false` | `false` (unchanged) | Already correct; re-stated so the proposal is a complete target state, not a diff assumption |
| Allow deletions | `false` | `false` (unchanged) | Already correct |
| Required conversation resolution | `false` | `true` | Prevents merging over an unresolved reviewer objection, including one raised by an automated check-preservation or debt-ratchet comment |
| Dismiss stale reviews | `true` | `true` (unchanged) | Already correct; keeps an approval from applying to a materially different diff |
| Required approving review count | `1` | `1` (unchanged) | Matches the parent spec's "require independent review", not raised further here — a stricter count is a separate owner decision, not implied by this proposal |
| Require review from Code Owners | `true` | `true` (unchanged), scope widened via §3 | Already correct as a setting; its effect widens only because CODEOWNERS itself is extended |
| Require approval of the most recent reviewable push (`require_last_push_approval`) | `false` | `true` | Closes the gap where an approved PR is force-pushed with new changes and merged without a fresh approval — the parent spec's "require independent review of the latest changes" |

Also verified: no GitHub environment named anything resembling `manifest-plugin-live`
exists. `gh api repos/RB-chrismandich/Manifest/environments` returns exactly two
environments, `copilot` and `main`; neither matches the release-gate environment
the decision document asked about. This is a change from the decision document's
"HTTP 404 in the audit" (which implied the name might simply not have been queried
yet) to a confirmed absence: whatever the release workflow references, it does not
exist as a protected GitHub environment today, and no release step should treat it
as present.

## 5. Preconditions that are NOT yet met

This is the load-bearing section. Every item below was checked against the
worktree at `d9e24157`, not asserted from the ledger.

1. **C7 (hash-filling) has not run.** `config/toolchain.lock.json` has 10 tool
   entries; every one has `"exe_sha256": null` (verified: `grep -c '"exe_sha256":
   null"' config/toolchain.lock.json` → 10, zero non-null matches). The store's
   `_verify_exe` compares against the lock's `exe_sha256`; a null entry means the
   tool is unattested and every preflight for it reports BLOCKED before the store
   manifest is even opened. Nothing the store verifies has been verified yet.
2. **Only 1 of 9 engine-bearing checks resolves through the store.** Verified: the
   registry's `tools[].executable` field uses the `store:` form in exactly one
   place — `store:node-env/bin/bats` (2 references, both for `test.bats` /
   `test.bundle-partition`). Every other tool-bearing check (`ruff`, `shellcheck`,
   `shfmt`, `yamllint`, `gitleaks`, `markdownlint-cli2`, `semgrep`, `pyright`,
   etc.) still resolves via `shutil.which` against ambient `PATH` plus a version-
   string probe (the C2 review's finding, confirmed unresolved by design — the
   ledger records the migration was deliberately deferred to C7 because migrating
   now, with every lock hash null, would only convert working checks to BLOCKED
   for zero verification gain). The store's core security property — a binary is
   trusted only after its hash matches a reviewed lock — currently applies to one
   tool.
3. **The shadow CI aggregate path has never executed a real run.** Every producer
   job and the aggregate itself carry `continue-on-error: true` in `ci.yml` (9
   occurrences, verified by grep). The ledger records this chunk (C6) as
   controller-verified only against local, non-CI measurement; cross-job
   `needs.*.outputs` plumbing, the real `github.event.before` payload shape on a
   push event, and `gh run download`/`gh api` behavior in the actual GitHub Actions
   environment are explicitly disclosed as unconfirmed (ledger, C6 entry).
4. **`dependency.audit.*` is built but disabled pending the dependency-metadata
   decision.** Verified: `dependency.audit.python` and `dependency.audit.node` are
   registered checks in `config/project-checks.json` but appear in none of the
   four profiles (`quick`, `full`, `security`, `release` — checked each list).
   They cannot contribute evidence to any required aggregate until the owner
   answers the §8 question about sending package metadata to PyPI/OSV/npm.
5. **No adapter has been verified against a real client**, and every receipt
   carries `client_version_verified: false`. Verified in source: this literal
   string is hardcoded in `src/manifest_agent/hooks/receipt.py:34` and referenced
   in every per-client adapter module (`claude_code.py`, `gemini.py`, `cursor.py`,
   `codex.py`) as an always-false field. The vendored per-client protocol fixtures
   under `tests/fixtures/hooks/<client>/unverified/` are marked unverified by
   directory name, not merely by a note.
6. **The model/client mapping is unresolved.** The parent spec states the
   Fable/Astra/Opus/Sol → client mapping is unconfirmed and this session's hosting
   build was not identified by local installation metadata. Nothing in the
   worktree resolves this; it is an open question (§8).
7. **The `full` profile has never reached PASS.** The ledger's own controller
   measurements at every one of the 8 completed chunk boundaries (C1 through C11)
   show `BLOCKED` with zero `FAIL`, never `PASS` — most recently `Counter({'BLOCKED':
   39, 'PASS': 20, 'NOT_APPLICABLE': 11})` at the final commit. A required check
   that cannot currently PASS cannot be made required without either blocking all
   merges or immediately requiring an override, which is the opposite of the
   proposal's intent.
8. **The `security` profile has never been aggregated at all** (§2) — there is no
   CI job computing it, shadow or otherwise, so there is no evidence to promote to
   required even after C7.
9. **`docs/SHARED_CHECKS.md` (579 lines) is over the cap that applies to it.**
   Verified directly against `configs/claude/scripts/docs_lint.py`: the file is
   not under `docs/superpowers/specs/` and matches none of the exemption globs
   (`**/specs/**` matches a path *segment* literally named `specs`, which
   `docs/SHARED_CHECKS.md` does not contain), so it is classified and capped like
   any other doc, and the ledger records it grew past its cap during C6 without
   being split. This is a pre-existing, disclosed debt independent of Phase 5
   activation; it does not block this proposal but is listed because the parent
   spec requires drift tests on generated/reference docs and this one is
   currently over cap in practice.
10. **Known non-reproducible test flake**: `test_shared_check_recursion.py` was
    observed flaky once during C6 (passed standalone in 110s) and has not been
    re-investigated. Not re-verified in this session (see "could not verify"
    below); listed as a disclosed carry-forward.
11. **`environment: manifest-plugin-live`** (or any equivalently-named release
    gate environment) does not exist on the live repository (verified §4). Any
    release workflow step that assumes its protections exist is assuming
    something false today.
12. **CODEOWNERS' second reviewer handle is undetermined** (§3, §8 item 2) — the
    literal lines in §3 cannot be merged as written; `@SECOND-REVIEWER-TBD` must
    be replaced with a real GitHub handle first.

## 6. The promotion gate (5b) — owner-verifiable checklist

Before any check in §2 becomes required, the owner should be able to confirm each
of the following independently (not take an agent's word for it):

- [ ] **Shadow-CI defects fixed and tested (C6).** Confirm in the merged history:
      a commit that (a) makes push-event base resolution use `github.event.before`
      only when it is a non-zero ancestor SHA, reporting changed-file checks
      BLOCKED otherwise rather than falling back to `merge-base(HEAD, HEAD~1)`, and
      (b) treats a producer's well-formed receipt with FAIL/BLOCKED as *produced*
      evidence, not a crash. Verify by reading `.github/workflows/ci.yml`'s "Resolve
      base revision" step and `aggregate.py`'s producer-acceptance logic, and by
      finding the corresponding tests.
- [ ] **`manifest provision --from-lock` succeeds in a real CI run with every hash
      verified (C7)**, and `full`, `security`, and `release` each reach PASS or a
      FAIL naming real, specific debt — zero BLOCKED — on at least one observed
      run per profile. Verify by opening the actual GitHub Actions run and reading
      the aggregate's `status` field, not a local measurement.
- [ ] **Negative canaries observed in real CI**, each producing FAIL: a
      badly-formatted file, a failing pytest assertion, a synthetic (non-real)
      secret, and a same-count-but-different-identity constitution finding.
      Confirm the legacy `lint`/`test`/`validate` jobs agree where they have
      overlapping coverage.
- [ ] **Twenty consecutive PR runs** where the new aggregate and the legacy jobs
      agree on pass/fail; any disagreement investigated and written down, with the
      count restarted after a disagreement, not continued.
- [ ] **Measured p95 full-CI duration ≤ 15 minutes** over that 20-run window — if
      missed, it should be reported as missed, not used as a reason to cut scope
      from the checks being run.
- [ ] **A probe PR touching `config/`** is observed to require the extended
      CODEOWNERS' owner review before it can merge (confirms §3 actually gates,
      not just that the file was edited correctly).
- [ ] **A `checks-aggregate-security` job exists, has shadow-run at least once,
      and its `security` profile has reached PASS or named-debt FAIL** — this is
      additional to the decision document's original 5b list, added here because
      §2 establishes that no such job exists yet; promoting a check that has never
      run once is a stronger gap than the ones the original 5b anticipated.

## 7. Rollback

If activation goes wrong after the owner approves it:

- **Required status check removal is immediate and fully reversible.** Un-checking
  `checks-aggregate-full` (and any added security aggregate) from the branch
  protection required-checks list via the GitHub UI or `gh api` restores the
  pre-activation state within the propagation delay of GitHub's own settings
  (typically seconds). No commit, revert, or code change is needed for this half
  of the rollback.
- **`enforce_admins`, `required_conversation_resolution`, and
  `require_last_push_approval`** are likewise plain settings toggles, reversible
  the same way, with no data loss.
- **CODEOWNERS is a file in the repository.** Reverting the appended block is a
  normal revert commit, itself subject to the same review requirements it removes
  — which is by design, not a gap; a reviewed removal is not a self-approved
  bypass.
- **What cannot be cleanly reverted**: PRs merged *during* a period of incorrect
  enforcement (for example, a false-negative window where a real defect was
  scored PASS because a tool resolved through unverified `PATH` rather than the
  hash-pinned store) are not automatically re-checked retroactively — rollback
  restores the gate, it does not re-audit history. If activation is later found to
  have let a defective change merge, that specific commit needs its own review,
  separate from the settings rollback. Any PR merged specifically *because* an
  admin bypassed a red required check (once `enforce_admins` is `true`, this
  becomes impossible without another settings change first, which is itself
  logged in the repository's audit log) should be treated as unreviewed and
  re-examined regardless of settings state.
- **Debt-baseline and toolchain-lock changes made under the extended CODEOWNERS**
  are ordinary file history; reverting the CODEOWNERS extension does not undo
  baseline entries approved while it was active, nor should it — those approvals
  stand on their own merits.

## 8. What the owner must decide

1. **Dependency-metadata upload**: may `pip-audit` and `npm audit` send package
   names and versions to PyPI/OSV and the npm registry from CI? — *Yes* enables
   `dependency.audit.*` in the `security` profile after implementation (C8); *No*
   means that coverage class stays permanently absent from any required check,
   and any future advisory-based check needs an offline feed instead.
2. **Who is the second independent CODEOWNERS reviewer**, and is
   `@RB-chrismandich` confirmed as the administrator who will apply the protection
   settings? — Without a second handle, §3's lines cannot be merged as CODEOWNERS
   rules requiring two reviewers is meaningless with only one repository owner;
   the owner must either name a second person/team or accept single-owner review
   (which weakens "independent review of the latest changes" when the owner
   authors the change themselves).
3. **Model/client matrix**: which installed client (and version) corresponds to
   each of Fable/Astra/Opus/Sol in this workflow? — Blocks C10 (live adapter
   verification) entirely; until answered, every adapter receipt keeps
   `client_version_verified: false` indefinitely, and no adapter can be installed
   into host settings.
4. **Platforms to attest** in the toolchain lock: is `darwin-arm64` the only
   developer platform in addition to `linux-x64` (CI), and may developer hosts run
   `manifest provision` with network access at all? — Shapes what C7 fills in;
   choosing "developer hosts never provision with network" means every developer
   machine either stays permanently BLOCKED for store-resolved tools or needs an
   `--import` path with a separately-distributed trusted hash list.
5. **Debt policy acceptance**: is 180 days the maximum exception lifetime for
   `config/debt-baseline.json` entries, and is the `release` profile's
   base-only-baseline rule (a candidate can never ship its own exceptions)
   acceptable? — *Reject either* sends 3c back for rework before any debt-backed
   check (including the constitution and bundle-link ratchets already in `full`)
   can be part of a required aggregate.
6. **Confirm as recorded decisions**: Gitleaks unified on 8.30.1 everywhere, and no
   TypeScript compiler check (because no `.ts` project currently exists, only
   `build.mjs`/esbuild tooling and template files) — both already implemented in
   the worktree; rejecting either means reopening 3b's engine-pin table and, for
   the second, potentially adding a `tsc`-based check the moment a real
   `tsconfig.json` is committed.
7. **`manifest-plugin-live` environment** (or whatever the release workflow
   should actually reference): verified absent from the live repository today
   (§4) — should the owner create and protect an environment under this or
   another name, or should the release workflow be changed to not depend on one
   existing?

## Could not verify

- The exact behavior of `github.event.before` on a real push event to `main`, and
  of `gh run download`/`gh api` inside GitHub's hosted runners, since this
  worktree cannot execute a real Actions run (no network / no CI dispatch
  available here). Recorded as unverified per the ledger's own C6 disclosure, not
  independently re-confirmed.
- Whether `test_shared_check_recursion.py`'s C6-era flake is still present; not
  re-run in isolation during this session.
- The precise current count and shape of `coverage_pending` entries by reason
  class (the ledger's running tallies); the field exists in
  `config/project-checks.json` as a dict, confirmed structurally present, but its
  per-reason breakdown was not re-tallied here.
