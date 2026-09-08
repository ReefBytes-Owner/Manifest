# Claude Orchestration Guide

This document defines how Claude should leverage parallel LLM agents
(Gemini, Cursor, Claude CLI, Codex, Antigravity, Devin) for cross-verification, planning, and validation.

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

## Manifest CLI

**Entry point**: `manifest parallel-agent` (`~/.local/bin/manifest`). Legacy
`parallel_agent.py` forwards with a deprecation warning.

### Quick Usage

**IMPORTANT**: always use **absolute paths** (agents run from different working
directories) and a **large timeout** (600-900s; the 120s default is often
insufficient).

Default MCP/tool routing — use the matching tool when the task domain matches.
**Always registered**: **Context7** library/API docs, setup, config · **Semgrep
CLI** local SAST, vulnerabilities, secrets (a CLI, not MCP).

**Opt-in, not present unless installed** (`./bootstrap.sh --install-mcp`; every
`npx mcp-remote` server costs a subprocess per invocation, so the shipped set is
deliberately Context7 only — #646): **Sentry** runtime errors, stack traces,
release regressions · **Linear** / **Atlassian** issue requirements, acceptance
criteria · **DeepWiki** unfamiliar repos, dependency internals · **Glean**
internal runbooks, ADRs · **Google Dev Docs** Firebase/Cloud/Android/Maps ·
**Apify** web scraping · **OpenTofu** provider/module docs. Check the tool list
before routing to one of these; do not assume it is available.

```bash
# Code review, all 5 agents, 10 min timeout
manifest parallel-agent --json --timeout 600 --review /absolute/path/to/file

# Full analysis (flags: parallel-agent.md)
manifest parallel-agent --json --validate --timeout 900 --analyze /abs/path
```

## Reference Index

Read on demand (NOT auto-loaded). You MUST read the reference before related tasks:

- `~/.claude/references/parallel-agent.md` — Read for flag specs, JSON schema validation, or resolving Credit Exhaustion.
- `~/.claude/references/orchestration.md` — Read when running multi-agent validation or debugging cross-verification failures.
- `~/.claude/references/git-platform.md` — Read when automating PRs, branch detection, or git_ops failures.
- `~/.claude/references/layout.md` — Read when modifying config trees or mapping file locations.
- `~/.claude/references/sub-agent-dispatch.md` — Read before a skill dispatches sub-agents: native Task vs
  `manifest parallel-agent`, when-to-dispatch threshold, model pinning, cross-platform fallback.
- `~/.claude/references/spec-artifact-discovery.md` — Read before a spec-* skill reads
  planning artifacts: speckit vs superpowers layout detection + precedence.
- `~/.claude/references/code-constitution.md` — Read BEFORE creating or modifying
  source: 13 articles, ceilings, per-language annexes (`constitution/<lang>.md`).
- `~/.claude/references/antipatterns.md` — Read before writing or refactoring code:
  guardrail registry (detection cues + prevention rules).
- `~/.claude/references/doc-concision.md` — Read before writing or auditing docs:
  per-type line caps, fan-out-to-sub-pages rule, fluff blocklist (`docs_lint.py`).
- `~/.claude/references/harness-routing.md` — Read before cross-harness
  skill/agent work: per-harness execution semantics + model tiers.

## Proactive Coding Guardrails (always on)

While writing: propagate error signals (never log-and-drop); validate inputs at
boundaries; secrets from env only; await/route every async op; pair
setup/teardown; serialize shared writes; refactor before accreting; no
speculative guards, single-use abstractions, or dead code; verify new deps
exist. When refining, NEVER silently remove security controls or validation.
Registry: `~/.claude/config/knowledge_base.yml`; `/ai-code-audit` = full audit.

## Proactive Decision Framework

Use one capable agent by default. Add independent review only for a trust-boundary
change, destructive behavior, a broad public compatibility or deployment change,
conflicting evidence or unresolved uncertainty, or a codebase-wide investigation
with genuinely independent tracks. File size, language, generic keywords, and
independent-unit counts are advisory context; they do not trigger a panel.

## Validation Criteria

- **Tier 1 (blocking)**: cross-verification, security, error handling, breaking
  changes. **Tier 2 (advisory)**: bugs, performance, maintainability, tests.
- Authoritative weights: `~/.claude/config/validation_criteria.yml`. Consensus
  thresholds and verdict rules (`APPROVED`/`NEEDS_REVIEW`/`BLOCKED`):
  `~/.claude/references/orchestration.md`.

## Skills

Skills live in `~/.claude/skills/` (deployed from the repo's
`.apm/skills/`). Each skill's `SKILL.md` frontmatter (`name`,
`description`) is the **authoritative registry** — Claude Code auto-loads every
description at session start, so no table is duplicated here. Per-skill
parallel-agent policy (always/conditional/never) lives in
`~/.claude/config/command_config.yml` under `tool_policies`.

Common entry points: `/git-commit`, `/project-verify`, `/<lang>-refactor`,
`/docs-all`, `/plan-manage`, `/env-check`, `/session-checkpoint`,
`/version-pin`. `/help` searches the full catalog by task.

**Skills are plugin bundles**: `/<bundle>:<name>`; refresh with
`claude plugin update <bundle>@manifest`. Others read `~/.manifest/skills`.

### Security Review Skill

`code-audit` activates for an explicit security-review request or a confirmed
change in behavior at a security boundary. Keywords and complexity metrics alone
do not activate it. Feedback remains inline and non-blocking.

## Plan Management

Plans are `~/.claude/.plans/YYYYMMDD-short-description.md` (copy `TEMPLATE.md`);
lifecycle CREATE → ACTIVE → `.archive/` or `.abandoned/`. Review existing plans
before creating one; 7+ days untouched means update, complete, or abandon.
`/plan-manage` runs the whole lifecycle. Details: `~/.claude/.plans/README.md`.
