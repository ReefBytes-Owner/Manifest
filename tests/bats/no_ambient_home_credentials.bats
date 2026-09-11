#!/usr/bin/env bats
# Correction 16 rule 2 (phase-3-5-decisions.md): no bats test may read the
# developer's real ~/.claude (or any other real-HOME dotfile under
# ~/.claude) -- every test that touches such a path must first sandbox its
# own HOME (or an equivalent variable it built under a tmp sandbox and hands
# to the script under test, e.g. `$BASE` in deploy_reconcile.bats).
#
# This is a static guard, not a runtime one: it greps every OTHER bats file
# for a real filesystem verb (mkdir/touch/rm/cp/mv/ln/chmod/cat/find) or a
# redirection applied directly to a literal `$HOME/.claude` / `${HOME}/.claude`
# / `~/.claude` path, and requires that file to `export HOME=` somewhere.
# Matches inside `grep`/`assert_output`/`assert_line`/test-name strings (no
# fs verb on the same line) are not flagged -- those assert against text
# content, they never touch a real path.

load '../test_helper/bats-support/load'
load '../test_helper/bats-assert/load'

SELF="$(basename "$BATS_TEST_FILENAME")"

@test "no bats test reads a real ~/.claude path without sandboxing its own HOME" {
    local failures=""
    local file base hits
    for file in "$BATS_TEST_DIRNAME"/*.bats; do
        base="$(basename "$file")"
        [[ "$base" == "$SELF" ]] && continue
        if grep -q 'export HOME=' "$file"; then
            continue
        fi
        hits=$(grep -nE \
            '(mkdir|touch|rm|cp|mv|ln|chmod|cat|find)[^#]*(\$\{?HOME\}?/\.claude|~/\.claude)|(\$\{?HOME\}?/\.claude|~/\.claude)[^#]*(>>?)[^"'"'"']*$' \
            "$file" | grep -vE '^[0-9]+:[[:space:]]*#' || true)
        if [[ -n "$hits" ]]; then
            failures+="$base:"$'\n'"$hits"$'\n'
        fi
    done
    if [[ -n "$failures" ]]; then
        echo "$failures"
        false
    fi
}
