---
name: python-refactor
description: Perform security, architecture, and quality analysis for Python codebases and return a prioritized, actionable refactoring roadmap.
---

# Python Codebase Refactor Analysis

Analyze a Python codebase against best practices, security principles, and enterprise
architecture standards. Generate a comprehensive refactoring report with prioritized
recommendations.

## Review and Verification

Follow the shared [review escalation contract](../../references/review-escalation.md).
Use one reviewing agent by default, run applicable check-only verification, and
add independent review only when that contract's risk conditions require it.

## Task

You are a Senior Principal Security Software Engineer analyzing a production Python codebase. Your goals are to:

1. Assess code against industry best practices and security standards
2. Identify security vulnerabilities (OWASP Top 10, injection risks, secrets handling)
3. Find architectural and code quality refactoring opportunities
4. Rate each finding by **effort** (Minimal/Medium/High) and **risk** (Low/Medium/High/Critical)
5. Generate an actionable improvement roadmap with priority matrix
6. Identify missing tooling configuration files

---

## Instructions

### Step 0: Consult Knowledge Base

Before starting analysis, check for known patterns relevant to this codebase:

```bash
manifest-workspace:learning-capture query --language python --format llm
```

If the knowledge base contains relevant antipatterns or insights for Python:

- Include them as additional check items in your analysis
- Flag any occurrences of known antipatterns with their KB ID (e.g., ANTI-001)
- Note if a known antipattern has been resolved

This step is **non-blocking** — if the knowledge base is empty or the query fails,
proceed with the standard analysis.

### Analysis Checklist

Work through each category; the sequence within and across categories does not matter.

**Project Standards**:

- Read AGENTS.md, CLAUDE.md for project context
- Check for existing tooling: pyproject.toml, ruff.toml, mypy.ini, .pre-commit-config.yaml
- Check package manager: `uv` (recommended), pip, poetry, pdm
- Read pyproject.toml for dependencies (prefer over requirements.txt)
- Check for PEP 735 dependency groups in pyproject.toml
- Check Python version target (>=3.12 recommended for modern features)

**Architecture**:

- List all Python files and check structure
- Check file sizes (detect God classes >500 lines)
- Check for module-level global state
- Check for proper package structure with **init**.py

**Security (CRITICAL)**:

- SQL injection: f-strings in queries (`f"SELECT...WHERE {var}"`)
- Hardcoded secrets: `password =`, `secret =`, `api_key =`, `token =`; hardcoded URLs, emails, hostnames
- Error handling: bare exceptions (`except:`), silent failures without logging
- Dangerous operations: `import pickle`, `eval()`, `exec()`, `yaml.load()` (should be
  `yaml.safe_load()`), `subprocess`, `os.system`, `os.popen`

**Code Quality**:

- Long functions (>100 lines)
- Duplicate code patterns
- Magic numbers/strings
- Dead/unused imports
- Inconsistent naming (snake_case for functions)

**Type Safety**:

- Missing type hints on functions
- Overuse of `Any` type
- Old Optional syntax (prefer `X | None` union syntax, Python 3.10+)
- Missing Pydantic v2 models for data validation
- `pyright` or `mypy` strict mode configuration
- Use of `typing_extensions` for backported features
- Prefer `collections.abc` over `typing` for generic types (Sequence, Mapping)
- Check for structured logging (structlog) over print/basic logging

**Documentation**:

- Module docstrings
- Functions without docstrings
- TODO/FIXME comments

**Testing**:

- Test file inventory
- Test coverage gaps
- Mocking patterns
- Pre-commit hooks configuration

---

## Effort Classification

| Level | Time | Scope | Examples |
|-------|------|-------|----------|
| **Minimal** | 1-2 hours | Single file, config only | Add docstring, extract constant, add type hint |
| **Medium** | 2-8 hours | Multi-file refactor | Create Pydantic model, add unit tests, fix injection |
| **High** | 1-3 days | Architectural | Dependency injection, full mypy compliance, break up God class |

## Risk Classification

| Level | Impact | Testing Required | Examples |
|-------|--------|------------------|----------|
| **Low** | No runtime change | None | Add config file, documentation, type hints |
| **Medium** | Internal changes | Unit tests | Refactor helper functions, add validation |
| **High** | API/signature changes | Integration tests | Change public interfaces, modify providers |
| **Critical** | Security/Breaking | Full regression | Fix injection, remove hardcoded credentials |

---

## Output Format

### Refactor Analysis Report

```markdown
# Refactor Analysis Report

**Date:** YYYY-MM-DD
**Overall Score:** XX/100

---

## Executive Summary

| Category | Score | Issues | Critical |
|----------|-------|--------|----------|
| Security | XX/25 | N | Y/N |
| Code Quality | XX/20 | N | Y/N |
| Architecture | XX/15 | N | Y/N |
| Type Safety | XX/15 | N | Y/N |
| Documentation | XX/10 | N | Y/N |
| Testing | XX/10 | N | Y/N |
| Dependencies | XX/5 | N | Y/N |

**Key Findings:**
- [1-3 sentence summary of most critical issues]

---

## Priority Matrix

### Immediate (Critical Risk - Any Effort)
| ID | Issue | Location | Effort | Risk |
|----|-------|----------|--------|------|
| SEC-001 | SQL injection via f-strings | `file.py:XXX` | Medium | Critical |

### Quick Wins (Low Risk + Minimal Effort)
| ID | Issue | Location | Effort | Risk |
|----|-------|----------|--------|------|
| TS-001 | Add mypy configuration | Root | Minimal | Low |

### Planned (Medium Risk/Effort)
[Table of medium priority items]

### Strategic (High Effort)
[Table of long-term items]

---

## Detailed Findings

[For each finding: Category, Severity, Risk, Effort, Location, Current Code, Issue, Recommended Fix]

---

## Recommendations

### Immediate (This Sprint)
- [ ] Fix SEC-001: ...
- [ ] Create pyproject.toml with Ruff/Mypy configuration

### Short Term (Next 2 Sprints)
- [ ] Add pre-commit hooks
- [ ] Add Pydantic models for API responses

### Long Term (Roadmap)
- [ ] Achieve 80%+ test coverage
```

---

## Configuration Templates

If missing, recommend creating these files:

### pyproject.toml

```toml
[project]
requires-python = ">=3.12"

[dependency-groups]  # PEP 735
dev = ["pytest>=8.0", "ruff>=0.9", "pyright>=1.1"]
test = ["pytest>=8.0", "pytest-cov>=6.0", "pytest-asyncio>=0.24"]

[tool.ruff]
line-length = 120
target-version = "py312"
src = ["src"]

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "C4", "UP", "S", "D", "PT", "SIM", "TCH", "ASYNC"]

[tool.pyright]
pythonVersion = "3.12"
typeCheckingMode = "strict"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

### .pre-commit-config.yaml

Verify this pin against the tool's latest release before recommending it.

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

---

## Analysis Principles

- **Be specific**: Every finding must have exact file:line location
- **Be actionable**: Every finding must have a concrete fix
- **Be accurate**: Verify patterns before reporting (avoid false positives)
- **Be prioritized**: Security issues always come first
- **Be practical**: Focus on high-impact improvements

## Common False Positives to Avoid

- f-strings used for logging (not SQL)
- Test files with intentional bad patterns
- Comments containing keywords (not actual code)
- Type hints that look like code patterns

---

## Learning Capture (Optional)

After completing the analysis, capture the most significant findings:

1. For each critical or high-severity finding:
   - Run:

     ```bash
     manifest-workspace:learning-capture add \
       --category antipattern --language python \
       --title "<finding title>" \
       --description "<finding description and recommended fix>" \
       --source python-refactor --confidence high
     ```

2. For any new tool recommendations discovered:
   - Run:

     ```bash
     manifest-workspace:learning-capture add \
       --category tool_discovery --language python \
       --title "<tool recommendation>" \
       --description "<why this tool is better>" \
       --source python-refactor --confidence medium
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
