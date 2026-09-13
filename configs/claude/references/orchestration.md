# Orchestration Reference

> Multi-agent review workflow, cross-verification patterns, synthesis, and
> validation phases. Referenced from CLAUDE.md.

## Cross-Verification Patterns

### Pattern 1: Agreement Scoring

After receiving outputs from both agents, assess consensus:

```text
Consensus Score = (Agreements / Total_Findings) * 100

≥80%: High confidence - proceed with unified recommendation
50-79%: Medium confidence - highlight disagreements to user
<50%: Low confidence - escalate for human review
```

### Pattern 2: Synthesis

When agents disagree, synthesize by:

1. Identifying the core disagreement
2. Evaluating each agent's reasoning
3. Providing a unified recommendation with caveats
4. Noting which agent's approach was preferred and why

### Pattern 3: Specialization

Use agents for their strengths:

- **Gemini**: Broad knowledge, creative solutions, research
- **Cursor**: IDE-integrated context, code-specific analysis
- **Claude**: Deep reasoning, security analysis, complex logic

## Workflow Integration

### Before Making Changes

```bash
# Get multi-agent review of proposed changes
~/.claude/scripts/parallel_agent.py --json --validate \
  "Review this planned change: [description]. Files affected: [list]"
```

### After Making Changes

```bash
# Validate the implementation (use absolute path, 10 min timeout)
~/.claude/scripts/parallel_agent.py --json --validate --timeout 600 --review /absolute/path/to/modified_file
```

### For Complex Decisions

```bash
# Get diverse perspectives
~/.claude/scripts/parallel_agent.py --json --full-output \
  "Evaluate these approaches for [problem]: Option A: ... Option B: ..."
```

## Error Handling

The script implements:

- **Agent validation**: Checks if `cursor`, `gemini`, and `claude` commands exist
- **Retry logic**: Retries once after 5s delay on failure
- **Partial results**: Continues with available agent outputs if some fail
- **Credit fallback**: Automatically retries with cheaper models on quota errors
- **Exit codes**: 0=success, 1=no args, 2=no agents available

## Orchestrated Code Review Workflow

Use one capable reviewer by default. Before adding independent review, assess
only the shared five-condition risk gate:

- trust-boundary change;
- destructive behavior;
- broad compatibility or deployment impact;
- conflicting evidence or unresolved uncertainty; or
- a codebase-wide investigation with genuinely independent analysis tracks.

Counts of files, lines, packages, modules, languages, keywords, and units do
not independently trigger review. If a condition is present, use the
pre-flight prompt to record the concrete evidence and select either Task
sub-agents for independent tracks or cross-model verification.

### Cross-model review

When the risk gate opens and cross-model verification is appropriate, execute:

```bash
~/.claude/scripts/parallel_agent.py --json --full-output --validate --timeout 600 --review /absolute/path/to/file
```

Parse JSON only after the command succeeds. If the check cannot be run safely
or its required capability is unavailable, report it as unavailable rather
than treating it as a passing review.

### Phase 3: Synthesis (on disagreement)

When agents disagree (consensus < 80%), spawn a synthesis agent:

```text
Task(
  subagent_type: "general-purpose",
  prompt: "Using the template at ~/.claude/prompts/synthesis.md, synthesize these outputs:
           Original task: [TASK]
           Gemini output: [GEMINI_OUTPUT]
           Cursor output: [CURSOR_OUTPUT]
           Claude output: [CLAUDE_OUTPUT]
           Return JSON with consensus_score, disagreements, unified_recommendation"
)
```

**Consensus Thresholds**:

- ≥80%: High confidence - proceed with unified recommendation
- 50-79%: Medium confidence - highlight disagreements to user
- <50%: Low confidence - escalate for human review

### Phase 4: Validation

Always run validation before finalizing changes:

```text
Task(
  subagent_type: "general-purpose",
  prompt: "Using the criteria in ~/.claude/prompts/validation.md and ~/.claude/config/validation_criteria.yml,
           validate this code: [CODE_OR_DIFF]
           Return JSON with tier1 results, tier2 results, overall_verdict"
)
```

**Verdicts**:

- `APPROVED`: All Tier 1 checks pass, Tier 2 score ≥ 0.60
- `NEEDS_REVIEW`: All Tier 1 checks pass, Tier 2 score < 0.60
- `BLOCKED`: Any Tier 1 check fails

### Configuration Files

| File | Purpose |
|------|---------|
| `~/.claude/prompts/preflight_analysis.md` | Pre-flight analysis prompt template |
| `~/.claude/prompts/synthesis.md` | Disagreement synthesis prompt template |
| `~/.claude/prompts/validation.md` | Validation criteria prompt template |
| `~/.claude/config/validation_criteria.yml` | Detailed validation rules and thresholds |

### Example Orchestration Flow

```text
User: "Add authentication middleware to the API routes"

Claude (Orchestrator):
  1. Spawns Task(Explore) for pre-flight analysis
     → Returns: {needs_parallel_review: true, reason: "Authentication logic", confidence: 0.95}

  2. Executes: ~/.claude/scripts/parallel_agent.py --json --validate --timeout 600 \
       --cursor-model advanced --claude-model opus --review "$(pwd)/src/middleware/auth.js"
     → Gemini: "Use JWT with refresh tokens, add rate limiting"
     → Cursor: "Use JWT with session fallback, add CSRF protection"
     → Claude: "Use JWT with refresh tokens, add rate limiting and input validation"
     → Consensus: 75% (MEDIUM)

  3. Spawns Task(general-purpose) for synthesis
     → Returns: {consensus_score: 0.75, unified_recommendation: "Use JWT with refresh tokens, add rate limiting, CSRF, and input validation"}

  4. Spawns Task(general-purpose) for validation
     → Returns: {tier1: {passed: true}, tier2: {score: 0.85}, verdict: "APPROVED"}

  5. Reports to user with synthesized recommendation and validation results
```
