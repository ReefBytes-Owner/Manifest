# Command Configuration

> Thresholds, tool policies, agent commands, and CLI flags.

**Last Updated**: 2026-08-20

## Command Configuration

**File**: `~/.claude/config/command_config.yml`

Defines behavior for each slash command.

### Thresholds

```yaml
thresholds:
  # Documentation commands
  docs_improve_lines: 500         # Trigger parallel agents when total doc lines > 500
  docs_diagrams_modules: 5        # Trigger when analyzing 5+ unique imports/modules

  # Advisory code-quality measurements (never auto-trigger a skill)
  skill_file_lines: 500           # File > 500 lines
  skill_function_count: 10        # > 10 functions per file
  skill_class_count: 5            # > 5 classes per file
  skill_cyclomatic_complexity: 15 # Cyclomatic complexity > 15
```

These thresholds are advisory review signals only. The risk-based escalation
contract uses
semantic conditions such as trust-boundary changes, destructive behavior, broad
compatibility or deployment impact, conflicting evidence, or genuinely
independent codebase-wide tracks.

### Consensus Thresholds

```yaml
# Consensus thresholds for parallel agent decisions (float 0.0-1.0)
consensus:
  high: 0.80    # >=0.80: Auto-proceed with unified recommendation
  medium: 0.50  # 0.50-0.79: Highlight disagreements to user
  low: 0.0      # <0.50: Block and escalate for human review
```

**Example**: If 2 of 3 agents agree → 67% consensus → medium confidence → disagreements highlighted

### Tool Policies

Defines which tools each command can use:

```yaml
tool_policies:
  python-refactor:
    allowed:
      - Read
      - Glob
      - Grep
      - Bash  # Check-only verification
    forbidden:
      - Write
      - Edit  # Read-only analysis
    bash_mode: check-only
    parallel_agents: conditional
    trigger_condition: trust_boundary_change OR destructive_behavior OR broad_compatibility_or_deployment_change OR conflicting_evidence_or_unresolved_uncertainty OR codebase_wide_independent_tracks
    validation_tier: 1
    subagents: conditional
    subagent_trigger: trust_boundary_change OR destructive_behavior OR broad_compatibility_or_deployment_change OR conflicting_evidence_or_unresolved_uncertainty OR codebase_wide_independent_tracks
    subagent_model: sonnet

  docs-generate-diagrams:
    allowed:
      - Read
      - Glob
      - Grep
    forbidden:
      - Bash
    parallel_agents: conditional
    trigger_condition: unique_imports >= 5
    validation_tier: 2
```

**Parallel agent modes:**

- `always`: Always run parallel agents (reserved for explicit high-assurance workflows)
- `never`: Never run parallel agents (single-agent mode)
- `conditional`: Run only when its stated risk condition is present

### Model Selection Defaults

```yaml
task_model_defaults:
  security:
    cursor: advanced
    claude: opus
    gemini: pro
    reason: "Security-critical code requires maximum model capability"

  review:
    cursor: flash
    claude: sonnet
    gemini: flash
    reason: "Code review benefits from balanced capability/speed"

  analyze:
    cursor: flash
    claude: sonnet
    gemini: flash
    reason: "Analysis tasks need good reasoning without opus cost"

  quick:
    cursor: mini
    claude: haiku
    gemini: flash
    reason: "Quick queries use lightest models for speed"
```

### Credit Exhaustion Fallback

```yaml
credit_fallback:
  cursor:
    chain:
      - advanced       # Try gpt-5.2 first
      - flash          # Fall back to gpt-5.1-codex
      - mini           # Fall back to gpt-5.1-codex-mini
      - auto           # Final fallback: let Cursor decide
    final_fallback: auto

  claude:
    chain:
      - opus           # Try opus first
      - sonnet         # Fall back to sonnet
      - haiku          # Final fallback
    final_fallback: haiku
```

**How it works:**

1. Agent runs with selected model (e.g., `opus`)
2. If quota exceeded, script detects error in stderr
3. Script retries with next model in chain (`sonnet`)
4. Process repeats until success or final fallback exhausted

---

## CLI Agent Command Configuration

**File**: `configs/claude/config/parallel_agent.yml` — `cli_agents:` block

Defines how `parallel_agent.py` invokes each CLI provider. Adding a CLI provider
is configuration-only — define its command shape here plus `model_tiers`,
`rate_limits`, and `credit_fallback` entries in the same file.

```yaml
cli_agents:
  # claude/gemini entries back the OAuth CLI fallback: used when the provider
  # SDK or its API key is unavailable but the CLI is installed and logged in.
  claude:
    binary: claude
    base_args: []
    model_args: ["--model", "{model}"]
    prompt_args: ["-p", "{prompt}"]
    output: stdout
  gemini:
    binary: gemini
    base_args: []
    model_args: ["-m", "{model}"]
    prompt_args: ["-p", "{prompt}"]
    output: stdout
  cursor:
    binary: cursor-agent
    base_args: ["--print", "--trust", "--output-format", "text", "--mode", "ask"]
    model_args: ["--model", "{model}"]
    prompt_args: ["{prompt}"]
    output: stdout
  codex:
    binary: codex
    base_args: ["exec", "--sandbox", "workspace-write", "--color", "never",
                "--output-last-message", "{output_file}"]
    model_args: ["--model", "{model}"]
    output: file_then_stdout
  antigravity:
    binary: agy
    base_args: []
    model_args: ["--model", "{model}"]
    prompt_args: ["--print", "{prompt}"]
    output: stdout
```

`output: file_then_stdout` reads the tempfile first, falling back to stdout;
`output: stdout` streams directly. `{model}`, `{prompt}`, and `{output_file}` are
substituted at runtime.

### Synthesis configuration

**File**: `configs/claude/config/parallel_agent.yml` — `synthesis:` block

When consensus falls below `threshold` (default 0.50), `SynthesisEngine` merges
agent outputs using the `synthesis.md` prompt template.

```yaml
synthesis:
  enabled: true
  threshold: 0.50
  model: "sonnet"       # model_tiers.claude tier
  timeout: 300
  backend: auto         # auto | cli | sdk
```

| `backend` | Behavior |
|-----------|----------|
| `auto` (default) | Same as primary claude agent: SDK when package + `ANTHROPIC_API_KEY`, else `claude -p` CLI |
| `cli` | Always invoke `claude -p` (OAuth/subscription login) |
| `sdk` | Always use Anthropic SDK (requires `ANTHROPIC_API_KEY`; for headless/CI) |

### Execution Backend (SDK vs CLI Fallback)

Claude and Gemini pick an execution backend per run (`agents/config.py`
`select_backend()`):

1. **SDK** — when the provider package (`anthropic` / `google-genai`) AND its API
   key (`ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`) are both present.
2. **CLI fallback** — otherwise, when the provider CLI (`claude` / `gemini`) is on
   PATH. OAuth/subscription logins work here with no API key — this is the default
   path on machines authenticated via `claude` / Gemini OAuth login.
3. **SDK with its own auth** (ADC/OAuth) as a last resort, else the provider is
   skipped with a warning.

The CLI fallback uses the `cli_agents.claude` / `cli_agents.gemini` command shapes
above. Cursor, Codex, Antigravity, and Devin always run via their CLI entries.

---

## Command-Line Options

### Agent Selection

```bash
--cursor-only          # Run only Cursor Agent
--gemini-only          # Run only Gemini CLI
--claude-only          # Run only Claude CLI
--codex-only           # Run only Codex CLI
--antigravity-only     # Run only Antigravity (agy)
--devin-only           # Run only Devin (opt-in; see below)
--no-claude            # Disable Claude CLI
--no-cursor            # Disable Cursor Agent
--no-gemini            # Disable Gemini CLI
--no-codex             # Disable Codex CLI
--no-antigravity       # Disable Antigravity for this run
--no-devin             # Disable Devin for this run
```

Devin ships **disabled** (`agent_roster.yml: devin.enabled_default: false`). Enable
it with `./bootstrap.sh --enable-devin` after `devin auth login` — an
unauthenticated agent errors instead of abstaining, which drags the consensus
metric into a verdict that is not a finding.

### Model Selection

```bash
--cursor-model <tier>       # Cursor model: mini, flash, advanced, auto (default: flash)
--claude-model <tier>       # Claude model: haiku, sonnet, opus (default: sonnet)
--gemini-model <tier>       # Gemini model: flash, pro (default: flash)
--codex-model <tier>        # Codex model: mini, flash, advanced, auto (default: auto)
--antigravity-model <tier>  # Antigravity model: mini, flash, advanced (default: flash)
--devin-model <name>        # Devin model: passed through verbatim (default: auto = no pin)
```

### Execution Modes

```bash
--analyze <file>       # Analyze a specific file for bugs/security
--review <file>        # Code review a file
--improve <file>       # Improve an observation YAML
```

### Output Options

```bash
--json                 # Output results in JSON format
--full-output          # Include complete agent outputs (no truncation)
--validate             # Check outputs against success criteria
--output <dir>         # Custom output directory (default: ~/.claude/.agent_outputs)
```

### Runtime Options

```bash
--timeout <seconds>    # Timeout per agent (default: 600)
--check-credits        # Run pre-flight credit check before execution
```

---

---

[← Configuration](README.md)
