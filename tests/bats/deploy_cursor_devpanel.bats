#!/usr/bin/env bats
# Tests for the Cursor side of the devpanel role-agents toggle. Mirrors
# deploy_cursor_pilotfish.bats (which covers the sibling pilotfish toggle) by
# exercising deploy_cursor_configs() itself, but against the SHARED
# configs/cursor/agents/ output dir the way generate_cursor_agents.py
# actually populates it in the real repo: nine pilotfish + six devpanel
# files, disjoint names, one directory. deploy_devpanel.bats already covers
# gate_devpanel_agents/check_devpanel_collision generically against a
# Claude-style home; this file is the missing devpanel counterpart to
# deploy_cursor_pilotfish.bats — same toggle, same manifest-owned prune
# semantics, applied to $CURSOR_TARGET_DIR/agents via the real deploy path.

load '../test_helper/bats-support/load'
load '../test_helper/bats-assert/load'

REPO_ROOT="$BATS_TEST_DIRNAME/../.."

setup() {
    export BATS_TMPDIR="${BATS_TMPDIR:-/tmp}"
    SANDBOX=$(mktemp -d "$BATS_TMPDIR/deploy_cursor_devpanel.XXXXXX")

    # shellcheck disable=SC1090
    source "$REPO_ROOT/bootstrap/lib/common.sh"
    # shellcheck disable=SC1090
    source "$REPO_ROOT/bootstrap/lib/deploy.sh"

    export SCRIPT_DIR="$SANDBOX/repo"
    CURSOR_AGENTS_SRC="$SCRIPT_DIR/configs/cursor/agents"
    mkdir -p "$SCRIPT_DIR/configs/cursor/rules" "$CURSOR_AGENTS_SRC"
    # Minimal but real hooks.json/mcp.json so deploy_cursor_configs' earlier
    # steps are no-ops rather than errors.
    printf '{"version":1,"hooks":{}}' > "$SCRIPT_DIR/configs/cursor/hooks.json"
    printf '{"mcpServers":{}}' > "$SCRIPT_DIR/configs/cursor/mcp.json"
    # merge_mcp_defaults() resolves its Python helper as
    # $SCRIPT_DIR/configs/claude/scripts/merge_mcp_defaults.py — mirror the
    # real one into this fake SCRIPT_DIR so the mcp.json merge step actually
    # runs instead of silently no-opping (deploy_cursor_configs' earlier
    # steps must be *real*, not stubbed away, for these assertions to mean
    # anything).
    mkdir -p "$SCRIPT_DIR/configs/claude/scripts"
    cp "$REPO_ROOT/configs/claude/scripts/merge_mcp_defaults.py" \
        "$SCRIPT_DIR/configs/claude/scripts/merge_mcp_defaults.py"

    # Seed the ONE shared output dir with BOTH role sets together — the real
    # shape generate_cursor_agents.py produces (11 files, disjoint names, one
    # directory) — rather than an isolated single-role-set fixture.
    for a in "${PILOTFISH_AGENT_FILES[@]}"; do
        name="${a%.md}"
        cat > "$CURSOR_AGENTS_SRC/$a" << EOF
---
name: $name
description: Cursor-native fixture for $name (pilotfish).
model: inherit
readonly: false
---

Fixture body for $name.
EOF
    done
    for a in "${DEVPANEL_AGENT_FILES[@]}"; do
        name="${a%.md}"
        cat > "$CURSOR_AGENTS_SRC/$a" << EOF
---
name: $name
description: Cursor-native fixture for $name (devpanel).
model: inherit
readonly: false
---

Fixture body for $name.
EOF
    done

    export TARGET_DIR="$SANDBOX/home/.claude" # unused directly; link_shared_assets no-ops on missing targets
    export CURSOR_TARGET_DIR="$SANDBOX/home/.cursor"
    export ENABLE_CURSOR=true
    export ENABLE_PILOTFISH=false
    export ENABLE_DEVPANEL=false
}

teardown() {
    [[ -n "${SANDBOX:-}" && -d "$SANDBOX" ]] && rm -rf "$SANDBOX"
}

# ── Enabled: deploys the five Cursor devpanel files, from the mixed shared source dir ──

@test "ENABLE_DEVPANEL=true: deploy_cursor_configs deploys the five Cursor devpanel files + marker" {
    export ENABLE_DEVPANEL=true
    run deploy_cursor_configs
    assert_success
    for a in developer.md debugger.md tester.md spec-guard.md chaos-engineer.md performance-auditor.md; do
        [ -f "$CURSOR_TARGET_DIR/agents/$a" ]
    done
    [ -f "$CURSOR_TARGET_DIR/agents/.devpanel" ]
    # Pilotfish stays off: none of its nine files should have landed even
    # though they sit right next to devpanel's in the shared source dir.
    for a in scout.md Explore.md mech-executor.md executor.md verifier.md security-executor.md context-chronicler.md compatibility-translator.md dependency-guardian.md; do
        [ ! -f "$CURSOR_TARGET_DIR/agents/$a" ]
    done
    run bash -c "ls '$CURSOR_TARGET_DIR'/agents/*.md | wc -l | tr -d ' '"
    assert_output "6"
}

@test "ENABLE_DEVPANEL=true: deployed agent carries Cursor-native frontmatter (model: inherit)" {
    export ENABLE_DEVPANEL=true
    run deploy_cursor_configs
    assert_success
    run grep -qxF "model: inherit" "$CURSOR_TARGET_DIR/agents/developer.md"
    assert_success
}

# ── Both toggles enabled: coexistence in the one shared dir ─────────────────

@test "ENABLE_PILOTFISH=true + ENABLE_DEVPANEL=true: both role sets land side by side in the same Cursor agents dir" {
    export ENABLE_PILOTFISH=true
    export ENABLE_DEVPANEL=true
    run deploy_cursor_configs
    assert_success
    for a in scout.md Explore.md mech-executor.md executor.md verifier.md security-executor.md context-chronicler.md compatibility-translator.md dependency-guardian.md \
        developer.md debugger.md tester.md spec-guard.md chaos-engineer.md performance-auditor.md; do
        [ -f "$CURSOR_TARGET_DIR/agents/$a" ]
    done
    [ -f "$CURSOR_TARGET_DIR/agents/.pilotfish" ]
    [ -f "$CURSOR_TARGET_DIR/agents/.devpanel" ]
    run bash -c "ls '$CURSOR_TARGET_DIR'/agents/*.md | wc -l | tr -d ' '"
    assert_output "15"
}

@test "disabling ENABLE_DEVPANEL after both were enabled prunes only the five devpanel files, pilotfish survives" {
    export ENABLE_PILOTFISH=true
    export ENABLE_DEVPANEL=true
    deploy_cursor_configs
    [ -f "$CURSOR_TARGET_DIR/agents/developer.md" ]
    [ -f "$CURSOR_TARGET_DIR/agents/scout.md" ]

    export ENABLE_DEVPANEL=false
    run deploy_cursor_configs
    assert_success
    for a in developer.md debugger.md tester.md spec-guard.md chaos-engineer.md performance-auditor.md; do
        [ ! -f "$CURSOR_TARGET_DIR/agents/$a" ]
    done
    [ ! -f "$CURSOR_TARGET_DIR/agents/.devpanel" ]
    for a in scout.md Explore.md mech-executor.md executor.md verifier.md security-executor.md context-chronicler.md compatibility-translator.md dependency-guardian.md; do
        [ -f "$CURSOR_TARGET_DIR/agents/$a" ]
    done
    [ -f "$CURSOR_TARGET_DIR/agents/.pilotfish" ]
    [ -d "$CURSOR_TARGET_DIR/agents" ]
}

@test "disabling ENABLE_PILOTFISH after both were enabled prunes only the six pilotfish files, devpanel survives" {
    export ENABLE_PILOTFISH=true
    export ENABLE_DEVPANEL=true
    deploy_cursor_configs

    export ENABLE_PILOTFISH=false
    run deploy_cursor_configs
    assert_success
    for a in scout.md Explore.md mech-executor.md executor.md verifier.md security-executor.md context-chronicler.md compatibility-translator.md dependency-guardian.md; do
        [ ! -f "$CURSOR_TARGET_DIR/agents/$a" ]
    done
    [ ! -f "$CURSOR_TARGET_DIR/agents/.pilotfish" ]
    for a in developer.md debugger.md tester.md spec-guard.md chaos-engineer.md performance-auditor.md; do
        [ -f "$CURSOR_TARGET_DIR/agents/$a" ]
    done
    [ -f "$CURSOR_TARGET_DIR/agents/.devpanel" ]
    [ -d "$CURSOR_TARGET_DIR/agents" ]
}

# ── Default / disabled: no cursor devpanel artifacts ────────────────────────

@test "ENABLE_DEVPANEL=false (default): deploy_cursor_configs deploys rules/mcp/hooks but no devpanel agent files" {
    run deploy_cursor_configs
    assert_success
    [ -d "$CURSOR_TARGET_DIR/rules" ]
    [ -f "$CURSOR_TARGET_DIR/mcp.json" ]
    [ -f "$CURSOR_TARGET_DIR/hooks.json" ]
    [ ! -f "$CURSOR_TARGET_DIR/agents/.devpanel" ]
}

@test "re-disabling after enabling preserves a coexisting user-authored Cursor agent" {
    export ENABLE_DEVPANEL=true
    deploy_cursor_configs
    echo "my custom cursor agent" > "$CURSOR_TARGET_DIR/agents/my-agent.md"

    export ENABLE_DEVPANEL=false
    run deploy_cursor_configs
    assert_success
    [ -f "$CURSOR_TARGET_DIR/agents/my-agent.md" ]
    assert_equal "$(cat "$CURSOR_TARGET_DIR/agents/my-agent.md")" "my custom cursor agent"
    [ ! -f "$CURSOR_TARGET_DIR/agents/developer.md" ]
    [ ! -f "$CURSOR_TARGET_DIR/agents/.devpanel" ]
}

# ── Collision guard ──────────────────────────────────────────────────────────

@test "collision: a foreign non-marker ~/.cursor/agents/tester.md blocks ONLY the devpanel agents step" {
    mkdir -p "$CURSOR_TARGET_DIR/agents"
    echo "user's own tester" > "$CURSOR_TARGET_DIR/agents/tester.md" # no .devpanel marker

    export ENABLE_DEVPANEL=true
    run deploy_cursor_configs
    assert_success # non-fatal: rest of the cursor deploy still completes
    assert_output --partial "devpanel: skipped Cursor role-agents deploy due to collision"
    # The foreign file is untouched — the real safety property under test.
    assert_equal "$(cat "$CURSOR_TARGET_DIR/agents/tester.md")" "user's own tester"
    # Rules/mcp/hooks still deployed despite the agents-step skip.
    [ -d "$CURSOR_TARGET_DIR/rules" ]
    [ -f "$CURSOR_TARGET_DIR/mcp.json" ]
    [ -f "$CURSOR_TARGET_DIR/hooks.json" ]
}

@test "no collision: a differently-named user agent does not block enabling devpanel" {
    mkdir -p "$CURSOR_TARGET_DIR/agents"
    echo "user agent" > "$CURSOR_TARGET_DIR/agents/my-agent.md" # not one of the five, no marker

    export ENABLE_DEVPANEL=true
    run deploy_cursor_configs
    assert_success
    refute_output --partial "skipped Cursor role-agents deploy due to collision"
    [ -f "$CURSOR_TARGET_DIR/agents/developer.md" ]
    [ -f "$CURSOR_TARGET_DIR/agents/my-agent.md" ]
}

@test "a pilotfish-only collision does not block the devpanel deploy step (independent guards)" {
    mkdir -p "$CURSOR_TARGET_DIR/agents"
    echo "user's own scout" > "$CURSOR_TARGET_DIR/agents/scout.md" # collides with pilotfish only

    export ENABLE_PILOTFISH=true
    export ENABLE_DEVPANEL=true
    run deploy_cursor_configs
    assert_success
    assert_output --partial "pilotfish: skipped Cursor role-agents deploy due to collision"
    refute_output --partial "devpanel: skipped Cursor role-agents deploy due to collision"
    [ -f "$CURSOR_TARGET_DIR/agents/developer.md" ]
    [ -f "$CURSOR_TARGET_DIR/agents/.devpanel" ]
    assert_equal "$(cat "$CURSOR_TARGET_DIR/agents/scout.md")" "user's own scout"
}

# ── ENABLE_CURSOR gate takes priority ────────────────────────────────────────

@test "ENABLE_CURSOR=false: no Cursor devpanel deploy even with ENABLE_DEVPANEL=true" {
    export ENABLE_CURSOR=false
    export ENABLE_DEVPANEL=true
    run deploy_cursor_configs
    assert_success
    [ ! -d "$CURSOR_TARGET_DIR" ] || [ ! -d "$CURSOR_TARGET_DIR/agents" ]
}

# ── Real repo: the actual generated configs/cursor/agents/ (11 mixed files) ─

@test "real repo: deploy_cursor_configs against the actual generated configs/cursor/agents/ deploys both role sets correctly" {
    REAL_SANDBOX="$SANDBOX/real"
    mkdir -p "$REAL_SANDBOX/repo/configs/cursor/rules"
    cp -R "$REPO_ROOT/configs/cursor/agents" "$REAL_SANDBOX/repo/configs/cursor/agents"
    printf '{"version":1,"hooks":{}}' > "$REAL_SANDBOX/repo/configs/cursor/hooks.json"
    printf '{"mcpServers":{}}' > "$REAL_SANDBOX/repo/configs/cursor/mcp.json"

    export SCRIPT_DIR="$REAL_SANDBOX/repo"
    export CURSOR_TARGET_DIR="$REAL_SANDBOX/home/.cursor"
    export TARGET_DIR="$REAL_SANDBOX/home/.claude"
    export ENABLE_CURSOR=true
    export ENABLE_PILOTFISH=true
    export ENABLE_DEVPANEL=true

    run deploy_cursor_configs
    assert_success
    for name in scout Explore mech-executor executor verifier security-executor context-chronicler compatibility-translator dependency-guardian \
        developer debugger tester spec-guard chaos-engineer performance-auditor; do
        [ -f "$CURSOR_TARGET_DIR/agents/$name.md" ]
    done
    run bash -c "ls '$CURSOR_TARGET_DIR'/agents/*.md | wc -l | tr -d ' '"
    assert_output "15"
    [ -f "$CURSOR_TARGET_DIR/agents/.pilotfish" ]
    [ -f "$CURSOR_TARGET_DIR/agents/.devpanel" ]
}
