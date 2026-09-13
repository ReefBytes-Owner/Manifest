# Using Commands

> Invoking skills and commands after your first successful run.

**Last Updated**: 2026-09-12

## Using Commands

Manifest integrates with Claude Code through slash commands.

### Available Commands

#### `/python-refactor` - Code Analysis (risk-based review)

Analyzes Python codebases for security, architecture, and code quality issues.

**Example:**

```bash
# In Claude Code
/python-refactor src/
```

**What it does:**

1. Uses one capable reviewer by default.
2. Runs independent cross-verification only for a trust-boundary or destructive
   change, broad compatibility or deployment impact, conflicting evidence or
   unresolved uncertainty, or genuinely independent codebase-wide tracks.
3. Runs the applicable security, quality, and check-only validation steps.
4. Records the review mode, escalation reason, and each check result; an
   unavailable check is reported as unavailable, never as a pass.

#### `/docs-generate-diagrams` - Architecture Diagrams (Conditional)

Generates Mermaid diagrams for project documentation.

**Example:**

```bash
# In Claude Code
/docs-generate-diagrams docs/ARCHITECTURE.md
```

**Triggers parallel agents when:** Analyzing 5+ unique imports/modules

#### `/docs-improve` - Documentation Analysis (Conditional)

Analyzes documentation against the Diataxis framework.

**Example:**

```bash
# In Claude Code
/docs-improve docs/
```

**Triggers parallel agents when:** Total documentation lines > 500

#### `/docs-improve-readme` - README Enhancement (Never uses parallel agents)

Improves README.md documentation following best practices.

**Example:**

```bash
# In Claude Code
/docs-improve-readme
```

### Command Output Formats

**Markdown (default):**

```bash
~/.claude/scripts/parallel_agent.py "Review this code"
```

**JSON (for programmatic parsing):**

```bash
~/.claude/scripts/parallel_agent.py --json "Review this code"
```

**Full output (no truncation):**

```bash
~/.claude/scripts/parallel_agent.py --json --full-output "Review this code"
```

---

---

[← Getting Started](../GETTING_STARTED.md)
