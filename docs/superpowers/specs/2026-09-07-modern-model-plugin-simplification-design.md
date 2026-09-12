# Modern-Model Plugin Simplification Design

**Date:** 2026-09-07
**Status:** Approved after Astra review (2026-09-07)

## Astra Review Disposition

Approved after verifying the revised design against repository source. The four
material findings are resolved: assurance selection and verdict semantics are
explicit; refactor policies permit check-only verification; benchmarks freeze
their baseline and isolate trials with honest telemetry and bounded repair;
command retirement distinguishes active routing from migration history and
requires effective replacement delivery. The security alias preserves inputs and
scope, and delegated-control claims require evidence beyond comments. No material
design blockers remain. This approval covers the design, not implementation or
measured performance claims.

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

Update the corresponding command policies and generated harness guidance to
permit applicable check-only commands. Keep the review read-only: do not enable
automatic fixes, formatting writes, package installation, or deployment. Missing
verification tools are reported as unavailable, never as passing checks.

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

Select the mode with `--assurance standard|high-assurance`; reject unknown values.
Persist it as `assurance` in run context and state. Resume the stored mode; an
explicit conflicting mode must fail rather than silently change the gate.
Standard clarification is performed by the orchestrator against acceptance
criteria; escalate unresolved material questions before implementation. High
assurance retains the separate QA and architecture clarification reviews.

In structured verdicts, `findings` contains only material blockers; optional
`advisories` contains nonblocking observations. `approve` requires empty
`findings`, passing required verification, and satisfied acceptance criteria.
Unknown, malformed, contradictory, or missing verdicts cannot approve a gate.
Legacy verdicts without `advisories` remain valid; do not silently downgrade an
existing finding to an advisory based only on its severity label. Update persona
charters, dispatch templates, and verdict instructions together. High assurance
means unanimous approval with zero material findings in the same iteration.
Keep the retired `runtime/cddl/cddl_loop.py` retired; mode handling belongs to
the active skill orchestration contract, not a revived loop executable.

## 3. Workspace Prompt Cleanup

### Retire `token-conserve`

The command is removed because its behavior already exists in always-on harness
guidance. Its skill directory, command policies, reminder hooks, catalog entry,
capability declarations, generated views, and documentation references are
removed together. This is a deliberate command removal; release notes must name
the always-on replacement behavior.

Verify that the replacement guidance is actually delivered through each supported
harness's install path, including plugin-only installations. Close any missing
delivery path before retiring the command. Remove active recommendations and
routing references; retain historical documents, release notes, and migration
records that deliberately describe the retired command.

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

The alias must preserve the original candidate inputs, scope, and output
contract. Its eventual removal requires a separate migration decision. Claims
that controls moved or were delegated must be verified against functioning
validation and call paths; explanatory comments alone do not refute a finding.

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

Freeze the pre-change full skills and their referenced policy context as benchmark
fixtures before rewriting them. Record source revision, content hashes, and
fixture identity. Compare frozen `full`, `slim`, and `none` conditions using the
same model, reasoning effort, tool access, background instructions, input fixture,
and acceptance criteria. Use fresh isolated trial state with repeated trials and
balanced condition order. Prevent home configuration, installed skills, prior
conversation, and repository guides from contaminating the conditions. An adapter
that cannot establish isolation reports unsupported rather than a scored result.

Run fixture acceptance checks using deterministic outcomes, never lexical answer
matching or an uncalibrated judge alone. Execute generated code only within an
explicitly isolated fixture environment; a temporary directory alone does not
establish execution isolation. Fixture trials must not access live repositories,
credentials, or services beyond the controlled model transport.

Version result records and include suite, condition, fixture hashes, provider,
resolved model/effort, trial identity, verification status, latency, and usage.
Unavailable token or cost telemetry is null with a reason, never zero or inferred
from output length. Keep legacy academic records readable and separate from
workflow aggregates. Bound each initial trial and recovery attempt with a timeout.
For misses, measure up to two recovery attempts under a fixed recovery protocol;
record cumulative observed usage/time and recovered, unresolved, or unavailable
status. Unresolved repairs are censored observations, not zero-cost successes.
Do not claim break-even savings without comparable measured recovery data.

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
- `token-conserve` is absent from active source inventories, generated views,
  hooks, catalogs, and recommendations; deliberate migration/history references
  remain valid, and replacement guidance is delivered on supported install paths;
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
