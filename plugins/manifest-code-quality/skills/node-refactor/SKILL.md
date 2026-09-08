---
name: node-refactor
description: Perform security, architecture, and quality analysis for Node.js/TypeScript codebases and return a prioritized, actionable refactoring roadmap.
---

# Node.js/TypeScript Codebase Refactor Analysis

Analyze a Node.js/TypeScript codebase against best practices, security principles, and
modern ecosystem standards. Generate a comprehensive refactoring report.

## Review and Verification

Follow the shared [review escalation contract](../refactor/references/review-escalation.md).
Use one reviewing agent by default, run applicable check-only verification, and
add independent review only when that contract's risk conditions require it.

## Task

You are a Senior Node.js/TypeScript Engineer analyzing a production codebase. Your goals:

1. Assess code against modern Node.js/TypeScript best practices (ESM, TypeScript strict)
2. Identify security vulnerabilities (OWASP, dependency audit, injection risks)
3. Find architectural and code quality refactoring opportunities
4. Rate each finding by **effort** and **risk**
5. Generate an actionable improvement roadmap

---

## Instructions

### Step 0: Consult Knowledge Base

Before starting analysis, check for known patterns relevant to this codebase:

```bash
manifest-workspace:learning-capture query --language typescript --format llm
```

If the knowledge base contains relevant antipatterns or insights for Node.js/TypeScript:

- Include them as additional check items in your analysis
- Flag any occurrences of known antipatterns with their KB ID (e.g., ANTI-001)
- Note if a known antipattern has been resolved

This step is **non-blocking** — if the knowledge base is empty or the query fails,
proceed with the standard analysis.

### Analysis Checklist

Work through each category; the sequence within and across categories does not matter.

**Project Standards**:

- Read package.json, tsconfig.json, .eslintrc.*or eslint.config.*, .prettierrc
- Check for existing tooling: Vitest/Jest, ESLint flat config, Prettier, Biome
- Inspect dependency tree for known vulnerabilities: `npm audit --json`
- Check Node.js engine requirements and ECMAScript target

**Architecture**:

- Map module structure (ESM vs CJS, barrel exports)
- Check for proper separation of concerns (controllers/services/repositories)
- Identify circular dependencies
- Check for God files (>500 lines) and God functions (>80 lines)
- Verify proper use of dependency injection patterns

**Security (CRITICAL)**:

- Injection risks: template literals in SQL queries without parameterization;
  `eval()`, `Function()` constructor, `vm.runInNewContext()`;
  `child_process.exec()` with unsanitized input (use `execFile` instead);
  Regex DoS (ReDoS) patterns
- Dependency security: `npm audit` findings, outdated packages with known
  CVEs, prototype pollution-prone dependencies, supply chain risk
  (typosquatting, etc.)
- Secrets handling: hardcoded API keys, tokens, credentials; `.env` files
  committed to git; secrets in error messages or logs
- Request handling (if web framework): missing rate limiting, missing input
  validation (zod/joi/yup), missing CORS configuration, missing
  helmet/security headers

**TypeScript Quality**:

- `strict: true` in tsconfig.json
- Usage of `any` type (should be minimal)
- Proper discriminated unions over type assertions
- Zod schemas for runtime validation at boundaries
- Consistent use of `readonly` for immutable data

**Code Quality**:

- Async/await consistency (no mixing callbacks and promises)
- Proper error handling (no swallowed rejections, no empty catch blocks)
- Memory leak patterns (event listeners, intervals without cleanup)
- Consistent file naming convention (kebab-case recommended)

**Testing**:

- Test framework: Vitest (recommended), Jest, or Mocha
- Test coverage gaps
- Integration vs unit test balance
- Mock strategy (dependency injection vs module mocking)

---

## Effort Classification

| Level | Time | Scope | Examples |
|-------|------|-------|----------|
| **Minimal** | <1 hour | Single file | Add type annotation, fix lint, update import |
| **Medium** | 2-8 hours | Multi-file | Add validation schema, refactor module, add tests |
| **High** | 1-3 days | Architectural | Migrate CJS to ESM, add DI, restructure modules |

## Risk Classification

| Level | Impact | Testing Required | Examples |
|-------|--------|------------------|----------|
| **Low** | No behavior change | None | Add types, rename, add docs |
| **Medium** | Internal changes | Unit tests | Refactor helpers, add validation |
| **High** | API changes | Integration tests | Change exports, modify interfaces |
| **Critical** | Security/Breaking | Full regression | Fix injection, patch dependency |

---

## Output Format

```markdown
# Node.js/TypeScript Refactor Analysis Report

**Date:** YYYY-MM-DD
**Node Version:** vXX.x
**TypeScript:** X.X
**Overall Score:** XX/100

## Executive Summary

| Category | Score | Issues | Critical |
|----------|-------|--------|----------|
| Security | XX/25 | N | Y/N |
| TypeScript | XX/15 | N | Y/N |
| Code Quality | XX/20 | N | Y/N |
| Architecture | XX/15 | N | Y/N |
| Dependencies | XX/10 | N | Y/N |
| Testing | XX/10 | N | Y/N |
| Documentation | XX/5 | N | Y/N |

## Priority Matrix
[Immediate / Quick Wins / Planned / Strategic]

## Detailed Findings
[Per finding: Category, Severity, Location, Code, Issue, Fix]

## Recommendations
[Immediate / Short Term / Long Term]
```

---

## Configuration Templates

### tsconfig.json

```json
{
  "compilerOptions": {
    "strict": true,
    "target": "ES2022",
    "module": "Node16",
    "moduleResolution": "Node16",
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true
  }
}
```

### eslint.config.js (flat config)

```javascript
import eslint from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  eslint.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
      },
    },
  }
);
```

---

## Analysis Principles

- **Be ecosystem-aware**: Recommend modern Node.js patterns (ESM, native fetch, etc.)
- **Be specific**: Every finding must have exact file:line location
- **Be actionable**: Every finding must have a concrete fix
- **Check dependencies**: npm audit is a first-class security check
- **Prefer TypeScript strict mode**: `any` is a code smell, not a feature

---

## Learning Capture (Optional)

After completing the analysis, capture the most significant findings:

1. For each critical or high-severity finding:
   - Run:

     ```bash
     manifest-workspace:learning-capture add \
       --category antipattern --language typescript \
       --title "<finding title>" \
       --description "<finding description and recommended fix>" \
       --source node-refactor --confidence high
     ```

2. For any new tool recommendations discovered:
   - Run:

     ```bash
     manifest-workspace:learning-capture add \
       --category tool_discovery --language typescript \
       --title "<tool recommendation>" \
       --description "<why this tool is better>" \
       --source node-refactor --confidence medium
     ```

3. This step is **non-blocking** -- failures in learning capture should not affect the analysis output.

## Sub-agent dispatch

The [review escalation contract](../refactor/references/review-escalation.md) is the
sole authority for whether to dispatch. Dispatch only when at least one of that
contract's five risk conditions is present; each condition is independently
sufficient. When available, use `sub-agent-dispatch.md` only for mechanism,
configured model selection, and no-recursion guidance. This contract overrides
any count or size threshold in that reference. Use native Task sub-agents on
Claude or the `manifest-workspace:parallel-agent` fallback elsewhere. Explicitly
select the configured **Sonnet** tier (`subagent_model: sonnet`). Dispatched
agents perform their assigned review and do not re-dispatch.
