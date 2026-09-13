# Gemini Orchestration Guide

This document defines how Gemini should leverage parallel LLM agents
(Gemini, Cursor, Claude CLI, Codex, Antigravity, Devin) for cross-verification, planning, and validation.

**Symlink Strategy**: The `.gemini/` directory shares most assets with `.claude/`
via symlinks. Prompts, configuration, scripts, plans, and all skills
point back to their canonical locations under `~/.claude/`. Only this guide
(`GEMINI.md`) and `settings.json` are Gemini-specific. This avoids duplication
and ensures both agents always operate from the same orchestration rules and
validation criteria.

## Risk-based review routing

Use a single capable reviewer by default. Independent review is risk-based:
escalate only for a trust-boundary change, destructive behavior, broad
compatibility or deployment impact, conflicting evidence or unresolved
uncertainty, or genuinely independent codebase-wide tracks. Counts of files,
packages, modules, languages, keywords, and units do not independently escalate.

## Token Economy (always on)

Apply at all times, in every session:

- Lead with the result. No filler ("Sure", "Here's the…"), no closing summaries.
- Match response length to the task; don't re-explain code you just wrote unless asked.
- Use programmatic edit tools for targeted edits; never reprint a whole file for a small change.
- If an implementation detail is genuinely ambiguous, ask ONE targeted question instead of guessing.
- Read what a change depends on (types, signatures, callers); skip speculative
  whole-tree crawls and re-reads of unchanged files. Don't starve context —
  a wrong edit costs more than one extra dependency read.
- Pin dispatched sub-agents to Sonnet by default; never inherit the session's model.

`/token-conserve` re-asserts this mode if drift is noticed mid-session.

## Parallel Agent Script

**Location**: `~/.claude/scripts/parallel_agent.py`
(accessed via symlink at `~/.gemini/scripts/parallel_agent.py`)

### Quick Usage

**IMPORTANT**:

- Always use **absolute paths** when specifying files to analyze or review
  (agents run from different working directories).
- Always use a **large timeout** (600-900s) for complex analyses; the 120s
  default is often insufficient.

Default MCP/tool routing — use the matching tool when the task domain matches.
`configs/gemini/settings.json` registers Context7 only; everything marked
*opt-in* is absent until `./bootstrap.sh --install-mcp` adds it, so check your
tool list before routing to one.

- **Context7 MCP** — library/API docs, code generation, setup, configuration
- *opt-in* **Sentry MCP** — production/runtime errors, stack traces, release regressions
- *opt-in* **Linear MCP** — issue requirements, acceptance criteria, project planning
- **Semgrep CLI** (`semgrep scan`) — local SAST, vulnerability and secrets
  checks (install: `brew install semgrep`; a CLI, so unaffected by MCP setup)
- *opt-in* **DeepWiki MCP** — unfamiliar repos, dependency internals, upstream API contracts
- *opt-in* **Glean MCP** — internal team knowledge, runbooks, ADRs
- *opt-in* **Google Dev Docs MCP** — Firebase/Cloud/Android/Maps documentation
- *opt-in* **Atlassian MCP** — Jira issues, Confluence pages, Compass components
- *opt-in* **Apify MCP** — web scraping/crawling for structured external data
- *opt-in* **OpenTofu MCP** — Terraform/OpenTofu registry, provider/module docs

```bash
# Basic code review with JSON output (all 5 agents, 10 min timeout)
~/.claude/scripts/parallel_agent.py --json --timeout 600 --review /absolute/path/to/file

# Full analysis with validation and model selection (15 min timeout)
~/.claude/scripts/parallel_agent.py --json --full-output --validate --timeout 900 \
  --cursor-model advanced --claude-model opus --analyze /absolute/path/to/file

# Generic prompt to all agents
~/.claude/scripts/parallel_agent.py --json "Your question here"

# Quick query with lightweight models
~/.claude/scripts/parallel_agent.py --cursor-model mini --claude-model haiku "Quick question"
```

### Options

| Option | Description |
|--------|-------------|
| `--json` | Output JSON for programmatic parsing |
| `--full-output` | Include complete agent outputs (no truncation) |
| `--validate` | Check outputs against success criteria |
| `--review <file>` | Code review mode |
| `--analyze <file>` | Bug/security analysis mode |
| `--improve <file>` | Improve observation YAML mode |
| `--cursor-only` | Run only Cursor Agent |
| `--gemini-only` | Run only Gemini CLI |
| `--claude-only` | Run only Claude CLI |
| `--antigravity-only` | Run only Antigravity (agy) |
| `--devin-only` | Run only Devin (opt-in; `--enable-devin` + `devin auth login`) |
| `--no-claude` | Disable Claude CLI (enabled by default) |
| `--no-antigravity` | Disable Antigravity for this run |
| `--cursor-model <tier>` | Cursor model: mini, flash, advanced, auto (default: flash) |
| `--claude-model <tier>` | Claude model: haiku, sonnet, opus (default: sonnet) |
| `--antigravity-model <tier>` | Antigravity model: mini, flash, advanced (default: flash) |
| `--check-credits` | Run pre-flight credit check |
| `--timeout <sec>` | Timeout per agent (default: 120) |
| `--output <dir>` | Custom output directory |

### Model Selection

The orchestrating agent selects models based on task complexity:

| Task Type | Cursor | Claude | Gemini | Antigravity | Reason |
|-----------|--------|--------|--------|-------------|--------|
| Security | advanced | opus | pro | advanced | Maximum capability for critical code |
| Review | flash | sonnet | flash | flash | Balanced performance/cost |
| Analyze | flash | sonnet | flash | flash | Good reasoning without opus cost |
| Improve | mini | haiku | flash | mini | Lighter models for suggestions |
| Quick | mini | haiku | flash | mini | Speed for simple queries |

**Model Tier Mappings:**

| Tier | Cursor | Claude | Gemini | Codex | Antigravity |
|------|--------|--------|--------|-------|-------------|
| mini/haiku | gpt-5.1-codex-mini | claude-haiku-4-5-20251001 | - | gpt-5.4-mini | Gemini 3.5 Flash (Low) |
| flash/sonnet | gpt-5.1-codex | claude-sonnet-4-6 | gemini-3-flash-preview | gpt-5.4 | Gemini 3.5 Flash (High) |
| advanced/opus/pro | gpt-5.2 | claude-opus-4-8 | gemini-3-pro-preview | gpt-5.5 | Claude Opus 4.6 (Thinking) |

### Credit Exhaustion Fallback

The script automatically detects credit/quota exhaustion and falls back:

- **Cursor**: gpt-5.2 → gpt-5.1-codex → gpt-5.1-codex-mini → auto
- **Claude**: opus → sonnet → haiku
- **Antigravity**: advanced → flash → mini

Detection methods:

1. Parse stderr for credit/quota error patterns after execution
2. Optional pre-flight check with `--check-credits` flag

### JSON Output Schema

```json
{
  "timestamp": "YYYYMMDD_HHMMSS",
  "mode": "review|analyze|prompt",
  "prompt": "The task description",
  "agents": {
    "cursor": {
      "status": "complete|missing|failed",
      "validated": true|false,
      "model": "gpt-5.1-codex|auto",
      "credit_fallback": false,
      "output": "Agent response..."
    },
    "gemini": {
      "status": "complete|missing|failed",
      "validated": true|false,
      "output": "Agent response..."
    },
    "claude": {
      "status": "complete|missing|failed",
      "validated": true|false,
      "model": "sonnet|haiku|opus",
      "credit_fallback": false,
      "output": "Agent response..."
    },
    "antigravity": {
      "status": "complete|missing|failed",
      "validated": true|false,
      "model": "flash|mini|advanced",
      "credit_fallback": false,
      "output": "Agent response..."
    }
  },
  "output_files": {
    "cursor": "/path/to/cursor_output.txt",
    "gemini": "/path/to/gemini_output.txt",
    "claude": "/path/to/claude_output.txt",
    "antigravity": "/path/to/antigravity_output.txt",
    "summary": "/path/to/summary.md"
  },
  "cross_verification": {
    "consensus_score": 85,
    "confidence": "high|medium|low",
    "agent_count": 5
  }
}
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_INCLUDE_DIRS` | Colon-separated directories for Gemini | `$(pwd):~/.claude:~/.gemini` |
| `CURSOR_MODEL_MINI` | Model name for 'mini' tier | `gpt-5.1-codex-mini` |
| `CURSOR_MODEL_FLASH` | Model name for 'flash' tier | `gpt-5.1-codex` |
| `CURSOR_MODEL_ADVANCED` | Model name for 'advanced' tier | `gpt-5.2` |
| `GEMINI_MODEL_FLASH` | Model name for 'flash' tier | `gemini-3-flash-preview` |
| `GEMINI_MODEL_PRO` | Model name for 'pro' tier | `gemini-3-pro-preview` |
| `CHECK_CREDITS_PREFLIGHT` | Enable pre-flight credit check | `false` |

---

## Proactive Decision Framework

Use risk-based review routing. One capable reviewer is the default. Escalate
only for a trust-boundary change, destructive behavior, broad compatibility or
deployment impact, conflicting evidence or unresolved uncertainty, or genuinely
independent codebase-wide tracks. File, package, module, language, keyword, and
independent-unit counts are not escalation conditions.

---

## Cross-Verification Patterns

### Pattern 1: Agreement Scoring

After receiving outputs from both agents, assess consensus:

```text
Consensus Score = (Agreements / Total_Findings) * 100

>=80%: High confidence - proceed with unified recommendation
50-79%: Medium confidence - highlight disagreements to user
<50%: Low confidence - escalate for human review
```

### Pattern 2: Synthesis

When agents disagree, synthesize by:

1. Identifying the core disagreement
2. Evaluating each agent's reasoning
3. Providing a unified recommendation with caveats
4. Noting which agent's approach was preferred and why

### Pattern 3: Specialization

Use agents for their strengths:

- **Gemini**: Broad knowledge, creative solutions, research
- **Cursor**: IDE-integrated context, code-specific analysis
- **Claude**: Deep reasoning, security analysis, complex logic

---

## Validation Criteria

- **Tier 1 (blocking)**: cross-verification, security, error handling, breaking
  changes. **Tier 2 (advisory)**: bugs, performance, maintainability, tests.
- Authoritative weights: `~/.gemini/config/validation_criteria.yml` (symlink to
  `~/.claude/config/`). Consensus thresholds and verdict rules
  (`APPROVED`/`NEEDS_REVIEW`/`BLOCKED`): `~/.claude/references/orchestration.md`.

## Proactive Coding Guardrails (always on)

Apply while writing or refactoring code, in every session:

- **Propagate error signals** — every catch rethrows, returns a typed
  error/fallback the caller must check, or routes to a central handler.
  Never log-and-fall-through.
- **Validate at boundaries** — type/presence/range checks at entry points;
  distinguish zero from missing; pass only validated values inward.
- **Secrets from the environment only** — no credential literals in source,
  tests, or .env.example; fail fast when required config is absent.
- **Handle the async lifecycle** — await or explicitly route every async
  operation; pair every listener/subscription/timer with its teardown;
  serialize or atomize concurrent writes to shared state.
- **Refactor before accreting** — extract the seam before adding to long
  functions/files; search for an existing helper before writing a new one.
- **No speculative code** — no guards for unreachable states, no single-use
  abstractions, no dead modules kept "for later".
- **Verify dependencies exist** — check the official registry (existence,
  maintenance, advisories) before adding any package.
- **Refinement safety** — when modifying existing code, NEVER remove security
  controls or validation without stating it in the change description.

Registry of anti-patterns (detection cues + prevention rules):
`~/.claude/config/knowledge_base.yml` (guardrail tags: arch, async-state,
error-handling, security, dependency, iteration). Full reference:
`~/.claude/references/antipatterns.md`. Pre-write doctrine (13 articles, size
ceilings, per-language annexes): `~/.claude/references/code-constitution.md`,
enforced by `constitution_check.py`. On-demand deep audit: `/ai-code-audit`.

---

---

## Workflow Integration

### Before Making Changes

```bash
# Get multi-agent review of proposed changes
~/.claude/scripts/parallel_agent.py --json --validate \
  "Review this planned change: [description]. Files affected: [list]"
```

### After Making Changes

```bash
# Validate the implementation (use absolute path, 10 min timeout)
~/.claude/scripts/parallel_agent.py --json --validate --timeout 600 --review /absolute/path/to/modified_file
```

### For Complex Decisions

```bash
# Get diverse perspectives
~/.claude/scripts/parallel_agent.py --json --full-output \
  "Evaluate these approaches for [problem]: Option A: ... Option B: ..."
```

---

## Error Handling

The script implements:

- **Agent validation**: Checks if `cursor-agent`, `gemini`, and `claude` commands exist
- **Retry logic**: Retries once after 5s delay on failure
- **Partial results**: Continues with available agent outputs if some fail
- **Credit fallback**: Automatically retries with cheaper models on quota errors
- **Exit codes**: 0=success, 1=no args, 2=no agents available

---

## Output Location

All outputs are stored in: `~/.claude/.agent_outputs/`

Files generated per run:

- `cursor_YYYYMMDD_HHMMSS.txt` - Cursor Agent output
- `gemini_YYYYMMDD_HHMMSS.txt` - Gemini CLI output
- `claude_YYYYMMDD_HHMMSS.txt` - Claude CLI output
- `summary_YYYYMMDD_HHMMSS.md` - Markdown summary
- `results_YYYYMMDD_HHMMSS.json` - JSON output (if --json)

---

## Orchestrated Code Review Workflow

One capable reviewer is the default. Add independent review only for a
trust-boundary change, destructive behavior, broad compatibility or deployment
impact, conflicting evidence or unresolved uncertainty, or genuinely
independent codebase-wide tracks. Counts of files, lines, packages, modules,
languages, keywords, and units do not independently escalate review.

### Phase 1: Pre-flight Analysis

Assess the five conditions in `~/.claude/prompts/preflight_analysis.md`
(symlinked at `~/.gemini/prompts/preflight_analysis.md`) and return JSON with
`needs_parallel_review`, `reason`, `triggered_criteria`, and `confidence`.
Record concrete behavior evidence; a keyword or size measurement is not a
trigger.

### Phase 2: Parallel Agent Review

If the risk gate opens and cross-model review is appropriate, execute:

```bash
~/.claude/scripts/parallel_agent.py --json --full-output --validate --timeout 600 --review /absolute/path/to/file
```

If the review cannot run safely or a required capability is unavailable, report
it as unavailable rather than as passing.

### Phase 3: Synthesis (on disagreement)

When agents disagree (consensus < 80%), synthesize using the template at
`~/.claude/prompts/synthesis.md` (symlinked at `~/.gemini/prompts/synthesis.md`).

Return JSON with `consensus_score`, `disagreements`, `unified_recommendation`.

**Consensus Thresholds**:

- >=80%: High confidence - proceed with unified recommendation
- 50-79%: Medium confidence - highlight disagreements to user
- <50%: Low confidence - escalate for human review

### Phase 4: Validation

Always run validation before finalizing changes using the criteria in
`~/.claude/prompts/validation.md` and `~/.claude/config/validation_criteria.yml`
(both symlinked into `~/.gemini/`).

Return JSON with `tier1` results, `tier2` results, `overall_verdict`.

**Verdicts**:

- `APPROVED`: All Tier 1 checks pass, Tier 2 score >= 0.60
- `NEEDS_REVIEW`: All Tier 1 checks pass, Tier 2 score < 0.60
- `BLOCKED`: Any Tier 1 check fails

### Configuration Files

| File | Purpose |
|------|---------|
| `~/.claude/prompts/preflight_analysis.md` | Pre-flight analysis prompt template |
| `~/.claude/prompts/synthesis.md` | Disagreement synthesis prompt template |
| `~/.claude/prompts/validation.md` | Validation criteria prompt template |
| `~/.claude/config/validation_criteria.yml` | Detailed validation rules and thresholds |

All of the above are accessible via symlinks under `~/.gemini/prompts/` and
`~/.gemini/config/`.

### Example Orchestration Flow

```text
User: "Add authentication middleware to the API routes"

Orchestrator:
  1. Pre-flight analysis
     -> Returns: {needs_parallel_review: true, reason: "Authentication logic", confidence: 0.95}

  2. Executes: ~/.claude/scripts/parallel_agent.py --json --validate --timeout 600 \
       --cursor-model advanced --claude-model opus --review "$(pwd)/src/middleware/auth.js"
     -> Gemini: "Use JWT with refresh tokens, add rate limiting"
     -> Cursor: "Use JWT with session fallback, add CSRF protection"
     -> Claude: "Use JWT with refresh tokens, add rate limiting and input validation"
     -> Consensus: 75% (MEDIUM)

  3. Synthesis
     -> Returns: {consensus_score: 0.75, unified_recommendation: "Use JWT with refresh tokens, add rate limiting, CSRF, and input validation"}

  4. Validation
     -> Returns: {tier1: {passed: true}, tier2: {score: 0.85}, verdict: "APPROVED"}

  5. Reports to user with synthesized recommendation and validation results
```

---

## Skills

Gemini CLI discovers skills from `~/.gemini/skills/` (symlinked from `~/.claude/skills/`).
These integrate with the parallel agent orchestration framework.

### Available Skills

| Skill | Description | Parallel Agents |
|-------|-------------|-----------------|
| `/a11y-audit` | WCAG 2.2 AA accessibility audit | NO |
| `/antipattern-detect` | Detect codebase antipatterns and suggest fixes | NO |
| `/session-checkpoint` | Save context checkpoint for session continuity | NO |
| `/ci-setup` | Configure CI/CD pipelines for target repository | NO |
| `/code-audit` | Auto-triggered security and quality checks | AUTO |
| `/metrics-report` | Visualize agent efficiency metrics | NO |
| `/docs-generate-diagrams` | Generate Mermaid architecture diagrams | CONDITIONAL |
| `/docs-improve` | Diataxis documentation framework analysis | CONDITIONAL |
| `/docs-improve-readme` | Improve README documentation | NO |
| `/env-check` | Verify CLI tools, auth, config, MCP, symlinks | NO |
| `/issue-prioritize` | Score and rank open issues by impact | CONDITIONAL |
| `/issue-triage` | Linear issue audit with duplicate detection | CONDITIONAL |
| `/learning-capture` | Capture structured lessons learned | NO |
| `/performance-check` | Core Web Vitals and bundle analysis | NO |
| `/plan-manage` | Plan lifecycle with parallel agent orchestration | CONDITIONAL |
| `/git-commit` | Full commit pipeline: docs, pull, pre-commits, commit, push | CONDITIONAL |
| `/go-refactor` | Go codebase security and quality analysis | CONDITIONAL (risk-based) |
| `/node-refactor` | Node.js/TypeScript security and quality analysis | CONDITIONAL (risk-based) |
| `/python-refactor` | Python codebase security and quality analysis | CONDITIONAL (risk-based) |
| `/shell-refactor` | Bash/Shell script security and quality analysis | CONDITIONAL (risk-based) |
| `/terraform-refactor` | Terraform IaC security and modularity analysis | CONDITIONAL (risk-based) |
| `/project-scaffold` | Initialize new project with quality gates and Manifest integration | NO |
| `/config-audit` | Detect cross-platform config drift | NO |
| `/ux-review` | UX/accessibility/performance audit | NO |
| `/project-verify` | Run linters, tests, and security scans in parallel | CONDITIONAL |

### Skill Usage

Skills are invoked as slash commands in Gemini CLI. Representative examples:

```bash
/python-refactor src/          # language analysis (also go/node/shell/terraform)
/git-commit "Add feature"  # commit pipeline (omit message to auto-generate)
/project-verify                        # linters, tests, security scans in parallel
/docs-improve-readme                   # docs (also /docs-generate-diagrams, /docs-improve)
/issue-triage                  # Linear backlog audit (also /issue-prioritize)
/plan-manage                   # plan lifecycle
/env-check                  # env sanity (also /config-audit)
/session-checkpoint                    # high-context save (also /learning-capture, /metrics-report)
```

### Auto-Triggered Skill

The `code-audit` skill (symlinked from `~/.claude/skills/code-audit/SKILL.md`)
activates only for an explicit security review request or confirmed changed
behavior at a security boundary. Keywords, file size, function/class counts,
and complexity metrics alone do not activate it; feedback remains inline and
non-blocking.

---

## Configuration Files

All configuration is symlinked from `~/.claude/` to ensure both Claude and Gemini
operate from identical orchestration rules.

| File | Purpose |
|------|---------|
| `~/.claude/config/command_config.yml` | Thresholds, tool policies, error recovery |
| `~/.claude/config/validation_criteria.yml` | Tier 1/Tier 2 validation rules with command overrides |
| `~/.claude/prompts/preflight_analysis.md` | Pre-flight analysis template |
| `~/.claude/prompts/synthesis.md` | Agent disagreement synthesis template |
| `~/.claude/prompts/validation.md` | Validation criteria template |

> Accessible locally via `~/.gemini/config/` and `~/.gemini/prompts/` symlinks.

---

## File Structure

```text
~/.gemini/
├── GEMINI.md                        # This orchestration guide
├── skills/ -> ~/.claude/skills/     # Symlinked shared skills (source of truth)
├── prompts/ -> ~/.claude/prompts/   # Symlinked shared templates
├── config/ -> ~/.claude/config/     # Symlinked shared configs
├── scripts/ -> ~/.claude/scripts/   # Symlinked shared scripts
├── .plans/ -> ~/.claude/.plans/     # Symlinked shared plans
└── settings.json                    # Gemini CLI project settings
```

---

## Plan Management

Plans are markdown files in `~/.claude/.plans/` (symlinked at `~/.gemini/.plans/`)
named `YYYYMMDD-short-description.md` (copy `TEMPLATE.md`). Lifecycle:
CREATE -> ACTIVE (check off deliverables as completed) -> `.archive/` when done
or `.abandoned/` if superseded. Review existing plans before creating new ones;
plans untouched 7+ days should be updated, completed, or abandoned. Use
`/plan-manage` for orchestrated create/review/execute/archive/abandon.

## Command Index

<!-- BEGIN COMMAND INDEX (generate_commands_doc.py --inject-guides) -->
<!-- markdownlint-disable MD013 -->

- **Git & PRs**: `/branch-clean` · `/git-commit` · `/git-find-artifact` · `/issue-dev-auto` · `/issue-sync-commit` · `/issue-sync-pr` · `/pr-address-comments` · `/pr-clean-base` · `/pr-merge-stacked` · `/pr-monitor` · `/pr-reset-reapply` · `/pr-review` · `/pr-triage-bots` · `/repo-clean`
- **Documentation**: `/docs-all` · `/docs-generate-diagrams` · `/docs-improve` · `/docs-improve-readme`
- **Security**: `/ci-audit-triggers` · `/ci-harden-workflow` · `/docker-audit-firewall` · `/llm-audit-traversal` · `/mcp-audit` · `/security-harden-proxy` · `/security-refute-findings` · `/security-review-diff` · `/security-triage-findings`
- **Planning & Specs**: `/data-wire-field` · `/design-validate` · `/issue-prep-auto` · `/issue-prioritize` · `/issue-triage` · `/plan-manage` · `/premise-verify` · `/spec-audit-tasks` · `/spec-decide-tradeoffs` · `/spec-review`
- **Skill Authoring**: `/ai-hooks-integration` · `/prompt-optimize` · `/skill-evolve`
- **CI/CD, Testing & Quality**: `/a11y-audit` · `/ai-code-audit` · `/ci-diagnose-drift` · `/ci-reproduce-failure` · `/ci-setup` · `/data-validate-live` · `/go-refactor` · `/node-refactor` · `/performance-check` · `/project-verify` · `/python-refactor` · `/shell-refactor` · `/smoke-manage` · `/terraform-refactor` · `/test-pin-bug` · `/test-vary-fixtures` · `/ux-review`
- **Infrastructure & Config**: `/api-optimize-bulk` · `/cache-warm-oob` · `/cli-audit-help` · `/config-audit` · `/config-debug-substitution` · `/config-validate-native` · `/data-design-ingestion` · `/deploy-diagnose-drift` · `/deploy-retire-component` · `/docker-compose-commandments` · `/docker-probe-internal` · `/llm-invoke-stdin` · `/pass-cli` · `/process-diagnose-stall` · `/project-scaffold` · `/shell-audit-errexit` · `/shell-audit-pipefail` · `/version-pin`
- **Meta & Orchestration**: `/antipattern-detect` · `/code-audit` · `/env-check` · `/help` · `/learning-capture` · `/memory-compress` · `/metrics-report` · `/session-checkpoint` · `/token-benchmark` · `/token-conserve`
- **Uncategorized**: `/automation-rework-breakeven` · `/code-audit-constitution` · `/code-to-design` · `/delegate` · `/delegate-setup` · `/deploy-reconcile` · `/design-loop` · `/design-md` · `/enhance-prompt` · `/extract-design-md` · `/extract-static-html` · `/false-green-check-audit` · `/generate-design` · `/i-have-adhd` · `/issue-manage` · `/lifecycle-run` · `/loop-scaffold` · `/manage-design-system` · `/parallel-agent` · `/pr-manage` · `/pr-smoke` · `/react-components` · `/react-native` · `/react-vite-dashboard` · `/refactor` · `/remotion` · `/render-verify` · `/review-round` · `/screen-prompts` · `/shadcn-ui` · `/shell-audit` · `/spec-amend` · `/spec-implement-loop` · `/stitch-loop` · `/taste-design` · `/test-isolate-ambient` · `/upload-to-stitch`

Run `/help <query>` for descriptions and when-to-use.

<!-- markdownlint-enable MD013 -->
<!-- END COMMAND INDEX -->
