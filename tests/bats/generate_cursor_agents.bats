#!/usr/bin/env bats
# Tests for configs/claude/scripts/generate_cursor_agents.py (spec
# 2026-07-11-cursor-feature-parity WS-5) and its wiring into
# generate_cursor_rules.sh.
#
# generate_cursor_agents.py takes --src/--output explicitly (no implicit
# REPO_ROOT-derived defaults are exercised here), so the hermetic seam is a
# sandbox source-agents dir the tests fully control.

load '../test_helper/bats-support/load'
load '../test_helper/bats-assert/load'

REPO_ROOT="$BATS_TEST_DIRNAME/../.."
GEN="$REPO_ROOT/configs/claude/scripts/generate_cursor_agents.py"

setup() {
    command -v python3 > /dev/null 2>&1 || skip "python3 not installed"
    python3 -c 'import yaml' 2> /dev/null || skip "PyYAML not installed"

    export BATS_TMPDIR="${BATS_TMPDIR:-/tmp}"
    SANDBOX=$(mktemp -d "$BATS_TMPDIR/gen_cursor_agents.XXXXXX")
    SRC="$SANDBOX/src-agents"
    OUT="$SANDBOX/out-agents"
    mkdir -p "$SRC"
}

teardown() {
    [[ -n "${SANDBOX:-}" && -d "$SANDBOX" ]] && rm -rf "$SANDBOX"
}

make_agent() {
    local name="$1" filename="$2" model="$3" effort="$4" desc="$5"
    cat > "$SRC/$filename" << EOF
---
name: $name
description: $desc
model: $model
effort: $effort
---

You are the **$name** role. Body text unchanged by the generator.
EOF
}

# ── Frontmatter translation ─────────────────────────────────────────────────

@test "model is always rewritten to inherit, regardless of the source alias" {
    make_agent scout scout.md haiku low "Read-only lookups."
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    run cat "$OUT/scout.md"
    assert_output --partial "model: inherit"
    refute_output --partial "model: haiku"
}

@test "effort field is dropped entirely" {
    make_agent executor executor.md opus medium "Judgment work."
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    run cat "$OUT/executor.md"
    refute_output --partial "effort"
}

@test "no bare opus/sonnet/haiku model value ever appears in generated output" {
    for f in scout:haiku Explore:haiku mech-executor:sonnet executor:opus verifier:opus security-executor:opus; do
        name="${f%%:*}"
        model="${f##*:}"
        make_agent "$name" "$name.md" "$model" low "desc for $name"
    done
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    run bash -c "grep -hE '^model:' '$OUT'/*.md | grep -vE '^model: inherit$'"
    assert_output ""
}

@test "readonly is true for scout/Explore/verifier, false for the other three" {
    for f in scout:haiku Explore:haiku mech-executor:sonnet executor:opus verifier:opus security-executor:opus; do
        name="${f%%:*}"
        model="${f##*:}"
        make_agent "$name" "$name.md" "$model" low "desc for $name"
    done
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success

    for name in scout Explore verifier; do
        run grep -qxF "readonly: true" "$OUT/$name.md"
        assert_success
    done
    for name in mech-executor executor security-executor; do
        run grep -qxF "readonly: false" "$OUT/$name.md"
        assert_success
    done
}

@test "is_background is set for scout/Explore/mech-executor/verifier, omitted for executor/security-executor" {
    for f in scout:haiku Explore:haiku mech-executor:sonnet executor:opus verifier:opus security-executor:opus; do
        name="${f%%:*}"
        model="${f##*:}"
        make_agent "$name" "$name.md" "$model" low "desc for $name"
    done
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success

    for name in scout Explore mech-executor verifier; do
        run grep -qxF "is_background: true" "$OUT/$name.md"
        assert_success
    done
    for name in executor security-executor; do
        run grep -qF "is_background" "$OUT/$name.md"
        assert_failure
    done
}

@test "name and description are preserved verbatim from the source" {
    make_agent scout scout.md haiku low "Read-only lookups and symbol searches."
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    run cat "$OUT/scout.md"
    assert_output --partial "name: scout"
    assert_output --partial "Read-only lookups and symbol searches."
}

@test "body content is preserved verbatim (only frontmatter differs)" {
    make_agent scout scout.md haiku low "desc"
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    run cat "$OUT/scout.md"
    assert_output --partial "You are the **scout** role. Body text unchanged by the generator."
}

@test "output frontmatter is valid YAML with exactly the Cursor field set" {
    make_agent scout scout.md haiku low "desc"
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    run python3 -c "
import yaml
text = open('$OUT/scout.md').read()
assert text.startswith('---\n')
end = text.index('\n---\n', 4)
fm = yaml.safe_load(text[4:end])
assert set(fm.keys()) <= {'name', 'description', 'model', 'readonly', 'is_background'}, fm.keys()
assert fm['model'] == 'inherit'
assert fm['readonly'] is True
print('schema-ok')
"
    assert_success
    assert_output --partial "schema-ok"
}

# ── Idempotence / change detection ──────────────────────────────────────────

@test "second run on unchanged source is a no-op (byte-identical, reported unchanged)" {
    make_agent scout scout.md haiku low "desc"
    "$GEN" --src "$SRC" --output "$OUT"
    before=$(shasum "$OUT/scout.md")

    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    assert_output --partial "1 unchanged"
    assert_equal "$(shasum "$OUT/scout.md")" "$before"
}

@test "changed source description is reflected on regeneration" {
    make_agent scout scout.md haiku low "old description"
    "$GEN" --src "$SRC" --output "$OUT"
    run cat "$OUT/scout.md"
    assert_output --partial "old description"

    make_agent scout scout.md haiku low "new description"
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    assert_output --partial "1 updated"
    run cat "$OUT/scout.md"
    assert_output --partial "new description"
}

# ── Orphan pruning ───────────────────────────────────────────────────────────

@test "a generated agent removed from source is pruned from output on regenerate" {
    make_agent scout scout.md haiku low "desc"
    make_agent verifier verifier.md opus medium "desc2"
    "$GEN" --src "$SRC" --output "$OUT"
    [ -f "$OUT/verifier.md" ]

    rm -f "$SRC/verifier.md"
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    assert_output --partial "1 removed"
    [ ! -e "$OUT/verifier.md" ]
    [ -f "$OUT/scout.md" ]
}

@test "an explicit single-source run against a shared output dir never deletes the other source's files (provenance-scoped pruning)" {
    # Two independent role sets (mirrors pilotfish vs devpanel) sharing one
    # output dir, generated together first (as a real deploy would default to).
    SRC_B="$SANDBOX/src-agents-b"
    mkdir -p "$SRC_B"
    make_agent scout scout.md haiku low "role-set A"
    SRC="$SRC_B" make_agent developer developer.md opus medium "role-set B"
    "$GEN" --src "$SRC" --src "$SRC_B" --output "$OUT"
    [ -f "$OUT/scout.md" ]
    [ -f "$OUT/developer.md" ]

    # Now regenerate scoped to ONLY role-set A (e.g. a maintainer iterating on
    # just that set) against the SAME shared --output. Role-set B's file must
    # survive — it belongs to a source dir this run never processed, so it is
    # not an orphan, even though it isn't in this run's valid_names.
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    assert_output --partial "0 removed"
    [ -f "$OUT/scout.md" ]
    [ -f "$OUT/developer.md" ]
}

@test "missing provenance manifest (pre-manifest out_dir) is a quiet no-op: untracked file left alone, no warning" {
    make_agent scout scout.md haiku low "desc"
    mkdir -p "$OUT"
    echo "hand-authored, predates the manifest" > "$OUT/legacy.md"
    # No $OUT/.sources.json at all.

    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    assert_output --partial "0 removed"
    refute_output --partial "malformed"
    [ -f "$OUT/legacy.md" ]
}

@test "malformed provenance manifest (invalid JSON) warns on stderr instead of silently reporting 0 removed" {
    make_agent scout scout.md haiku low "desc"
    mkdir -p "$OUT"
    echo "not valid json {" > "$OUT/.sources.json"
    echo "orphan, provenance now unrecoverable" > "$OUT/orphan.md"

    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    assert_output --partial "malformed provenance manifest"
    assert_output --partial "0 removed"
    # Conservative: without recoverable provenance the untracked file is left
    # alone rather than guessed-orphaned.
    [ -f "$OUT/orphan.md" ]
}

@test "malformed provenance manifest (JSON array, not an object) also warns rather than silently swallowing" {
    make_agent scout scout.md haiku low "desc"
    mkdir -p "$OUT"
    echo '["not", "an", "object"]' > "$OUT/.sources.json"

    run "$GEN" --src "$SRC" --output "$OUT"
    assert_success
    assert_output --partial "provenance manifest is not a JSON object"
}

@test "a malformed manifest self-heals: the next run's manifest is rebuilt and valid JSON again" {
    make_agent scout scout.md haiku low "desc"
    mkdir -p "$OUT"
    echo "not valid json {" > "$OUT/.sources.json"
    "$GEN" --src "$SRC" --output "$OUT"

    run python3 -c "
import json
data = json.load(open('$OUT/.sources.json'))
assert data == {'scout.md': 'src-agents'}, data
print('manifest-ok')
"
    assert_success
    assert_output --partial "manifest-ok"
}

@test "provenance-scoped pruning still removes a true orphan once its OWN source dir is (re-)processed" {
    SRC_B="$SANDBOX/src-agents-b"
    mkdir -p "$SRC_B"
    make_agent scout scout.md haiku low "role-set A"
    SRC="$SRC_B" make_agent developer developer.md opus medium "role-set B"
    "$GEN" --src "$SRC" --src "$SRC_B" --output "$OUT"

    rm -f "$SRC_B/developer.md"
    run "$GEN" --src "$SRC" --src "$SRC_B" --output "$OUT"
    assert_success
    assert_output --partial "1 removed"
    [ ! -e "$OUT/developer.md" ]
    [ -f "$OUT/scout.md" ]
}

# ── Dry run ──────────────────────────────────────────────────────────────────

@test "--dry-run on a missing output dir reports would-create and writes nothing" {
    make_agent scout scout.md haiku low "desc"
    run "$GEN" --src "$SRC" --output "$OUT" --dry-run
    assert_success
    assert_output --partial "[DRY-RUN] Would create:"
    [ ! -e "$OUT" ]
}

@test "--dry-run reports would-remove for an orphan without deleting it" {
    make_agent scout scout.md haiku low "desc"
    make_agent verifier verifier.md opus medium "desc2"
    "$GEN" --src "$SRC" --output "$OUT"
    rm -f "$SRC/verifier.md"

    run "$GEN" --src "$SRC" --output "$OUT" --dry-run
    assert_success
    assert_output --partial "[DRY-RUN] would remove:"
    [ -f "$OUT/verifier.md" ]
}

# ── Errors ───────────────────────────────────────────────────────────────────

@test "missing source dir fails with a clear error" {
    rm -rf "$SRC"
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_failure
    assert_output --partial "not found"
}

@test "source agent missing required frontmatter fails with a clear error" {
    cat > "$SRC/broken.md" << 'EOF'
---
name: broken
---

body
EOF
    run "$GEN" --src "$SRC" --output "$OUT"
    assert_failure
    assert_output --partial "description"
}

# ── Wiring into generate_cursor_rules.sh ────────────────────────────────────

@test "generate_cursor_rules.sh invokes the agents generator when pyyaml is available" {
    REPO_SANDBOX="$SANDBOX/repo"
    SKILLS_DIR="$REPO_SANDBOX/configs/claude/skills"
    RULES_DIR="$REPO_SANDBOX/configs/cursor/rules"
    AGENTS_SRC="$REPO_SANDBOX/configs/claude/agents"
    SCRIPTS_DIR="$REPO_SANDBOX/configs/claude/scripts"
    CONFIG_DIR="$REPO_SANDBOX/configs/claude/config"
    mkdir -p "$SKILLS_DIR" "$RULES_DIR" "$AGENTS_SRC" "$SCRIPTS_DIR" "$CONFIG_DIR"
    cp "$GEN" "$SCRIPTS_DIR/generate_cursor_agents.py"
    cp "$REPO_ROOT/configs/claude/scripts/generate_cursor_rules.sh" "$SCRIPTS_DIR/generate_cursor_rules.sh"
    cp "$REPO_ROOT/configs/claude/scripts/cursor_rules_model_guidance.py" \
        "$SCRIPTS_DIR/cursor_rules_model_guidance.py"
    cp -R "$REPO_ROOT/configs/claude/scripts/manifest_model_policy" \
        "$SCRIPTS_DIR/manifest_model_policy"
    # generate_cursor_rules.sh also regenerates configs/cursor/mcp.json (WS-1);
    # stage that generator + a minimal fixture registry too, mirroring
    # generate_cursor_mcp.bats's own sandbox setup.
    cp "$REPO_ROOT/configs/claude/scripts/generate_cursor_mcp.py" "$SCRIPTS_DIR/generate_cursor_mcp.py"
    cat > "$CONFIG_DIR/mcp_servers.yml" << 'EOF'
mcp_servers:
  fixture-server:
    url: "https://example.com/fixture-server"
EOF
    chmod +x "$SCRIPTS_DIR"/*.sh "$SCRIPTS_DIR"/*.py

    SRC="$AGENTS_SRC" make_agent scout scout.md haiku low "desc"

    run "$SCRIPTS_DIR/generate_cursor_rules.sh"
    assert_success
    assert_output --partial "Cursor agents:"
    [ -f "$REPO_SANDBOX/configs/cursor/agents/scout.md" ]
}

# ── Real repo (read-only-ish guarded check) ─────────────────────────────────

@test "real repo: configs/cursor/agents/*.md exist for the nine pilotfish + six devpanel roles" {
    run bash -c "ls '$REPO_ROOT'/configs/cursor/agents/*.md | wc -l | tr -d ' '"
    assert_output "15"
    for name in scout Explore mech-executor executor verifier security-executor context-chronicler compatibility-translator dependency-guardian \
        developer debugger tester spec-guard chaos-engineer performance-auditor; do
        [ -f "$REPO_ROOT/configs/cursor/agents/$name.md" ]
    done
}

@test "real repo: every generated agent has valid Cursor frontmatter (no effort, no bare model alias)" {
    run bash -c "grep -l 'effort:' '$REPO_ROOT'/configs/cursor/agents/*.md"
    assert_failure
    run bash -c "grep -hE '^model:' '$REPO_ROOT'/configs/cursor/agents/*.md | grep -vE '^model: inherit$'"
    assert_output ""
}

@test "real repo: regenerating agents leaves the tree git-clean" {
    if ! git -C "$REPO_ROOT" diff --exit-code --quiet configs/cursor/agents/ 2> /dev/null; then
        skip "configs/cursor/agents/ has local modifications; skipping repo-level check"
    fi

    run python3 "$REPO_ROOT/configs/claude/scripts/generate_cursor_agents.py"
    assert_success
    assert_output --partial "0 created, 0 updated"

    git -C "$REPO_ROOT" diff --exit-code --quiet configs/cursor/agents/
}
