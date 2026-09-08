---
name: code-audit
description: Auto-trigger when changed behavior crosses a security boundary, or run on an explicit security review request. Gives focused security feedback without blocking user flow.
---

# Code Quality Analysis Skill

This skill activates for changed behavior at a security boundary or for an
explicit security review request. It reviews the behavior and its call path,
not isolated words or identifiers.

## Trigger Criteria

Activate when either condition is true:

1. The user explicitly requests a security review.
2. The change modifies behavior at a security boundary, including an
   authentication or authorization decision, cryptographic operation, secret
   lifecycle, privilege transition, untrusted-input validation boundary, or
   command/data execution boundary.

For implicit activation, confirm the behavior change from the diff and relevant
call path before activating. For an explicit request, inspect the requested
existing code and its relevant call paths even when no diff exists; the request
itself satisfies activation. A variable named `session`, a hash used only for
nonsecurity caching, or generic words such as `input` or `pattern` do not
activate this skill. File size, language, function/class counts, and complexity
metrics are advisory routing context only; they never activate this skill or
force a panel.

## Behavior

When triggered, this skill:

0. **Loads local doctrine and known issues.** Read
   `../../runtime/references/code-constitution.md` and
   `../../runtime/references/antipatterns.md`, then consult the mutable knowledge
   base for the detected language:

   ```bash
   manifest-workspace:learning-capture query --language <detected-language> --format llm
   ```

   If relevant entries exist, include them as additional check items. This is
   **non-blocking** — skip if the query fails or returns empty.

1. **Establishes the review scope.** For implicit activation, confirm the
   changed boundary behavior from the diff and relevant call path. For an
   explicit request, inspect the requested existing code and call paths whether
   or not a diff is present.
2. **Reviews inline by default** with one capable reviewing agent.
3. **Runs applicable deterministic checks** using shell access only for
   check-only linters, tests, and security scanners. Never run `--fix`, a
   formatter that writes, installation, deployment, or remediation commands.
   Record a missing executable as `unavailable` with the reason; never count it
   as a passing check.
4. **Adds independent review only when at least one escalation condition is
   present**:
   - authentication, authorization, cryptography, secret handling, or another
     trust-boundary change;
   - destructive data or infrastructure behavior;
   - a public compatibility or deployment change with broad impact;
   - conflicting evidence or unresolved reviewer uncertainty;
   - a codebase-wide investigation with genuinely independent analysis tracks.
5. **Reports findings inline** without blocking user workflow. When escalation
   is required, use `manifest-workspace:parallel-agent --json --validate
   --analyze <file>` and pin the dispatched reviewer to the configured Sonnet
   tier. Dispatched reviewers do not re-dispatch.

## Sub-agent dispatch

Follow the [bundle-local dispatch selection rules](references/sub-agent-dispatch.md),
but this skill's five consequence/uncertainty conditions
override its generic count threshold. Dispatch only when at least one condition
in step 4 is present; file count or independent-unit count alone is insufficient.
Use native Task dispatch on Claude/Cursor, or
`manifest-workspace:parallel-agent` with an inline fallback on other assistants.
Pass the configured Sonnet tier explicitly. Dispatched reviewers execute their
review directly and do not re-dispatch.

## Analysis Scope

### Security Checks

| Check | Severity | Pattern |
|-------|----------|---------|
| Hardcoded secrets | Critical | `password =`, `secret =`, `api_key =` |
| SQL injection | Critical | f-strings in SQL queries |
| Command injection | Critical | User input in `subprocess`, `os.system` |
| Unsafe deserialization | Critical | `pickle.load`, `yaml.load` (not safe_load) |
| Bare exceptions | High | `except:` without specific exception |
| Empty catch blocks | High | `catch {}` or `catch (e) {}` with no handling |
| Missing input validation | High | External data used without validation |

### Quality Checks

| Check | Severity | Pattern |
|-------|----------|---------|
| God class | Medium | File >500 lines |
| Long function | Medium | Function >100 lines |
| Too many parameters | Low | Function with >5 parameters |
| Missing type hints | Low | Function without return type |
| Magic numbers | Low | Unexplained numeric literals |

### Registry Anti-Patterns (advisory)

On trigger, additionally consult the antipattern entries returned by the
bundle-local `manifest-workspace:learning-capture query` call above. Every entry carrying
exactly one guardrail-category tag (`arch`, `async-state`, `error-handling`,
`security`, `dependency`, `iteration`) — including
`provenance: session-capture` entries added after this skill shipped — defines
a `detection_cue` and a `prevention_rule`.

- Match the code being written/reviewed against the entries' detection cues
  (use the cue for the file's language when the cue is a per-language map).
- For each match, report inline: entry ID, title, severity, and the entry's
  `prevention_rule` as the suggested fix.
- These findings are **advisory and non-blocking** (spec 457 FR-011): they
  never gate or interrupt the workflow. Blocking remains exclusive to the
  Tier 1 validation gates (`validation_criteria.yml`).
- Full per-entry detail: `../../runtime/references/antipatterns.md`. For a
  systematic whole-codebase review, suggest `manifest-code-quality:ai-code-audit` instead of
  expanding inline feedback.

### Optional Semgrep pass

Semgrep is optional. Run it only when the user selected the `semgrep`
capability or the requested audit mode explicitly requires Semgrep. Its absence
must not fail the default inline audit. If a selected or explicitly requested
Semgrep mode lacks the executable, fail that mode with an actionable capability
message instead of silently claiming the scan ran.

## Output Format

When triggered, report findings in this format:

```markdown
## Code Quality Analysis

**File**: `path/to/file.py`
**Triggered by**: [Explicit security review | Security-boundary behavior change]
**review_mode**: [single-agent | escalated]
**escalation_reason**: [none | one or more concrete escalation conditions]

### Checks

| Command | Result | Unavailable reason |
|---------|--------|--------------------|
| `[exact check-only command]` | [pass/fail/unavailable] | [reason or N/A] |

### Findings

| Severity | Issue | Location | Recommendation |
|----------|-------|----------|----------------|
| Critical | Hardcoded API key | Line 45 | Move to environment variable |
| High | Bare exception | Line 112 | Catch specific exception |
| Medium | Long function | Lines 200-350 | Extract helper methods |

### Summary
- Critical: X issues (must fix before merge)
- High: X issues (should fix soon)
- Medium: X issues (refactor when possible)

### Independent Review
- Reviewer: [Key finding, or not run]
- Trigger: [Concrete escalation condition, or none]
```

## Non-Blocking Behavior

This skill provides information without interrupting user workflow:

- **Never blocks** code execution or user commands
- **Reports inline** when patterns detected
- **Suggests fixes** but doesn't auto-apply
- **Escalates only** for Critical severity findings

## Integration with Commands

This skill works alongside `manifest-code-quality:python-refactor`:

- **Skill**: Lightweight, auto-triggered, inline feedback
- **Command**: Comprehensive, user-invoked, full report

When both trigger:

1. Skill provides immediate feedback
2. User can invoke `manifest-code-quality:python-refactor` for detailed analysis
3. Results are complementary, not duplicated

## Configuration

Use this activation contract for the current invocation:

```yaml
any_of:
  - explicit_security_review_request
  - security_boundary_behavior_change
non_triggers:
  - nonsecurity_cache_hash
  - session_variable
  - generic_input_or_pattern_token
  - file_size_or_complexity
```

## Prioritization

When multiple issues found, prioritize by:

1. **Security** - Always first
2. **Correctness** - Bugs and logic errors
3. **Performance** - Efficiency issues
4. **Maintainability** - Code quality
5. **Style** - Formatting and conventions
