# Sub-Agent Dispatch & Selection Rules

> Read-on-demand reference (NOT auto-loaded). Skills that fan work out link here instead of
> restating these rules. Indexed from `configs/claude/CLAUDE.md` → "Reference Index".

This repo has **two** sub-agent paradigms. A skill's `tool_policies` entry in
`config/command_config.yml` records which it uses (`subagents` and/or `parallel_agents`); the skill
body states the concrete trigger and links here.

## The two mechanisms

| Mechanism | What it is | Use for | Availability |
|-----------|-----------|---------|--------------|
| **Native Task/Agent sub-agents** | In-session sub-agents dispatched via the Task tool (Explore, general-purpose, …) | Parallel reads, fan-out research, independent per-item work, broad audits, **CDDL personas** | **Claude Code, Cursor** |
| **`parallel_agent.py`** | External multi-CLI cross-verification (Gemini/Cursor/Codex/Antigravity) with consensus scoring | Independent cross-model verification of one artifact/decision | Cross-platform |
| **Headless CLI invoke** (`cddl_invoke.py`, `EVOLVE_CLI`, `SYNTH_CLI`) | Single-provider subprocess using `cli_agents` config | CDDL critics on Gemini/Codex/Agy; synthesis; SkillClaw evolve | Cross-platform (CLI on PATH) |

## Review and cross-verification escalation

This risk gate governs **review and cross-verification only**. It does not
replace a skill's workload-decomposition trigger: `/docs-all`, `/docs-improve`,
and `/issue-prioritize` retain their documented fan-out rules for independent
documents, analysis items, or issues.

Use a single capable reviewer by default. Add independent review only when the
review work has at least one of these conditions:

- a trust-boundary change;
- destructive behavior;
- broad compatibility or deployment impact;
- conflicting evidence or unresolved uncertainty; or
- a codebase-wide investigation with genuinely independent analysis tracks.

Counts of files, packages, modules, languages, keywords, and units never
escalate review by themselves. A skill may choose its cross-verification
mechanism after this risk gate opens: native Task/Agent sub-agents for
independent review work, or `parallel_agent.py` for cross-model verification.
If none of the conditions is present, review inline.

## workload decomposition

For work other than review and cross-verification, follow each skill's own
documented fan-out trigger. Those workload triggers may use counts or other
scale signals and remain independent of the risk gate.

## Model selection (measured — the one cache-safe cost lever)

**Default a dispatched sub-agent to Sonnet unless the task needs more.** Pass an
explicit `model` when dispatching; do not inherit the parent's model by accident.

Measured 2026-07-25 over 47,185 real API requests
(`docs/baselines/2026-07-25-credit-baseline.md`): 63% of sub-agent traffic
already runs Sonnet, but the premium remainder costs **$845** more than it needs
to — Opus sub-agents $503.74→$302.24, Fable sub-agents $919.32→$275.80. Fable is
the bigger half: it bills $10/$50 per MTok, 2x Opus.

Sub-agents are the **only** place a model switch is cache-safe, because each
carries its own context and its own cache. That is what makes this lever work
and the obvious alternative fail:

> **Do not route individual turns within a conversation to a cheaper model.**
> Prompt caches are model-scoped. Main-loop turns average ~150K cache-read
> tokens, so switching model mid-conversation invalidates the prefix and forces
> the next premium turn to pay a full cache **write**. Measured on the most
> attractive candidate class (mechanical tool calls, median output 150 tokens):
> **$129 saved against a $1,628 penalty — net −$1,499.** This is the intuitive
> optimisation and it loses money; it is rejected on evidence, not preference.

Escalate a sub-agent above Sonnet only for genuinely hard reasoning. Mechanical
fan-out (file reads, greps, per-item transforms) is Haiku-eligible and roughly
halves the Sonnet figure again.

### Enforcement

Every skill with `subagents: always|conditional` declares a `subagent_model` in
`config/command_config.yml`, and its `## Sub-agent dispatch` section states the
same model. Both are gated by `tests/bats/subagent_policy.bats` (checks T7/T8),
enumerated from the disposition — a new dispatching skill fails until it pins a
model, with no name list to maintain.

| `subagent_model` | Use for |
|---|---|
| `sonnet` | **The default.** Any dispatch that is not one of the rows below. |
| `haiku` | Purely mechanical fan-out: file reads, greps, per-item transforms. |
| `opus` | Genuinely hard reasoning — adversarial verification of security or correctness findings. |
| `charter` | Per-role tiers declared in the CDDL charters (`cddl-role-models.md`). |

Ad-hoc dispatches outside a skill (Explore, general-purpose, one-off fan-out)
are not reachable by that gate, so the same default is stated as a rule in the
always-loaded orchestration guides' Token Economy section.

## No recursion

A dispatched sub-agent performs its assigned task **directly** and does **not** itself fan out
further sub-agents. This prevents agent-explosion.

## Cross-platform fallback

| Platform | Native Task | Fallback for fan-out / CDDL critics |
|----------|-------------|-------------------------------------|
| Claude Code | Yes | — |
| Cursor | Yes | — |
| Gemini CLI, Codex, Antigravity | No | `cddl_invoke.py`, `parallel_agent.py`, or inline |

Never leave an assistant without an executable path. Headless seams share
`parallel_agent.yml` → `cli_agents` and `SYNTH_*` / `CDDL_INVOKE_*` / `EVOLVE_*` env overrides.

---

## Convention: adding sub-agent guidance to a skill

### 1. Classify the skill

Record whether it can use native sub-agents or external cross-model review.
Use `never` for single-step, sequential, or shared-state work; use
`conditional` when the shared risk gate can justify independent review. This
classification does not create a count-based trigger.

### 2. Record it in `config/command_config.yml`

```yaml
tool_policies:
  <skill-name>:
    subagents: conditional
    subagent_trigger: "trust_boundary_change OR destructive_behavior OR broad_compatibility_or_deployment_change OR conflicting_evidence_or_unresolved_uncertainty OR codebase_wide_independent_tracks"
    subagent_model: sonnet
```

For `never`, record `subagent_rationale` in the policy or a one-line marker in
the skill body.

### 3. Add the in-body policy

State that a single capable reviewer is the default, name the five conditions,
and require inline review otherwise. Link here rather than duplicating
mechanics. Dispatched sub-agents perform their assigned work directly and do
not re-dispatch.

### 4. Verify

```bash
bats tests/bats/subagent_policy.bats
yamllint configs/claude/config/command_config.yml
```
