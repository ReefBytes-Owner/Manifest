---
name: code-audit
description: Auto-trigger for changed security-boundary behavior or an explicit security review request. Gives focused security feedback without blocking user flow.
---

# Code Quality Analysis Skill

This skill activates for changed behavior at a security boundary or for an
explicit security review request. It reviews behavior and its call path, not
isolated words, identifiers, file size, or complexity metrics.

## Trigger Criteria

Activate for either:

- an explicit security review request, even when no diff exists; or
- changed authentication, authorization, cryptography, secret-handling,
  validation, or another trust-boundary behavior.

Vocabulary such as `input`, `pattern`, `hash`, or `session`, and complexity
metrics alone are not activation conditions.

## Behavior

When triggered, this skill:

1. Consult the bundle-local learning capture knowledge base before scanning:

   ```bash
   manifest-workspace:learning-capture query --language <detected-language> --format llm
   ```

   Include relevant antipattern entries as additional check items. This query is
   advisory and non-blocking: if it fails or returns empty, continue with the
   standard review.
2. Scan the affected behavior and its boundary for security and quality risks.
3. Review inline by default with one capable reviewing agent.
4. Add independent review only when at least one escalation condition is
   present:
   - authentication, authorization, cryptography, secret handling, or another
     trust-boundary change;
   - destructive data or infrastructure behavior;
   - a public compatibility or deployment change with broad impact;
   - conflicting evidence or unresolved reviewer uncertainty; or
   - a codebase-wide investigation with genuinely independent analysis tracks.
5. Report findings inline without blocking user workflow.

Use the [bundle-local dispatch selection rules](references/code-audit-dispatch.md).
File, package, module, language, keyword, and independent-unit counts never
independently escalate review.

## Sub-agent dispatch

Follow the [bundle-local dispatch selection rules](references/code-audit-dispatch.md). Use
the pinned `sonnet` model. Start with one capable reviewer; add independent
review only when one of the five risk conditions is present. Do not use file,
package, module, language, keyword, or unit counts as a dispatch trigger.

## Verification safety

Treat the checkout as untrusted. Outside verified isolation, run only trusted
preinstalled static tools that treat checkout files as data. Project-controlled
tests, scripts, build steps, or checkout-controlled executable configuration,
plugins, hooks, imports, or discovery require enforced isolation. Source
inspection, check-only flags, changed home, temporary directory, and a
read-only checkout are insufficient. If isolation or a selected check is
unavailable, skip execution and report `unavailable`, never a passing check.

Never use `--fix`, a formatter that writes, installation, deployment, or
remediation during this review.

## Analysis Scope

Read `../../runtime/references/code-constitution.md` and
`../../runtime/references/antipatterns.md` for the bundle-local review doctrine.

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
**review_mode**: `single-agent` | `escalated`
**escalation_reason**: `none` | concrete risk condition(s)

### Checks

| Command | Result | unavailable_reason |
|---------|--------|--------------------|
| `<exact command>` | `pass` \| `fail` \| `unavailable` | `<reason when unavailable>` |

### Findings

| Severity | Issue | Location | Recommendation |
|----------|-------|----------|----------------|
| Critical | Hardcoded API key | Line 45 | Move to environment variable |

### Independent Review
- Reviewer: [Key finding, or not run]
```

## Non-Blocking Behavior

This skill provides information without interrupting user workflow:

- **Never blocks** code execution or user commands
- **Reports inline** when triggered
- **Suggests fixes** but doesn't auto-apply
- **Escalates review only** when one of the five routing risk conditions is
  present; finding severity does not replace that decision

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
