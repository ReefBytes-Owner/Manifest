---
name: go-refactor
description: Security, architecture, and quality analysis for Go codebases with a prioritized fix roadmap. Use for "refactor this Go service", "audit this Go code", "Go code review".
---

# Go Codebase Refactor Analysis

Analyze a Go codebase against best practices, security principles, and idiomatic Go
standards. Generate a comprehensive refactoring report with prioritized recommendations.

## Review and Verification

Follow the shared [review escalation contract](../../references/review-escalation.md).
Use one reviewing agent by default, run applicable check-only verification, and
add independent review only when that contract's risk conditions require it.

## Task

You are a Senior Go Engineer analyzing a production Go codebase. Your goals are to:

1. Assess code against Go idioms, effective Go patterns, and security standards
2. Identify security vulnerabilities (injection, unsafe packages, secrets handling)
3. Find architectural and code quality refactoring opportunities
4. Rate each finding by **effort** (Minimal/Medium/High) and **risk** (Low/Medium/High/Critical)
5. Generate an actionable improvement roadmap with priority matrix

---

## Instructions

### Step 0: Consult Knowledge Base

Before starting analysis, check for known patterns relevant to this codebase:

```bash
manifest-workspace:learning-capture query --language go --format llm
```

If the knowledge base contains relevant antipatterns or insights for Go:

- Include them as additional check items in your analysis
- Flag any occurrences of known antipatterns with their KB ID (e.g., ANTI-001)
- Note if a known antipattern has been resolved

This step is **non-blocking** — if the knowledge base is empty or the query fails,
proceed with the standard analysis.

### Step 1: Read Project Standards and Configuration

- Read AGENTS.md, CLAUDE.md, README.md for project context
- Check for existing tooling: go.mod, go.sum, .golangci.yml, Makefile
- Check Go version and module path
- List dependencies and check for known vulnerabilities via `govulncheck`

### Step 2: Architecture Analysis

- List all Go packages and check module structure
- Check for proper package boundaries (no circular imports)
- Check for God packages (>20 files or >2000 lines)
- Verify interface usage (accept interfaces, return structs)
- Check for proper error wrapping with `fmt.Errorf("...: %w", err)`

### Step 3: Security Analysis (CRITICAL)

Scan for these patterns:

**SQL Injection**:

- String concatenation in SQL: `"SELECT...WHERE " + var`
- `fmt.Sprintf` in queries without parameterized inputs

**Unsafe Operations**:

- `unsafe.Pointer` usage
- `reflect` package in production code (performance/security)
- `os/exec.Command` with user input
- `net/http` without timeouts

**Secrets Handling**:

- Hardcoded credentials, API keys, tokens
- Secrets in struct tags or comments

**Concurrency Safety**:

- Race conditions: shared state without mutex/channels
- Goroutine leaks: goroutines without context cancellation
- Unbuffered channels in loops

**Error Handling**:

- Ignored errors: `result, _ := fn()`
- Bare `panic()` in production code
- Missing error returns from deferred Close()

### Step 4: Code Quality Analysis

- Long functions (>60 lines)
- Deep nesting (>4 levels)
- Magic numbers/strings
- Exported types without documentation
- Inconsistent naming (Go conventions: MixedCaps, not snake_case)
- init() function abuse

### Step 5: Testing Analysis

- Test file coverage: every `foo.go` should have `foo_test.go`
- Table-driven test patterns
- Test helper usage (`t.Helper()`)
- Benchmark tests for performance-critical code
- Fuzz tests for input parsing

### Step 6: golangci-lint Integration

Recommend or check `.golangci.yml` configuration:

```yaml
linters:
  enable:
    - errcheck
    - govet
    - staticcheck
    - gosec
    - ineffassign
    - unused
    - gocritic
    - revive
    - bodyclose
    - noctx
    - exhaustive
```

---

## Effort Classification

| Level | Time | Scope | Examples |
|-------|------|-------|----------|
| **Minimal** | <1 hour | Single file | Add error check, fix naming, add doc comment |
| **Medium** | 2-8 hours | Multi-file | Refactor package, add tests, fix concurrency |
| **High** | 1-3 days | Architectural | Restructure modules, add interfaces, break God packages |

## Risk Classification

| Level | Impact | Testing Required | Examples |
|-------|--------|------------------|----------|
| **Low** | No behavior change | None | Add docs, rename internal var |
| **Medium** | Internal changes | Unit tests | Refactor helpers, add validation |
| **High** | API/signature changes | Integration tests | Change exported interfaces |
| **Critical** | Security/Breaking | Full regression | Fix injection, race conditions |

---

## Output Format

```markdown
# Go Refactor Analysis Report

**Date:** YYYY-MM-DD
**Go Version:** X.XX
**Modules:** N packages
**Overall Score:** XX/100

---

## Executive Summary

| Category | Score | Issues | Critical |
|----------|-------|--------|----------|
| Security | XX/25 | N | Y/N |
| Concurrency | XX/15 | N | Y/N |
| Error Handling | XX/15 | N | Y/N |
| Code Quality | XX/15 | N | Y/N |
| Architecture | XX/15 | N | Y/N |
| Testing | XX/10 | N | Y/N |
| Documentation | XX/5 | N | Y/N |

## Priority Matrix

### Immediate (Critical Risk)
[Table of critical items]

### Quick Wins (Low Risk + Minimal Effort)
[Table of quick wins]

### Planned (Medium Risk/Effort)
[Table of medium items]

### Strategic (High Effort)
[Table of long-term items]

## Detailed Findings
[For each: Category, Severity, Location, Current Code, Issue, Fix]

## Recommendations
[Grouped by timeframe: Immediate, Short Term, Long Term]
```

---

## Configuration Templates

If missing, recommend creating:

### .golangci.yml

```yaml
run:
  timeout: 5m
  go: "1.23"

linters:
  enable:
    - errcheck
    - govet
    - staticcheck
    - gosec
    - ineffassign
    - unused
    - gocritic
    - revive
    - bodyclose
    - noctx

linters-settings:
  gocritic:
    enabled-tags:
      - diagnostic
      - style
      - performance
  revive:
    rules:
      - name: unexported-return
        disabled: true
```

### Makefile

```makefile
.PHONY: lint test build

lint:
 golangci-lint run ./...

test:
 go test -race -coverprofile=coverage.out ./...

build:
 go build -ldflags="-s -w" -o bin/ ./...
```

---

## Analysis Principles

- **Be idiomatic**: Recommend Go patterns, not patterns from other languages
- **Be specific**: Every finding must have exact file:line location
- **Be actionable**: Every finding must have a concrete fix
- **Prioritize concurrency**: Race conditions are production incidents
- **Check error handling**: Go's explicit errors are a feature, not boilerplate

---

## Learning Capture (Optional)

After completing the analysis, capture the most significant findings:

1. For each critical or high-severity finding:
   - Run:

     ```bash
     manifest-workspace:learning-capture add \
       --category antipattern --language go \
       --title "<finding title>" \
       --description "<finding description and recommended fix>" \
       --source go-refactor --confidence high
     ```

2. For any new tool recommendations discovered:
   - Run:

     ```bash
     manifest-workspace:learning-capture add \
       --category tool_discovery --language go \
       --title "<tool recommendation>" \
       --description "<why this tool is better>" \
       --source go-refactor --confidence medium
     ```

3. This step is **non-blocking** -- failures in learning capture should not affect the analysis output.

## Sub-agent dispatch

The [review escalation contract](../../references/review-escalation.md) is the
sole authority for whether to dispatch. Dispatch only when at least one of that
contract's five risk conditions is present; each condition is independently
sufficient. When available, use `sub-agent-dispatch.md` only for mechanism,
configured model selection, and no-recursion guidance. This contract overrides
any count or size threshold in that reference. Use native Task sub-agents on
Claude or the `manifest-workspace:parallel-agent` fallback elsewhere. Explicitly
select the configured **Sonnet** tier (`subagent_model: sonnet`). Dispatched
agents perform their assigned review and do not re-dispatch.
