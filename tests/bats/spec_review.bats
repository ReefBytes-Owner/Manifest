#!/usr/bin/env bats
# Tests for configs/claude/scripts/spec_review.sh

load '../test_helper/bats-support/load'
load '../test_helper/bats-assert/load'

REPO_ROOT="$BATS_TEST_DIRNAME/../.."
SCRIPT="$REPO_ROOT/configs/claude/scripts/spec_review.sh"

setup() {
    export BATS_TMPDIR="${BATS_TMPDIR:-/tmp}"
    SANDBOX=$(mktemp -d "$BATS_TMPDIR/spec_review.XXXXXX")
    # Default the panel to a guaranteed-failing command so seam/discovery/format
    # tests fall back to the single-CLI reviewer (the real parallel_agent.py lives
    # next to the script and must never be invoked in tests). Panel tests override.
    export SPEC_REVIEW_PANEL_CMD=/bin/false
}

teardown() {
    [[ -n "$SANDBOX" && -d "$SANDBOX" ]] && rm -rf "$SANDBOX"
}

@test "spec_review.sh is executable and prints usage on --help" {
    run bash "$SCRIPT" --help
    assert_success
    assert_output --partial "spec-review"
    assert_output --partial "--silent"
}

@test "spec_review.sh rejects an unknown flag" {
    run bash "$SCRIPT" --bogus
    assert_failure
}

@test "discover_artifacts finds speckit spec/plan/tasks in a specs dir" {
    mkdir -p "$SANDBOX/specs/001-feature"
    : > "$SANDBOX/specs/001-feature/spec.md"
    : > "$SANDBOX/specs/001-feature/plan.md"
    : > "$SANDBOX/specs/001-feature/tasks.md"
    source "$SCRIPT"
    run discover_artifacts "$SANDBOX"
    assert_success
    assert_output --partial "spec	$SANDBOX/specs/001-feature/spec.md"
    assert_output --partial "plan	$SANDBOX/specs/001-feature/plan.md"
    assert_output --partial "tasks	$SANDBOX/specs/001-feature/tasks.md"
}

@test "discover_artifacts finds superpowers design+plan (tasks embedded in plan)" {
    mkdir -p "$SANDBOX/docs/superpowers/specs" "$SANDBOX/docs/superpowers/plans"
    : > "$SANDBOX/docs/superpowers/specs/2026-06-08-thing-design.md"
    : > "$SANDBOX/docs/superpowers/plans/2026-06-08-thing.md"
    source "$SCRIPT"
    run discover_artifacts "$SANDBOX"
    assert_success
    assert_output --partial "spec	$SANDBOX/docs/superpowers/specs/2026-06-08-thing-design.md"
    assert_output --partial "plan	$SANDBOX/docs/superpowers/plans/2026-06-08-thing.md"
    refute_output --partial "tasks	"
}

@test "discover_artifacts prints nothing when no artifacts exist" {
    source "$SCRIPT"
    run discover_artifacts "$SANDBOX"
    assert_output ""
}

@test "discover_artifacts pairs a superpowers design-doc FILE within its own tree" {
    # Mixed-layout repo: the co-existing speckit layout must NOT hijack the
    # explicit design doc's plan (feature 482 US3 / FR-001).
    mkdir -p "$SANDBOX/specs/001-feature" \
        "$SANDBOX/docs/superpowers/specs" "$SANDBOX/docs/superpowers/plans"
    : > "$SANDBOX/specs/001-feature/spec.md"
    : > "$SANDBOX/specs/001-feature/plan.md"
    : > "$SANDBOX/docs/superpowers/specs/2026-06-08-thing-design.md"
    : > "$SANDBOX/docs/superpowers/plans/2026-06-08-thing.md"
    source "$SCRIPT"
    run discover_artifacts "$SANDBOX/docs/superpowers/specs/2026-06-08-thing-design.md"
    assert_success
    assert_output --partial "spec	$SANDBOX/docs/superpowers/specs/2026-06-08-thing-design.md"
    assert_output --partial "plan	$SANDBOX/docs/superpowers/plans/2026-06-08-thing.md"
    refute_output --partial "001-feature"
}

@test "discover_artifacts pairs a speckit spec FILE with its siblings" {
    mkdir -p "$SANDBOX/specs/001-feature"
    : > "$SANDBOX/specs/001-feature/spec.md"
    : > "$SANDBOX/specs/001-feature/plan.md"
    source "$SCRIPT"
    run discover_artifacts "$SANDBOX/specs/001-feature/spec.md"
    assert_success
    assert_output --partial "spec	$SANDBOX/specs/001-feature/spec.md"
    assert_output --partial "plan	$SANDBOX/specs/001-feature/plan.md"
}

@test "assemble_prompt embeds template and role-labelled artifact contents" {
    local tpl="$SANDBOX/tpl.md"; printf 'HEAD
{{ARTIFACTS}}
TAIL
' > "$tpl"
    printf 'spec body here
' > "$SANDBOX/spec.md"
    printf 'plan body here
' > "$SANDBOX/plan.md"
    source "$SCRIPT"
    run assemble_prompt "$tpl" "spec	$SANDBOX/spec.md" "plan	$SANDBOX/plan.md"
    assert_success
    assert_output --partial "HEAD"
    assert_output --partial "=== SPEC: $SANDBOX/spec.md ==="
    assert_output --partial "spec body here"
    assert_output --partial "=== PLAN: $SANDBOX/plan.md ==="
    assert_output --partial "plan body here"
    assert_output --partial "TAIL"
    refute_output --partial "{{ARTIFACTS}}"
}

_fake_reviewer() {  # writes a stub reviewer CLI named 'agy' into SANDBOX
    cat > "$SANDBOX/agy" <<'STUB'
#!/usr/bin/env bash
cat >/dev/null   # consume stdin
printf '⚠️  CLARIFICATION REQUIRED: Migration\n   ├─ Location: plan vs tasks\n   ├─ The Gap: zero-downtime vs destructive\n   ├─ Recommended Direction: split into 3 tasks\n   └─ Reason Why: locking violates the constraint\n'
STUB
    chmod +x "$SANDBOX/agy"
}

_panel_json() {  # emit a parallel_agent.py-style JSON doc; args: "name|status|output"
    python3 - "$@" <<'PY'
import json, sys
agents = {}
for spec in sys.argv[1:]:
    name, status, output = spec.split("|", 2)
    agents[name] = {"status": status, "output": output}
print(json.dumps({"agents": agents}))
PY
}

_fake_synth() {  # stub synth CLI that echoes a merged finding, proving it ran
    cat > "$SANDBOX/synth" <<'STUB'
#!/usr/bin/env bash
cat >/dev/null   # consume the merge prompt on stdin
printf '⚠️  CLARIFICATION REQUIRED: Merged\n   └─ Reason Why: deduped\n'
STUB
    chmod +x "$SANDBOX/synth"
}

_fake_panel() {  # stub parallel_agent.py emitting canned JSON from $PANEL_FIXTURE
    cat > "$SANDBOX/panel" <<'STUB'
#!/usr/bin/env bash
# prompt arrives as the trailing positional arg; ignore it. Emit canned JSON.
cat "$PANEL_FIXTURE"
STUB
    chmod +x "$SANDBOX/panel"
}

@test "run_reviewer pipes prompt through the injectable seam" {
    _fake_reviewer
    source "$SCRIPT"
    SPEC_REVIEW_CLI="$SANDBOX/agy" run run_reviewer "any prompt"
    assert_success
    assert_output --partial "CLARIFICATION REQUIRED: Migration"
}

@test "format_findings tree passes structured findings through" {
    source "$SCRIPT"
    run format_findings "⚠️  CLARIFICATION REQUIRED: X" "tree"
    assert_output --partial "CLARIFICATION REQUIRED: X"
}

@test "format_findings reports clean when reviewer returns NO_ISSUES" {
    source "$SCRIPT"
    run format_findings "NO_ISSUES" "tree"
    assert_success
    assert_output --partial "No inconsistencies found"
}

@test "format_findings json emits a JSON array" {
    source "$SCRIPT"
    run format_findings "NO_ISSUES" "json"
    assert_output --partial "[]"
}

@test "format_findings json wraps real findings as valid JSON (incl. special chars)" {
    source "$SCRIPT"
    run format_findings 'CLARIFICATION: a & b \slash "quote"' "json"
    assert_success
    # the python3-wrapped branch must produce JSON that parses and round-trips
    echo "$output" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d,list) and "CLARIFICATION" in d[0]'
}

@test "content_hash is stable for same content and differs on change" {
    printf 'a\n' > "$SANDBOX/x.md"; printf 'b\n' > "$SANDBOX/y.md"
    source "$SCRIPT"
    h1="$(content_hash "$SANDBOX/x.md" "$SANDBOX/y.md")"
    h2="$(content_hash "$SANDBOX/x.md" "$SANDBOX/y.md")"
    [ "$h1" = "$h2" ]
    printf 'b2\n' > "$SANDBOX/y.md"
    h3="$(content_hash "$SANDBOX/x.md" "$SANDBOX/y.md")"
    [ "$h1" != "$h3" ]
}

@test "should_run_silent skips when fewer than 2 artifacts" {
    mkdir -p "$SANDBOX/specs/001"; : > "$SANDBOX/specs/001/spec.md"
    source "$SCRIPT"
    SPEC_REVIEW_STATE="$SANDBOX/.spec-review" run should_run_silent "$SANDBOX"
    assert_failure
    assert_output --partial "fewer than 2 artifacts"
}

@test "should_run_silent runs until a successful review records the hash (issue #317)" {
    mkdir -p "$SANDBOX/specs/001"
    printf 's\n' > "$SANDBOX/specs/001/spec.md"
    printf 'p\n' > "$SANDBOX/specs/001/plan.md"
    source "$SCRIPT"
    export SPEC_REVIEW_STATE="$SANDBOX/.spec-review"
    run should_run_silent "$SANDBOX"; assert_success          # first time: changed
    # The gate no longer records the hash — a failed review must be retried,
    # so an immediate second call still says run.
    run should_run_silent "$SANDBOX"; assert_success
    # Simulate a successful review recording the hash (what
    # _silent_review_inline does on success); now identical content skips.
    echo "$output" > "$SPEC_REVIEW_STATE/.last-run"
    run should_run_silent "$SANDBOX"; assert_failure
    assert_output --partial "unchanged"
    printf 'p2\n' > "$SANDBOX/specs/001/plan.md"
    run should_run_silent "$SANDBOX"; assert_success          # changed again
}

@test "on-demand review prints findings from the mocked reviewer" {
    _fake_reviewer
    mkdir -p "$SANDBOX/specs/001"
    printf 's\n' > "$SANDBOX/specs/001/spec.md"
    printf 'p\n' > "$SANDBOX/specs/001/plan.md"
    SPEC_REVIEW_CLI="$SANDBOX/agy" SPEC_REVIEW_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review.md" \
        run bash "$SCRIPT" "$SANDBOX"
    assert_success
    assert_output --partial "CLARIFICATION REQUIRED: Migration"
}

@test "on-demand review on no artifacts exits 0 with nothing-to-review" {
    run bash "$SCRIPT" "$SANDBOX"
    assert_success
    assert_output --partial "nothing to review"
}

@test "silent mode writes feedback file and exits 0 (inline via NO_DETACH)" {
    _fake_reviewer
    mkdir -p "$SANDBOX/specs/001"
    printf 's\n' > "$SANDBOX/specs/001/spec.md"
    printf 'p\n' > "$SANDBOX/specs/001/plan.md"
    SPEC_REVIEW_CLI="$SANDBOX/agy" SPEC_REVIEW_NO_DETACH=1 \
        SPEC_REVIEW_STATE="$SANDBOX/.spec-review" \
        SPEC_REVIEW_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review.md" \
        run bash "$SCRIPT" --silent "$SANDBOX"
    assert_success
    assert [ -f "$SANDBOX/.spec-review/feedback.md" ]
    run cat "$SANDBOX/.spec-review/feedback.md"
    assert_output --partial "CLARIFICATION REQUIRED: Migration"
}

@test "silent mode fails open: non-zero reviewer still exits 0" {
    printf '#!/usr/bin/env bash\nexit 3\n' > "$SANDBOX/agy"; chmod +x "$SANDBOX/agy"
    mkdir -p "$SANDBOX/specs/001"
    printf 's\n' > "$SANDBOX/specs/001/spec.md"
    printf 'p\n' > "$SANDBOX/specs/001/plan.md"
    SPEC_REVIEW_CLI="$SANDBOX/agy" SPEC_REVIEW_NO_DETACH=1 \
        SPEC_REVIEW_STATE="$SANDBOX/.spec-review" \
        SPEC_REVIEW_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review.md" \
        run bash "$SCRIPT" --silent "$SANDBOX"
    assert_success
}

@test "silent mode is a no-op on unchanged content (second run)" {
    _fake_reviewer
    mkdir -p "$SANDBOX/specs/001"
    printf 's\n' > "$SANDBOX/specs/001/spec.md"
    printf 'p\n' > "$SANDBOX/specs/001/plan.md"
    local env="SPEC_REVIEW_CLI=$SANDBOX/agy SPEC_REVIEW_NO_DETACH=1 SPEC_REVIEW_STATE=$SANDBOX/.spec-review SPEC_REVIEW_TEMPLATE=$REPO_ROOT/configs/claude/prompts/spec_review.md"
    env $env bash "$SCRIPT" --silent "$SANDBOX"
    rm -f "$SANDBOX/.spec-review/feedback.md"
    env $env bash "$SCRIPT" --silent "$SANDBOX"   # unchanged -> skip, no rewrite
    assert [ ! -f "$SANDBOX/.spec-review/feedback.md" ]
}

@test "silent mode self-heals a stale lock (older than 10 min)" {
    _fake_reviewer
    mkdir -p "$SANDBOX/specs/001"
    printf 's\n' > "$SANDBOX/specs/001/spec.md"
    printf 'p\n' > "$SANDBOX/specs/001/plan.md"
    mkdir -p "$SANDBOX/.spec-review/.lock"
    # age the stale lock 20 minutes into the past
    touch -t "$(date -v-20M +%Y%m%d%H%M 2>/dev/null || date -d '20 min ago' +%Y%m%d%H%M)" "$SANDBOX/.spec-review/.lock"
    SPEC_REVIEW_CLI="$SANDBOX/agy" SPEC_REVIEW_NO_DETACH=1 \
        SPEC_REVIEW_STATE="$SANDBOX/.spec-review" \
        SPEC_REVIEW_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review.md" \
        run bash "$SCRIPT" --silent "$SANDBOX"
    assert_success
    assert [ -f "$SANDBOX/.spec-review/feedback.md" ]   # ran despite the stale lock
    assert [ ! -d "$SANDBOX/.spec-review/.lock" ]        # lock released after run
}

@test "spec-review SKILL.md has valid frontmatter and points at the engine" {
    local skill="$REPO_ROOT/.apm/skills/spec-review/SKILL.md"
    assert [ -f "$skill" ]
    run head -1 "$skill"; assert_output "---"
    run grep -E '^name: spec-review' "$skill"; assert_success
    run grep -E 'spec_review\.sh' "$skill"; assert_success
}

@test ".gitignore ignores the .spec-review runtime dir" {
    run grep -E '^\.spec-review/?$' "$REPO_ROOT/.gitignore"
    assert_success
}

@test "settings.runtime.json registers the spec_review silent save hook" {
    # settings.local.json is inert at user scope; hooks live in the runtime
    # fragment that bootstrap merges into ~/.claude/settings.json.
    local s="$REPO_ROOT/configs/claude/settings.runtime.json"
    run python3 -c "import json; d=json.load(open('$s')); cmds=[h['command'] for m in d['hooks']['PostToolUse'] for h in m['hooks']]; assert any('spec_review.sh' in c and '--silent' in c for c in cmds), cmds"
    assert_success
}

@test "settings.local.json remains valid JSON" {
    run python3 -c "import json; json.load(open('$REPO_ROOT/configs/claude/settings.local.json'))"
    assert_success
}

@test "settings.runtime.json does not duplicate the plugin-owned version-pin hook" {
    local s="$REPO_ROOT/configs/claude/settings.runtime.json"
    run python3 -c "import json; d=json.load(open('$s')); cmds=[h['command'] for m in d['hooks']['PostToolUse'] for h in m['hooks']]; assert not any('version_pin' in c for c in cmds), cmds"
    assert_success
}

@test "explicit --spec/--plan flags are used instead of auto-discovery" {
    _fake_reviewer
    printf 's\n' > "$SANDBOX/myspec.md"
    printf 'p\n' > "$SANDBOX/myplan.md"
    # ROOT has no discoverable layout; only the explicit flags point at artifacts
    SPEC_REVIEW_CLI="$SANDBOX/agy" SPEC_REVIEW_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review.md" \
        run bash "$SCRIPT" --spec "$SANDBOX/myspec.md" --plan "$SANDBOX/myplan.md" "$SANDBOX"
    assert_success
    assert_output --partial "CLARIFICATION REQUIRED: Migration"
}

@test "clean message includes the artifact count" {
    # reviewer stub that reports no issues
    printf '#!/usr/bin/env bash\ncat >/dev/null\nprintf NO_ISSUES\n' > "$SANDBOX/agy"
    chmod +x "$SANDBOX/agy"
    mkdir -p "$SANDBOX/specs/001"
    printf 's\n' > "$SANDBOX/specs/001/spec.md"
    printf 'p\n' > "$SANDBOX/specs/001/plan.md"
    SPEC_REVIEW_CLI="$SANDBOX/agy" SPEC_REVIEW_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review.md" \
        run bash "$SCRIPT" "$SANDBOX"
    assert_success
    assert_output --partial "No inconsistencies found across 2 artifacts"
}

@test "default reviewer is agy when SPEC_REVIEW_CLI is unset" {
    # Put a stub named 'agy' on PATH; do NOT set SPEC_REVIEW_CLI. HOME is
    # pinned to an empty sandbox directory so this can never resolve a real
    # ~/.claude config or invoke a real agy CLI, even outside the runner
    # (Correction 14 / C7o rule 1).
    _fake_reviewer                      # creates $SANDBOX/agy
    mkdir -p "$SANDBOX/specs/001" "$SANDBOX/home"
    printf 's\n' > "$SANDBOX/specs/001/spec.md"
    printf 'p\n' > "$SANDBOX/specs/001/plan.md"
    PATH="$SANDBOX:$PATH" HOME="$SANDBOX/home" \
        SPEC_REVIEW_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review.md" \
        run bash "$SCRIPT" "$SANDBOX"
    assert_success
    assert_output --partial "CLARIFICATION REQUIRED: Migration"
}

@test "on-demand review is BLOCKED (never a clean verdict) when no reviewer CLI exists on PATH" {
    # No 'agy' anywhere: PATH is pinned to a minimal set with no $SANDBOX entry,
    # and SPEC_REVIEW_CLI is left unset (defaults to 'agy'). An absent reviewer
    # must be an explicit error, never "No inconsistencies found" (false green).
    mkdir -p "$SANDBOX/specs/001"
    printf 's\n' > "$SANDBOX/specs/001/spec.md"
    printf 'p\n' > "$SANDBOX/specs/001/plan.md"
    PATH="/usr/bin:/bin" HOME="$SANDBOX/no-home" \
        SPEC_REVIEW_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review.md" \
        run bash "$SCRIPT" "$SANDBOX"
    assert_failure
    refute_output --partial "No inconsistencies found"
    assert_output --partial "reviewer CLI not found"
}

# ---------------------------------------------------------------------------
# SPEC_REVIEW_MODEL seam
# ---------------------------------------------------------------------------

@test "resolve_review_model honors explicit SPEC_REVIEW_MODEL" {
    source "$SCRIPT"
    SPEC_REVIEW_MODEL="My Model X"
    run resolve_review_model
    assert_success
    assert_output "My Model X"
}

@test "resolve_review_model reads model_tiers.antigravity.advanced for agy" {
    cat > "$SANDBOX/pa.yml" <<'EOF'
model_tiers:
  antigravity:
    advanced: "Claude Opus 4.6 (Thinking)"
EOF
    source "$SCRIPT"
    SPEC_REVIEW_MODEL=""
    SPEC_REVIEW_CLI="agy"
    SPEC_REVIEW_CONFIG="$SANDBOX/pa.yml"
    run resolve_review_model
    assert_success
    assert_output "Claude Opus 4.6 (Thinking)"
}

@test "resolve_review_model is empty for non-agy CLI without explicit model" {
    cat > "$SANDBOX/pa.yml" <<'EOF'
model_tiers:
  antigravity:
    advanced: "Claude Opus 4.6 (Thinking)"
EOF
    source "$SCRIPT"
    SPEC_REVIEW_MODEL=""
    SPEC_REVIEW_CLI="gemini"
    SPEC_REVIEW_CONFIG="$SANDBOX/pa.yml"
    run resolve_review_model
    assert_success
    assert_output ""
}

@test "resolve_review_model fails open when config is missing" {
    source "$SCRIPT"
    SPEC_REVIEW_MODEL=""
    SPEC_REVIEW_CLI="agy"
    SPEC_REVIEW_CONFIG="$SANDBOX/does-not-exist.yml"
    run resolve_review_model
    assert_success
    assert_output ""
}

@test "run_reviewer passes --model when a model resolves" {
    mkdir -p "$SANDBOX/bin"
    cat > "$SANDBOX/bin/fakecli" <<'EOF'
#!/usr/bin/env bash
echo "ARGS:$*"
EOF
    chmod +x "$SANDBOX/bin/fakecli"
    source "$SCRIPT"
    SPEC_REVIEW_CLI="$SANDBOX/bin/fakecli"
    SPEC_REVIEW_MODEL="Tier-X"
    run run_reviewer "prompt body"
    assert_success
    assert_output --partial "--model Tier-X"
}

@test "run_reviewer omits --model when nothing resolves" {
    mkdir -p "$SANDBOX/bin"
    cat > "$SANDBOX/bin/fakecli" <<'EOF'
#!/usr/bin/env bash
echo "ARGS:$*"
EOF
    chmod +x "$SANDBOX/bin/fakecli"
    source "$SCRIPT"
    SPEC_REVIEW_CLI="$SANDBOX/bin/fakecli"
    SPEC_REVIEW_MODEL=""
    SPEC_REVIEW_CONFIG="$SANDBOX/does-not-exist.yml"
    run run_reviewer "prompt body"
    assert_success
    refute_output --partial "--model"
}

# ---------------------------------------------------------------------------
# Parallel-agent panel engine
# ---------------------------------------------------------------------------

@test "assemble_merge_prompt substitutes {{REVIEWS}} with the reviews block" {
    local tpl="$SANDBOX/merge.md"; printf 'MHEAD\n{{REVIEWS}}\nMTAIL\n' > "$tpl"
    source "$SCRIPT"
    run assemble_merge_prompt "$tpl" "=== REVIEWER: GEMINI ===
finding one"
    assert_success
    assert_output --partial "MHEAD"
    assert_output --partial "=== REVIEWER: GEMINI ==="
    assert_output --partial "finding one"
    assert_output --partial "MTAIL"
    refute_output --partial "{{REVIEWS}}"
}

@test "parse_panel_json reports count, all-no-issues flag, and writes blocks" {
    source "$SCRIPT"
    _panel_json "gemini|complete|⚠️  CLARIFICATION REQUIRED: A" \
                "cursor|complete|NO_ISSUES" \
                "codex|failed|exit 1 boom" > "$SANDBOX/fx.json"
    run parse_panel_json "$SANDBOX/fx.json" "$SANDBOX/blocks" "$SANDBOX/raw"
    assert_success
    assert_output "2	0"
    grep -q "=== REVIEWER: GEMINI ===" "$SANDBOX/blocks"
    grep -q "CLARIFICATION REQUIRED: A" "$SANDBOX/blocks"
}

@test "parse_panel_json flags all-no-issues when no CLARIFICATION present" {
    source "$SCRIPT"
    _panel_json "gemini|complete|NO_ISSUES" "cursor|complete|NO_ISSUES" > "$SANDBOX/fx.json"
    run parse_panel_json "$SANDBOX/fx.json" "$SANDBOX/b" "$SANDBOX/r"
    assert_success
    assert_output "2	1"
}

@test "parse_panel_json tolerates console preamble before the JSON" {
    source "$SCRIPT"
    { printf 'Warning: only 1 agent enabled\n'; _panel_json "gemini|complete|NO_ISSUES"; } > "$SANDBOX/fx.json"
    run parse_panel_json "$SANDBOX/fx.json" "$SANDBOX/b" "$SANDBOX/r"
    assert_success
    assert_output "1	1"
}

@test "run_synthesizer merges reviews through the synth seam + merge template" {
    _fake_synth
    source "$SCRIPT"
    SPEC_REVIEW_SYNTH_CLI="$SANDBOX/synth" \
    SPEC_REVIEW_MERGE_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review_merge.md" \
        run run_synthesizer <<< "=== REVIEWER: GEMINI ===
finding one"
    assert_success
    assert_output --partial "CLARIFICATION REQUIRED: Merged"
}

@test "run_panel: >=2 agents are merged by the synthesizer" {
    _fake_panel; _fake_synth
    _panel_json "gemini|complete|⚠️  CLARIFICATION REQUIRED: A" \
                "cursor|complete|⚠️  CLARIFICATION REQUIRED: B" > "$SANDBOX/fx.json"
    source "$SCRIPT"
    PANEL_FIXTURE="$SANDBOX/fx.json" \
    SPEC_REVIEW_PANEL_CMD="$SANDBOX/panel" \
    SPEC_REVIEW_SYNTH_CLI="$SANDBOX/synth" \
    SPEC_REVIEW_MERGE_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review_merge.md" \
        run run_panel "the assembled prompt"
    assert_success
    assert_output --partial "CLARIFICATION REQUIRED: Merged"
}

@test "run_panel: exactly 1 agent passes through without a synth call" {
    _fake_panel
    _panel_json "gemini|complete|⚠️  CLARIFICATION REQUIRED: Solo" > "$SANDBOX/fx.json"
    source "$SCRIPT"
    PANEL_FIXTURE="$SANDBOX/fx.json" \
    SPEC_REVIEW_PANEL_CMD="$SANDBOX/panel" \
    SPEC_REVIEW_SYNTH_CLI="/bin/false" \
        run run_panel "p"
    assert_success
    assert_output --partial "CLARIFICATION REQUIRED: Solo"
}

@test "run_panel: all NO_ISSUES short-circuits to NO_ISSUES (no synth call)" {
    _fake_panel
    _panel_json "gemini|complete|NO_ISSUES" "cursor|complete|NO_ISSUES" > "$SANDBOX/fx.json"
    source "$SCRIPT"
    PANEL_FIXTURE="$SANDBOX/fx.json" \
    SPEC_REVIEW_PANEL_CMD="$SANDBOX/panel" \
    SPEC_REVIEW_SYNTH_CLI="/bin/false" \
        run run_panel "p"
    assert_success
    assert_output --partial "NO_ISSUES"
}

@test "run_panel: panel failure falls back to the single-CLI reviewer" {
    _fake_reviewer
    source "$SCRIPT"
    SPEC_REVIEW_PANEL_CMD="/bin/false" \
    SPEC_REVIEW_CLI="$SANDBOX/agy" \
        run run_panel "p"
    assert_success
    assert_output --partial "CLARIFICATION REQUIRED: Migration"
}

@test "run_panel: synthesizer failure falls back to labeled concat" {
    _fake_panel
    _panel_json "gemini|complete|⚠️  CLARIFICATION REQUIRED: A" \
                "cursor|complete|⚠️  CLARIFICATION REQUIRED: B" > "$SANDBOX/fx.json"
    source "$SCRIPT"
    PANEL_FIXTURE="$SANDBOX/fx.json" \
    SPEC_REVIEW_PANEL_CMD="$SANDBOX/panel" \
    SPEC_REVIEW_SYNTH_CLI="/bin/false" \
    SPEC_REVIEW_MERGE_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review_merge.md" \
        run run_panel "p"
    assert_success
    assert_output --partial "=== REVIEWER: GEMINI ==="
    assert_output --partial "CLARIFICATION REQUIRED: A"
    assert_output --partial "CLARIFICATION REQUIRED: B"
}

@test "on-demand review routes through the parallel panel" {
    _fake_panel
    _panel_json "gemini|complete|⚠️  CLARIFICATION REQUIRED: PanelPath" > "$SANDBOX/fx.json"
    mkdir -p "$SANDBOX/specs/001"; : > "$SANDBOX/specs/001/spec.md"; : > "$SANDBOX/specs/001/plan.md"
    PANEL_FIXTURE="$SANDBOX/fx.json" \
    SPEC_REVIEW_PANEL_CMD="$SANDBOX/panel" \
    SPEC_REVIEW_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review.md" \
        run bash "$SCRIPT" "$SANDBOX"
    assert_success
    assert_output --partial "CLARIFICATION REQUIRED: PanelPath"
    assert_output --partial "parallel agent panel"
}

@test "silent mode routes through the panel and writes feedback (NO_DETACH)" {
    _fake_panel
    _panel_json "gemini|complete|⚠️  CLARIFICATION REQUIRED: HookPanel" > "$SANDBOX/fx.json"
    mkdir -p "$SANDBOX/specs/001"; printf 'a\n' > "$SANDBOX/specs/001/spec.md"; printf 'b\n' > "$SANDBOX/specs/001/plan.md"
    PANEL_FIXTURE="$SANDBOX/fx.json" \
    SPEC_REVIEW_PANEL_CMD="$SANDBOX/panel" \
    SPEC_REVIEW_NO_DETACH=1 SPEC_REVIEW_STATE="$SANDBOX/.spec-review" \
    SPEC_REVIEW_TEMPLATE="$REPO_ROOT/configs/claude/prompts/spec_review.md" \
        run bash "$SCRIPT" --silent "$SANDBOX"
    assert_success
    assert [ -f "$SANDBOX/.spec-review/feedback.md" ]
    grep -q "CLARIFICATION REQUIRED: HookPanel" "$SANDBOX/.spec-review/feedback.md"
}
