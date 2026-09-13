#!/usr/bin/env bats
# WS-2 (2026-07-11-cursor-feature-parity-design.md §3.2/§4): presence guard for
# the 6 CLAUDE.md items ported into configs/cursor/rules/orchestration.mdc.
# orchestration.mdc is hand-maintained (not generated), so future CLAUDE.md
# edits can silently desync it — this test fails loudly the moment any of the
# 6 ported tokens/sections goes missing again.

load '../test_helper/bats-support/load'
load '../test_helper/bats-assert/load'

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"
RULE_FILE="$REPO_ROOT/configs/cursor/rules/orchestration.mdc"

@test "orchestration.mdc contains the Reference Index section with all 8 references" {
    run grep -c '^## Reference Index$' "$RULE_FILE"
    assert_output "1"
    for ref in parallel-agent.md orchestration.md git-platform.md layout.md \
               sub-agent-dispatch.md spec-artifact-discovery.md antipatterns.md \
               doc-concision.md; do
        grep -qF "~/.claude/references/$ref" "$RULE_FILE" || {
            echo "orchestration.mdc: missing reference $ref" >&2
            return 1
        }
    done
}

@test "orchestration.mdc names the plugin refresh command, not a retired one" {
    # This assertion has now been wrong twice in the same way, so it is pinned to
    # the mechanism rather than to a tool name. It first pinned "daily skill dev
    # workflow" to sync-skills; SC-006 made apm-dev-sync the answer; spec 674
    # Phase 5 retires apm-dev-sync too. Each time, the failure mode is identical
    # -- handing a reader a command that will not refresh their skills.
    grep -qF 'claude plugin update' "$RULE_FILE"
    grep -qF '~/.manifest/skills' "$RULE_FILE"
    ! grep -qE '^\*\*CLI tool\*\*.*apm-dev-sync' "$RULE_FILE"
}

@test "orchestration.mdc contains the Proactive Decision Framework" {
    grep -qF '## Proactive Decision Framework' "$RULE_FILE"
    grep -qF 'risk-based review routing' "$RULE_FILE"
    ! grep -qF '### CONSIDER Parallel Agents For' "$RULE_FILE"
}

@test "orchestration.mdc contains the code-audit semantic activation policy" {
    grep -qF '### Auto-Triggered Rule' "$RULE_FILE"
    grep -qF 'security boundary' "$RULE_FILE"
    grep -qF 'vocabulary and complexity metrics alone do not activate it' "$RULE_FILE"
    ! grep -qF '>500 lines, >10 functions, or >5' "$RULE_FILE"
}

@test "orchestration.mdc contains the token-conserve re-assert note" {
    grep -qF 're-asserts this mode if drift is noticed mid-session' "$RULE_FILE"
}
