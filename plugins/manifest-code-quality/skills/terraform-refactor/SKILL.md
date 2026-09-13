---
name: terraform-refactor
description: Perform security, modularity, and quality analysis for Terraform/OpenTofu IaC codebases and return a prioritized, actionable refactoring roadmap.
---

# Terraform IaC Refactor Analysis

Analyze Terraform/OpenTofu infrastructure-as-code against security best practices,
module composition patterns, and state management standards. Generate a comprehensive
refactoring report with prioritized recommendations.

## Review routing

Use one capable reviewer by default. Add independent review only when a
condition in the [review escalation contract](../refactor/references/review-escalation.md)
is present; file, package, module, language, keyword, and unit counts do not
independently escalate review.

## Task

You are a Senior Infrastructure/Platform Engineer analyzing production Terraform code.
Your goals:

1. Assess IaC against security standards (tfsec, checkov, Sentinel patterns)
2. Identify misconfigurations, drift risks, and blast radius issues
3. Find modularity and composition improvements
4. Rate each finding by **effort** and **risk**
5. Generate an actionable improvement roadmap

---

## Instructions

### Step 0: Consult Knowledge Base

Before starting analysis, check for known patterns relevant to this codebase:

```bash
manifest-workspace:learning-capture query --language terraform --format llm
```

If the knowledge base contains relevant antipatterns or insights for Terraform/OpenTofu:

- Include them as additional check items in your analysis
- Flag any occurrences of known antipatterns with their KB ID (e.g., ANTI-001)
- Note if a known antipattern has been resolved

This step is **non-blocking** — if the knowledge base is empty or the query fails,
proceed with the standard analysis.

### Analysis Checklist

Read README.md and CLAUDE.md for project context; check for existing tooling
(`.terraform-version`, `.tflint.hcl`, `.checkov.yaml`); inspect provider versions
and `required_providers` blocks; check backend configuration (state storage,
locking). Then work each category below — order does not matter.

### Architecture

- Map module structure (root vs child modules)
- Check for module composition (DRY principles)
- Identify monolithic configurations (>500 lines per file)
- Verify proper use of workspaces vs separate state files
- Check for hardcoded values that should be variables

### Security (CRITICAL)

*Access Control*:

- Overly permissive IAM policies (`*` actions or resources)
- Missing least-privilege boundaries
- Public access to storage buckets, databases, or APIs
- Missing encryption at rest and in transit

*Network Security*:

- Security groups with `0.0.0.0/0` ingress
- Missing VPC flow logs
- Public subnets for private resources
- Missing WAF or DDoS protection

*Secrets Management*:

- Hardcoded secrets in `.tf` files or `terraform.tfvars`
- Missing integration with secret managers (Vault, AWS SSM, etc.)
- Secrets in state file (sensitive = true missing)

*State Security*:

- Local state files (should be remote with locking)
- State file without encryption
- Missing state file access controls

### Module Quality

- Input validation with `validation` blocks
- Proper use of `locals` for computed values
- Output descriptions and documentation
- Version constraints on modules and providers
- Consistent file structure: main.tf, variables.tf, outputs.tf, versions.tf

### Drift & Lifecycle

- Resources with `ignore_changes` lifecycle rules (intentional or hiding drift?)
- Missing `prevent_destroy` on critical resources
- Proper tagging strategy (cost allocation, ownership, environment)
- Resource naming conventions

### Testing

- Terratest or tftest integration tests
- `terraform validate` and `terraform plan` in CI
- Policy-as-code (OPA/Sentinel/Checkov)

---

## Effort Classification

| Level | Time | Scope | Examples |
|-------|------|-------|----------|
| **Minimal** | <1 hour | Single resource | Add tags, fix variable description, add sensitive flag |
| **Medium** | 2-8 hours | Module refactor | Extract module, add validation, fix IAM policy |
| **High** | 1-3 days | Architectural | State migration, module restructure, add testing |

## Risk Classification

| Level | Impact | Testing Required | Examples |
|-------|--------|------------------|----------|
| **Low** | No infra change | `terraform plan` | Add descriptions, tags, locals |
| **Medium** | Non-destructive change | Plan review | Add variables, outputs, lifecycle rules |
| **High** | Resource modification | Staging first | Change IAM, security groups, encryption |
| **Critical** | Destructive/Security | Full DR test | State migration, access control, secrets |

---

## Output Format

```markdown
# Terraform Refactor Analysis Report

**Date:** YYYY-MM-DD
**Terraform Version:** X.X
**Providers:** N
**Modules:** N
**Overall Score:** XX/100

**review_mode**: `single-agent` | `escalated`
**escalation_reason**: `none` | concrete risk condition(s)

## Checks

| Command | Result | unavailable_reason |
|---------|--------|--------------------|
| `<exact command>` | `pass` \| `fail` \| `unavailable` | `<reason when unavailable>` |

## Executive Summary

| Category | Score | Issues | Critical |
|----------|-------|--------|----------|
| Security | XX/30 | N | Y/N |
| Module Quality | XX/20 | N | Y/N |
| State Management | XX/15 | N | Y/N |
| Code Quality | XX/15 | N | Y/N |
| Testing | XX/10 | N | Y/N |
| Documentation | XX/10 | N | Y/N |

## Priority Matrix
[Immediate / Quick Wins / Planned / Strategic]

## Detailed Findings
[Per finding with blast radius assessment]

## Recommendations
[Immediate / Short Term / Long Term]
```

---

## Configuration Templates

### .tflint.hcl

```hcl
config {
  call_module_type = "all"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "aws" {
  enabled = true
  version = "0.35.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}
```

---

## Analysis Principles

- **Assess blast radius**: Every finding should note what breaks if changed
- **Be specific**: Include resource addresses (module.x.resource.y)
- **Security first**: IAM and network misconfigurations are production incidents
- **State is sacred**: State migrations require extreme care
- **Plan before apply**: Every recommendation should be verifiable with `terraform plan`

---

## Learning Capture (Optional)

After completing the analysis, capture the most significant findings:

1. For each critical or high-severity finding:
   - Run:

     ```bash
     manifest-workspace:learning-capture add \
       --category antipattern --language terraform \
       --title "<finding title>" \
       --description "<finding description and recommended fix>" \
       --source terraform-refactor --confidence high
     ```

2. For any new tool recommendations discovered:
   - Run:

     ```bash
     manifest-workspace:learning-capture add \
       --category tool_discovery --language terraform \
       --title "<tool recommendation>" \
       --description "<why this tool is better>" \
       --source terraform-refactor --confidence medium
     ```

3. This step is **non-blocking** -- failures in learning capture should not affect the analysis output.

## Sub-agent dispatch

Follow the [dispatch mechanics](references/terraform-refactor-dispatch.md) and
the [review escalation contract](../refactor/references/review-escalation.md). Use
the pinned `sonnet` model. Start with one capable reviewer; add independent
review only when at least one of that contract's five risk conditions is
present. This overrides any count or size threshold. Check commands are
check-only. Unavailable checks are reported as `unavailable`, never pass.
