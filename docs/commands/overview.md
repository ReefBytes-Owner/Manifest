# Available Commands

> The command surface at a glance.

**Last Updated**: 2026-08-20

## Available Commands

> **Find any command:** run `/help [query]` in-session for searchable, categorized
> discovery, or browse the full generated [Command Reference](../../docs/COMMANDS.md#command-reference)
> (every command, grouped by category, drift-checked in CI). The table below is a
> curated highlight subset.

| Command | Description | Parallel Agents | Validation |
|---------|-------------|-----------------|------------|
| `/help` | Find the right command fast — searchable, categorized discovery (read-only) | NEVER | — |
| `/git-commit` | Full commit pipeline: regenerate docs, pull latest, run pre-commits, commit, push | CONDITIONAL | Tier 1 + Tier 2 |
| `/python-refactor` | Python security, architecture, code quality analysis | CONDITIONAL (risk-based) | Tier 1 + Tier 2 (≥0.80) |
| `/shell-refactor` | Bash/Shell script security and quality with shellcheck | CONDITIONAL (risk-based) | Tier 1 + Tier 2 (≥0.70) |
| `/docs-generate-diagrams` | Generate Mermaid architecture flowcharts and sequence diagrams | CONDITIONAL (≥5 imports) | Tier 2 |
| `/docs-improve` | Analyze docs against Diataxis framework (tutorials, how-tos, reference, explanation) | CONDITIONAL (≥500 lines) | Tier 2 |
| `/docs-improve-readme` | Improve README structure and content following best practices | NEVER | Tier 2 |
| `/issue-prioritize` | Fetch and rank open issues by impact, urgency, readiness, risk (GitHub/GitLab/Linear) | CONDITIONAL (top candidates) | Tier 2 |
| `/issue-triage` | Linear issue audit: duplicates, staleness, priority validation | CONDITIONAL (scenario-based) | Tier 2 |
| `/issue-dev-auto` | Autonomously develop one `auto-dev`-labeled issue test-first and open a PR (never merges); run via `/loop /issue-dev-auto` | NEVER | Tier 1 + Tier 2 |
| `/repo-clean` | Review-then-confirm cleanup sweep of open PRs and stale/merged/gone branches | CONDITIONAL | Tier 1 + Tier 2 |
| `/plan-manage` | Plan lifecycle: create, review, execute, archive, abandon | CONDITIONAL | Tier 2 |
| `/smoke-manage` | Catalog-driven smoke tests; UI steps run via browser-use `mode: agent` | NEVER | Tier 2 |
| `/skill-evolve` | Promote SkillClaw-evolved skills into a review PR (dry-run by default) | NEVER | Tier 2 |

**CLI tools** (installed to `~/.local/bin/`):

| Tool | Description |
|------|-------------|
| `sync-skills` | Sync `.apm/skills/` to all home targets; requires `MANIFEST_ROOT` env var |

---

---

[← Commands Guide](../COMMANDS.md)
