# Validation & Consensus

> How skills are processed and how agent output is scored into a verdict.

**Last Updated**: 2026-09-12

## Skill Processing Architecture

How slash commands (skills) are processed from user input to execution with parallel agent integration.

```mermaid
%%{init: {'theme':'neutral'}}%%
flowchart LR
    classDef input fill:#f0f9ff,stroke:#0284c7,color:#0c4a6e
    classDef process fill:#f0fdf4,stroke:#16a34a,color:#14532d
    classDef decision fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef external fill:#3b82f6,stroke:#1d4ed8,color:#fff

    USER["User: /skill-name args"]:::input

    subgraph "Skill Layer"
        PARSE["Parse Skill & Args"]:::process
        LOAD_CMD["Load Skill Definition<br/>(SKILL.md)"]:::process
    end

    subgraph "Review Preflight"
        CHECK_CRITERIA{"risk-based review<br/>escalation?"}:::decision
        TRIGGER["Escalate only for:<br/>- Trust boundary or destructive behavior<br/>- Broad compatibility/deployment impact<br/>- Unresolved uncertainty<br/>- Independent codebase-wide tracks"]:::process
    end

    subgraph "Execution"
        SINGLE["Single Agent Execution"]:::process
        PARALLEL["Parallel Agent Execution<br/>(parallel_agent.py)"]:::external
    end

    subgraph "Post-Processing"
        SYNTHESIS{"Consensus<br/>< 80%?"}:::decision
        SYNTH_AGENT["Synthesis Agent<br/>(resolve disagreements)"]:::external
        VALIDATION["Validation Agent<br/>(check against criteria)"]:::external
    end

    OUTPUT["Return Result to User"]:::input

    USER --> PARSE
    PARSE --> LOAD_CMD
    LOAD_CMD --> CHECK_CRITERIA

    CHECK_CRITERIA -->|Escalated| PARALLEL
    CHECK_CRITERIA -->|Single-agent| SINGLE

    PARALLEL --> SYNTHESIS
    SINGLE --> VALIDATION

    SYNTHESIS -->|Yes| SYNTH_AGENT
    SYNTHESIS -->|No| VALIDATION
    SYNTH_AGENT --> VALIDATION
    VALIDATION --> OUTPUT
```

**Command Types**:

- **Risk-based review escalation**: `/python-refactor`, `/shell-refactor`, and
  other refactor skills use independent cross-verification only when the
  review-risk gate opens.
- **Workload-based fan-out**: `/docs-generate-diagrams` (5+ modules) and
  `/docs-improve` (500+ total documentation lines) retain their own scale
  triggers; those triggers are not review escalation.
- **No parallel work**: `/docs-improve-readme` (straightforward documentation).

---

## Validation Pipeline

How code changes and agent outputs are validated against Tier 1 (critical) and Tier 2 (quality) criteria.

```mermaid
%%{init: {'theme':'neutral'}}%%
flowchart TD
    classDef input fill:#f0f9ff,stroke:#0284c7,color:#0c4a6e
    classDef tier1 fill:#ef4444,stroke:#dc2626,color:#fff
    classDef tier2 fill:#eab308,stroke:#a16207,color:#fff
    classDef process fill:#f0fdf4,stroke:#16a34a,color:#14532d
    classDef success fill:#22c55e,stroke:#166534,color:#fff
    classDef blocked fill:#dc2626,stroke:#991b1b,color:#fff

    CODE["Code/Agent Output"]:::input
    LOAD_CRITERIA["Load validation_criteria.yml"]:::process

    subgraph "Tier 1: Critical Checks (Blocking)"
        CROSS_VERIFY["Cross-Verification<br/>(escalated review only)"]:::tier1
        SECURITY["Security Issues<br/>(weight: 0.3)"]:::tier1
        ERROR_HANDLE["Error Handling<br/>(weight: 0.2)"]:::tier1
        BREAKING["Breaking Changes<br/>(weight: 0.2)"]:::tier1
    end

    TIER1_CHECK{"All Tier 1<br/>Pass?"}:::tier1
    BLOCKED_VERDICT["VERDICT: BLOCKED"]:::blocked

    subgraph "Tier 2: Quality Checks (Advisory)"
        BUG_DETECT["Bug Detection<br/>(weight: 0.25)"]:::tier2
        PERFORMANCE["Performance<br/>(weight: 0.25)"]:::tier2
        MAINTAIN["Maintainability<br/>(weight: 0.25)"]:::tier2
        TEST_COV["Test Coverage<br/>(weight: 0.25)"]:::tier2
    end

    TIER2_SCORE["Calculate Tier 2 Score<br/>(weighted sum)"]:::process

    TIER2_CHECK{"Score<br/>≥ 0.60?"}:::tier2

    APPROVED["VERDICT: APPROVED"]:::success
    NEEDS_REVIEW["VERDICT: NEEDS_REVIEW"]:::tier2

    CODE --> LOAD_CRITERIA
    LOAD_CRITERIA --> SECURITY
    LOAD_CRITERIA -->|escalated review| CROSS_VERIFY
    CROSS_VERIFY --> SECURITY
    SECURITY --> ERROR_HANDLE
    ERROR_HANDLE --> BREAKING
    BREAKING --> TIER1_CHECK

    TIER1_CHECK -->|Yes| BUG_DETECT
    TIER1_CHECK -->|No| BLOCKED_VERDICT

    BUG_DETECT --> PERFORMANCE
    PERFORMANCE --> MAINTAIN
    MAINTAIN --> TEST_COV
    TEST_COV --> TIER2_SCORE
    TIER2_SCORE --> TIER2_CHECK

    TIER2_CHECK -->|Yes| APPROVED
    TIER2_CHECK -->|No| NEEDS_REVIEW
```

**Validation Verdicts**:

- **APPROVED**: All Tier 1 pass, Tier 2 ≥ 0.60 → Safe to proceed
- **NEEDS_REVIEW**: All Tier 1 pass, Tier 2 < 0.60 → Manual review recommended
- **BLOCKED**: Any Tier 1 fails → Changes rejected

**Command-Specific Overrides**:
Commands can override default thresholds in `validation_criteria.yml`:

```yaml
command_overrides:
  python-refactor:
    tier1:
      security_issues: 0.5  # Higher weight for Python security
  git-commit:
    tier2:
      test_coverage: 0.0    # Don't require tests for commits
```

---

## Cross-Verification Consensus

How agent outputs are compared and consensus scores are calculated for decision-making.

```mermaid
%%{init: {'theme':'neutral'}}%%
flowchart TD
    classDef input fill:#f0f9ff,stroke:#0284c7,color:#0c4a6e
    classDef process fill:#f0fdf4,stroke:#16a34a,color:#14532d
    classDef high fill:#22c55e,stroke:#166534,color:#fff
    classDef medium fill:#eab308,stroke:#a16207,color:#fff
    classDef low fill:#ef4444,stroke:#dc2626,color:#fff

    OUTPUTS["Agent Outputs<br/>(Gemini, Cursor, Claude)"]:::input

    EXTRACT["Extract Key Findings<br/>(Issues, Recommendations, Risks)"]:::process

    COMPARE["Compare Findings Across Agents"]:::process

    COUNT_AGREE["Count Agreements<br/>(Same finding from 2+ agents)"]:::process
    COUNT_TOTAL["Count Total Unique Findings"]:::process

    CALC["Calculate Consensus Score<br/>(Agreements / Total * 100)"]:::process

    SCORE_CHECK{"Consensus<br/>Score?"}:::process

    HIGH["≥80% - High Confidence<br/>✓ Unified recommendation<br/>✓ Auto-proceed"]:::high
    MEDIUM["50-79% - Medium Confidence<br/>⚠ Show disagreements<br/>⚠ User review recommended"]:::medium
    LOW["<50% - Low Confidence<br/>✗ Escalate to user<br/>✗ Manual decision required"]:::low

    OUTPUTS --> EXTRACT
    EXTRACT --> COMPARE
    COMPARE --> COUNT_AGREE
    COMPARE --> COUNT_TOTAL
    COUNT_AGREE --> CALC
    COUNT_TOTAL --> CALC
    CALC --> SCORE_CHECK

    SCORE_CHECK -->|≥80%| HIGH
    SCORE_CHECK -->|50-79%| MEDIUM
    SCORE_CHECK -->|<50%| LOW
```

**Example Consensus Calculation**:

Given 3 of 5 agents with these findings (Gemini, Cursor, Claude shown for brevity):

- **Gemini**: [A, B, C, D]
- **Cursor**: [A, B, E]
- **Claude**: [A, C, F]

**Analysis**:

- Total unique findings: A, B, C, D, E, F = **6**
- Agreements (2+ agents):
  - A: all 3 agents shown ✓
  - B: Gemini + Cursor ✓
  - C: Gemini + Claude ✓
- Agreement count: **3**
- **Consensus Score**: 3/6 * 100 = **50%** (MEDIUM)

**Action**: Show disagreements (D, E, F are unique to single agents), recommend user review.

---

---

[← Architecture Diagrams](README.md)
