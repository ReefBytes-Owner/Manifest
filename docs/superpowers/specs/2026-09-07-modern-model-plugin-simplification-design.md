# Modern-Model Plugin Simplification Design

**Date:** 2026-09-07
**Status:** Approved in chat; awaiting written-spec review

## Goal

Reduce prompt and review overhead that no longer earns its cost with modern
reasoning models while preserving deterministic verification, durable state,
provider integrations, security boundaries, and explicit high-assurance modes.

## Scope

This change implements five concrete improvements across `manifest-code-quality`,
`manifest-security`, `manifest-spec-planning`, and `manifest-workspace`:

1. Risk-based escalation for language refactor reviews.
2. Proportional completion criteria for the critic-gated implementation loop.
3. Retirement of redundant token-conservation prompting and correction of
   memory-compression guarantees.
4. Consolidation of security-finding triage and narrower code-audit triggers.
5. Workflow-representative token and quality benchmarks.

The broader `manifest-docs`, `manifest-ops`, and `stitch-design` bundle
consolidations are excluded. The review identified general opportunities there,
but no individual retirement has enough evidence yet.

## Design Principles

- A stronger model reduces the need for generic reasoning instructions, not the
  need for tools, repository policy, external state, or deterministic checks.
- Escalation is proportional to consequence and uncertainty.
- Findings gate completion only when they are material to requirements,
  correctness, security, or operability.
- Existing command names remain compatible unless their behavior is already
  supplied continuously by the harness.
- Generated plugin views are derived from source manifests and skills; they are
  never maintained as independent sources.

## 1. Risk-Based Review Escalation

The Python, Node/TypeScript, Go, Shell, and Terraform refactor skills will stop
requiring parallel agents for every invocation.

The default path uses one capable reviewing agent plus applicable deterministic
linters, tests, and security scanners. Independent review is added when at least
one of these conditions is present:

- authentication, authorization, cryptography, secret handling, or another
  trust-boundary change;
- destructive data or infrastructure behavior;
- a public compatibility or deployment change with broad impact;
- conflicting evidence or unresolved reviewer uncertainty;
- a codebase-wide investigation with genuinely independent analysis tracks.

File size, language, or a generic keyword alone cannot force fan-out. Reports
must state whether review was single-agent or escalated and identify the trigger.

## 2. Proportional Spec Implementation Loop

`spec-implement-loop` gains two assurance modes:

- **standard** (default): implementation, deterministic verification, and one
  independent review. Completion requires satisfied acceptance criteria, passing
  required checks, and no unresolved material findings.
- **high-assurance**: the existing separated developer, developer-reviewer, QA,
  and architecture personas. All reviewers must agree that no material findings
  remain in the same iteration.

Cosmetic preferences, optional improvements, and unsupported speculation do not
block either mode. They may be reported as advisory observations. The existing
run artifacts, iteration ceiling, dirty-tree protections, and no-commit policy
remain intact.

## 3. Workspace Prompt Cleanup

### Retire `token-conserve`

The command is removed because its behavior already exists in always-on harness
guidance. Its skill directory, command policies, reminder hooks, catalog entry,
capability declarations, generated views, and documentation references are
removed together. This is a deliberate command removal; release notes must name
the always-on replacement behavior.

### Correct `memory-compress`

The skill will describe summaries as selective, potentially lossy artifacts.
It must preserve the original source record or point to its durable location,
identify the summary's intended use, retain required identifiers and decisions,
and never claim zero information loss. `session-checkpoint` remains separate and
unchanged because resumability is an operational capability.

## 4. Security Skill Consolidation

`security-triage-findings` becomes the canonical evidence-based refutation gate.
It will absorb the useful removed/delegated-control guidance currently emphasized
by `security-refute-findings`.

`security-refute-findings` remains temporarily as a deprecated compatibility
alias that routes to the canonical skill. It must not duplicate the full policy.
Catalog and command documentation mark the alias clearly.

`code-audit` will trigger from changed behavior at a security boundary or from an
explicit security review request. Generic tokens such as `input`, `pattern`,
`hash`, and `session` are insufficient by themselves. Complexity metrics remain
advisory routing signals and do not automatically invoke a multi-agent panel.

## 5. Benchmark Modernization

The benchmark gains a workflow suite representing Manifest's real decisions.
Each workflow is evaluated in three conditions:

1. no skill context;
2. a slim skill containing only constraints, tools, and success criteria;
3. the current full skill.

The initial workflows cover code review, security finding triage, implementation
against acceptance criteria, and repository documentation. Each condition records
task correctness, required-constraint compliance, latency, input/output tokens,
and repair cost after a miss. Repair cost is measured by recovery runs rather than
assumed.

The existing MMLU, HumanEval, HellaSwag, and TruthfulQA prompts remain available
as a secondary regression suite. They cannot be the primary evidence for keeping
or retiring a Manifest skill. Reports separate workflow results from academic
regression results.

## Compatibility and Migration

- Removing `token-conserve` is the only intentional command deletion.
- `security-refute-findings` remains callable during the deprecation window.
- Existing `spec-implement-loop` callers receive standard mode unless they opt
  into high assurance.
- Provider model IDs continue to resolve through central configuration; no skill
  hardcodes Fable, Astra, Sol, or other model IDs.
- Source manifests, command catalogs, generated harness views, and capability
  fixtures must agree after regeneration.

## Testing

Tests are written before behavior changes and must prove:

- all language refactor skills contain the shared conditional-escalation contract
  and no unconditional fan-out language;
- standard and high-assurance loop contracts have distinct completion criteria;
- `token-conserve` is absent from source inventories, generated views, hooks,
  catalogs, and user documentation;
- memory compression makes no lossless guarantee and preserves source provenance;
- the compatibility security alias routes to the canonical triage skill;
- broad keywords alone do not satisfy the code-audit trigger contract;
- benchmark result schemas distinguish workflow/academic suites and all three
  context conditions, including measured repair cost;
- bundle-link, generated-view, capability-matrix, and command-document checks pass.

## Non-Goals

- Removing operational plugins or provider integrations.
- Benchmarking proprietary model families against one another in this change.
- Rewriting every skill for brevity without workflow evidence.
- Changing security controls, deployment safety, Git ownership, or credential
  handling.
