---
name: project-verify
description: Use when the user wants a pass/fail quality check on a project — before merging, shipping, or as a PR gate. Detects Python, Go, Node.js, or Terraform and runs lint, test, and security scans, producing one report. Read-only; installs nothing.
---

# Verify Skill

Run a comprehensive quality gate check against a target project or directory.
Detects the project language, runs linting, tests, and security scans in parallel,
and produces a unified report.

## Arguments

- `$ARGUMENTS` -- Path to a project directory (default: current working directory).

## Scope: portable subset, not a registry closure

This skill's Phase 1-2 tool selection is a **standalone portable subset**: it
detects language by indicator file and picks a generic, commonly-available
tool per category (`ruff`, `golangci-lint`, `eslint`, `tflint`, ...) for
*any* target project, with no repository-specific configuration. That is
different from an **explicitly selected project-check closure** such as
Manifest's own `manifest check <profile> [--group G]` (the repository's
`project-checks.json` registry; see its `SHARED_CHECKS.md` for commands and
status vocabulary), which runs a fixed, versioned, per-repository registry of
checks rather than auto-detected generic tools. Do not conflate the two:
when the target project is this Manifest repository itself and an explicit,
versioned check closure is what's wanted, prefer `manifest check` (currently
shadow-only, non-blocking, pending Phase 3 coverage) over this skill's
generic detection.

---

## Phase 0: Consult Knowledge Base

After detecting the project language (Phase 1), check for known issues:

```bash
manifest-workspace:learning-capture query --language <detected-language> --format llm
```

If the knowledge base contains relevant antipatterns or insights:

- Note them in the final report under a "Known Issues" section
- Flag any linter/test failures that match known antipatterns with their KB ID

This phase is **non-blocking** — if the knowledge base is empty or the query fails,
proceed with the standard verification.

---

## Phase 1: Language Detection

Scan the target directory for indicator files to determine the language(s) in use.
Run checks for every detected language.

| Language | Indicator Files |
|----------|----------------|
| Python | `pyproject.toml`, `setup.py`, `requirements.txt`, `*.py` |
| Go | `go.mod`, `go.sum`, `*.go` |
| Node | `package.json`, `tsconfig.json`, `*.ts`, `*.js` |
| Terraform | `*.tf`, `*.tfvars` |

If no language is detected, report an error and stop.

---

## Phase 2: Tool Selection

Based on the detected language, select the tools for each gate category.

| Category | Python | Go | Node | Terraform |
|----------|--------|----|------|-----------|
| **Lint** | `ruff check .` | `golangci-lint run` | `npx eslint .` | `tflint` |
| **Test** | `pytest --tb=short -q` | `go test ./...` | `npx vitest run` | `terraform validate` |
| **Security** | `bandit -r . -q` | `gosec ./...` | `npm audit --audit-level=moderate` | `tfsec .` |

Before running a tool, verify it is installed with `command -v <tool>`. If a tool
is missing, record its category as `skip` with a message explaining which tool to
install, and continue with the remaining tools.

---

## Phase 3: Parallel Execution

Run all three categories (lint, test, security) in parallel using background
processes. Capture both stdout and stderr for each. Apply a 120-second timeout
per tool.

```bash
# Example parallel execution pattern
<lint_command> > /tmp/verify_lint.out 2>&1 &
<test_command> > /tmp/verify_test.out 2>&1 &
<security_command> > /tmp/verify_security.out 2>&1 &
wait
```

---

## Phase 3.5: Smoke Tests (Optional)

After the standard lint/test/security scans complete, check for a smoke catalog
(see the smoke-manage skill):

1. **Detect catalog**: Check if `smoke-catalog/` exists and contains `*.yaml` or `*.yml` files.
2. **If it does**: Execute the deterministic PR-gate tier:

   ```bash
   manifest-code-quality:smoke-manage run --tier Lite
   ```

3. **If runtime deps are missing** (runner reports Playwright/Chromium absent): Record as
   `skip` with the runner's install hint.

4. **If `smoke-catalog/` does not exist**: Skip silently (do not report).

Add smoke test results to the Phase 4 classification and Phase 5 report table as a
`Smoke` category row alongside Lint, Test, and Security.

---

## Phase 4: Result Classification

Classify each category result:

| Exit Code | Classification | Meaning |
|-----------|---------------|---------|
| 0 | **pass** | No issues found |
| 1 | **warn** | Issues found but non-blocking (lint warnings, low-severity findings) |
| 2+ | **fail** | Critical issues (test failures, high-severity vulnerabilities) |
| timeout | **fail** | Tool exceeded 120s timeout |
| skip | **skip** | Tool not installed |

For security scans, further classify by severity:

- High/Critical findings -> **fail**
- Medium findings -> **warn**
- Low/Info findings -> **pass** (noted)

---

## Phase 5: Output Report

```markdown
## Verify Report

**Project**: {absolute-path}
**Language(s)**: {detected-languages}
**Timestamp**: {ISO-8601}

### Results

| Category | Tool | Status | Summary |
|----------|------|--------|---------|
| Lint | ruff | pass | 0 issues |
| Test | pytest | fail | 3 failed, 42 passed |
| Security | bandit | warn | 2 medium-severity findings |

### Details

#### Lint
{trimmed lint output -- first 50 lines}

#### Test
{trimmed test output -- first 50 lines}

#### Security
{trimmed security output -- first 50 lines}

### Verdict

- **PASS**: All categories pass
- **WARN**: No failures but warnings present
- **FAIL**: One or more categories failed
```

---

## Safety Checks

- Never modify source files -- this skill is read-only analysis
- Never install packages or dependencies -- report missing tools and stop
- Respect `.gitignore` patterns -- do not scan ignored directories
- Cap output at 50 lines per category to avoid flooding context

---

## Learning Capture (Optional)

After producing the quality report, if any checks failed:

1. For each failed check category (lint, test, security):
   - Record the tool, failure count, and most common failure pattern
   - Run:

     ```bash
     manifest-workspace:learning-capture add \
       --category antipattern --language <detected> \
       --title "Verify: <tool> failures" \
       --description "<summary of failures>" \
       --source verify --confidence medium
     ```

2. This step is **non-blocking** -- failures in learning capture should not affect the verify report.
