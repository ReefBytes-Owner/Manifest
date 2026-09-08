# Built-in Commands

> Shipped commands, label management, and the issue-linking hooks.

**Last Updated**: 2026-08-20

## Built-in Commands

Manifest ships with 80+ skills and 1 CLI tool; the table below is a curated
subset of the most-used commands. The **full, always-current catalog** is in the
generated [Command Reference](../COMMANDS.md#command-reference) below (every command, grouped
by category) — or run `/help [query]` in-session for searchable discovery. Both
are built from each skill's `SKILL.md` frontmatter, the authoritative source.

| Command | Description | Parallel Agents |
|---------|-------------|-----------------|
| `/git-commit` | Full commit pipeline: docs, pull, pre-commits, commit, push | CONDITIONAL (Phase 3) |
| `/docs-improve-readme` | Improve README documentation | NO |
| `/docs-generate-diagrams` | Generate Mermaid architecture diagrams | CONDITIONAL (5+ modules) |
| `/docs-improve` | Diataxis documentation framework analysis | CONDITIONAL (>500 lines) |
| `/docs-all` | Run docs-improve-readme/docs-generate-diagrams/docs-improve as sub-agents in one pass | CONDITIONAL |
| `/python-refactor` | Python codebase security and quality analysis | CONDITIONAL (risk-based) |
| `/shell-refactor` | Bash/Shell script security and quality analysis | CONDITIONAL (risk-based) |
| `/node-refactor` | Node.js/TypeScript codebase security and quality analysis | CONDITIONAL (risk-based) |
| `/go-refactor` | Go codebase security and quality analysis | CONDITIONAL (risk-based) |
| `/terraform-refactor` | Terraform/OpenTofu IaC security, modularity, and quality analysis | CONDITIONAL (risk-based) |
| `/issue-triage` | Linear issue audit: duplicates, staleness, priority validation | CONDITIONAL |
| `/issue-prioritize` | Score and rank open issues by impact/urgency/readiness/risk | CONDITIONAL |
| `/issue-dev-auto` | Autonomously develop one opted-in (`auto-dev`-labeled) issue end-to-end — selects next ready issue, implements test-first, verifies, opens a PR. **Now also monitors automation PRs and (opt-in via `PR_MERGE_LOOP_APPLY=1`) merges them to main once the gated decision clears — CI green, comments addressed, #360 gate Tier-1 pass, consensus ≥0.80; fail-closed to a human otherwise.** Self-paced, stops after 5 empty runs | NO |
| `pr_merge_loop.sh run [--apply]` | Bounded self-paced merge-loop pass: enforces a hard 10-minute ceiling, stops after 5 consecutive empty runs, serializes merges via `loop_lock` (one in flight), exits 11 on halt (post-merge `main` red). Default dry-run; pass `--apply` or set `PR_MERGE_LOOP_APPLY=1` for real merges. `/loop /issue-dev-auto` is the outer re-invoker. Standalone `run` orchestrates monitoring/merge only — it does not itself push code revisions; a PR needing `revise` requires the SKILL (`/loop /issue-dev-auto`) to apply fixes, otherwise it polls until the ceiling. | NO |
| `/issue-sync-pr` | Hook-triggered: on PR open, back-link + advance linked issue to `needs-review` + ensure closing keyword (fail-open) | NO |
| `/issue-sync-commit` | Hook-triggered: on branch commit, advance a `planned` issue to `in-progress`, deduped (fail-open) | NO |
| `/plan-manage` | Plan lifecycle with parallel agent orchestration | CONDITIONAL |
| `/smoke-manage` | Catalog-driven smoke tests; UI steps run via browser-use `mode: agent` | NO |
| `/session-checkpoint` | Create compact checkpoint summary when context is high | NO |
| `/env-check` | Verify CLI tools, auth, config syntax, MCP, symlinks | NO |
| `/config-audit` | Detect cross-platform config drift and broken symlinks | NO |
| `/version-pin` | Enforce specific, hashed version pins in dependency files (auto-fix on demand; warn-only save hook) | ALWAYS (Tier 1) |
| `/pr-review` | Review all open PRs and recommend a disposition per PR (analysis-only) | NO |
| `/pr-monitor` | Babysit a just-opened PR/MR: watch CI to green (fix failures), address Copilot findings, tag Jules and handle its feedback. Auto-triggers on `gh pr create`/`glab mr create` | NO |
| `/branch-clean` | Prune merged/gone/stale branches safely (dry-run by default, local-only) | CONDITIONAL (--apply) |
| `/repo-clean` | Review-then-confirm cleanup sweep of open PRs and stale/merged/gone branches (GitHub/GitLab/local) | CONDITIONAL (close/prune path) |
| `/skill-evolve` | Promote SkillClaw-evolved skills into a review PR (dry-run by default); requires SkillClaw enabled | NO |
| `/pass-cli` | Retrieve credentials from Proton Pass vaults via `pass-cli` agent CLI | NO |
| `/spec-review` | Independent Antigravity (agy) cross-reference of spec/plan/tasks for internal consistency; on-demand or via fail-open PostToolUse save hook (content-hash debounced, detached); analysis-only; works with speckit and superpowers layouts; silent-mode findings land in `.spec-review/feedback.md`. `--mode product\|technical` distinguishes the two lifecycle spec-review passes | NO |
| `/lifecycle-run` | Drive a unit of work through the codified nine-phase state-gated lifecycle (Specify→…→Verify) with hard gating; entry is a ticket URL/issue key (GitHub/GitLab/Linear/Jira); the smoke-test suite is the Verify gate. Backed by `lifecycle.sh` (constitution Principle VI) | NO |
| `/a11y-audit` | WCAG 2.2 AA accessibility audit | NO |
| `/antipattern-detect` | Detect recurring antipatterns from lint, test, and review feedback | NO |
| `/ci-setup` | Configure CI/CD pipelines for a target repository (GitHub Actions or GitLab CI) | NO |
| `/code-audit` | Auto-triggered security and quality checks | AUTO (always when triggered) |
| `/metrics-report` | Visualize agent efficiency metrics | NO |
| `/learning-capture` | Capture structured lessons learned | NO |
| `/performance-check` | Frontend performance audit: bundle size, Core Web Vitals, caching | NO |
| `/project-scaffold` | Initialize new projects with quality gates and Manifest integration | NO |
| `/ux-review` | UX audit: accessibility, responsive design, performance budgets | NO |
| `/project-verify` | Run linters, tests, and security scans in parallel | CONDITIONAL |
| `/token-benchmark` | Measure Manifest context token overhead and quality delta across providers; regenerates `docs/TOKEN_BENCHMARK.md` | NO |

**CLI tool** (installed to `~/.local/bin/`):

| Tool | Description |
|------|-------------|
| `apm-dev-sync` | **Retired** (spec 674 Phase 5). Skills ship as plugin bundles: `claude plugin update <bundle>@manifest` |
| `sync-skills` | Legacy copy-based sync; stands down for apm-owned domains (`skills`) |

The `code-audit` skill activates for an explicit security-review request or a
confirmed security-boundary behavior change. Keywords, file size, and complexity
metrics alone do not activate it.

---

## Label Management

Issue labels are defined in a central registry at `configs/claude/config/labels.yml` and synced
across GitHub, GitLab, and Linear.

### Canonical Labels

| Label | Color | Hex | Description |
|-------|-------|-----|-------------|
| `planned` | Blue | `#1D76DB` | Implementation plan exists for this issue |
| `in-progress` | Yellow | `#FBCA04` | Implementation is actively underway |
| `needs-review` | Orange | `#E3A21A` | Requires human review before completion |
| `done` | Green | `#0E8A16` | Implementation complete and validated |
| `follow-up` | Lavender | `#D4C5F9` | Spawned from another issue during implementation |
| `future` | Green | `#C2E0C6` | Queued for future prioritization and scheduling |
| `auto-dev` | Purple | `#5319E7` | Eligible for the autonomous issue developer (/issue-dev-auto) |
| `needs-human` | Dark red | `#B60205` | Auto-dev could not complete; needs a human |
| `blocked-dependency` | Gray | `#6A737D` | Has an unmet dependency; excluded from the auto-dev queue |
| `ready-to-merge` | Green | `#0E8A16` | Auto-dev verified the PR but lacked merge authority; awaiting a human merge |
| `loop-active` | Yellow | `#FBCA04` | Transient lock — the auto-dev merge loop is acting on this PR |
| `hold` | Red-orange | `#D93F0B` | Do not auto-merge; the loop must route this PR to a human |

**Deprecated**: `processed` — use `done` instead (same color and purpose).

### Syncing Labels

```bash
# Dry-run — see what would be created
~/.claude/scripts/label_sync.sh --dry-run

# Sync all labels to the current Git platform (GitHub or GitLab)
~/.claude/scripts/label_sync.sh

# Sync only to Linear
~/.claude/scripts/label_sync.sh --platform linear --team ENG

# Validate without creating
~/.claude/scripts/label_sync.sh --validate

# Via git_ops.sh wrapper
~/.claude/scripts/git_ops.sh label-sync
~/.claude/scripts/git_ops.sh label-sync --dry-run
```

### Managing Labels

```bash
# List labels on current platform
~/.claude/scripts/git_ops.sh label-list

# Create a single label on current platform
~/.claude/scripts/git_ops.sh label-create "my-label" --color "FF0000" --description "My label"

# Create a label in Linear
~/.claude/scripts/linear_ops.sh label-create --name "my-label" --color "FF0000" --team ENG

# List labels in Linear
~/.claude/scripts/linear_ops.sh label-list --team ENG
```

---

## Issue-Linking Hooks

Two opt-in, fail-open hooks keep the linked GitHub/GitLab issue in sync with
development activity (skills `issue-sync-pr` and `issue-sync-commit`, over the shared
`issue_support.sh` engine). They never block a git action.

```bash
# Enable (unified PostToolUse hook); add --native for a guarded git post-commit hook
configs/claude/scripts/install_issue_hooks.sh --enable [--native]

# Preview / debug without mutating the tracker
configs/claude/scripts/issue_support.sh sync-pr --dry-run
configs/claude/scripts/issue_support.sh resolve --branch 005-my-feature --json

# Disable (keeps the skills; flips the runtime gate off and removes the hooks)
configs/claude/scripts/install_issue_hooks.sh --remove
```

Behavior: PR opened → linked issue advances to `needs-review` + back-link + `Closes #N`;
commit on a branch → a `planned` issue advances to `in-progress` (deduped). Coverage
boundary: PR creation via the web UI or raw `gh`/`glab` outside a tool is not
auto-observed — run `issue_support.sh sync-pr` manually there. Config:
`command_config.yml → tool_policies.{pr,commit}-issue-sync`.

---

---

[← Commands Guide](../COMMANDS.md)
