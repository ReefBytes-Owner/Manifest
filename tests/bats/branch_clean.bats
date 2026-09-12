#!/usr/bin/env bats
# Tests for configs/claude/scripts/branch_clean.sh

load '../test_helper/bats-support/load'
load '../test_helper/bats-assert/load'
load '../test_helper/git_identity.bash'

REPO_ROOT="$BATS_TEST_DIRNAME/../.."
SCRIPT="$REPO_ROOT/configs/claude/scripts/branch_clean.sh"

setup() {
    export BATS_TMPDIR="${BATS_TMPDIR:-/tmp}"
    SANDBOX=$(mktemp -d "$BATS_TMPDIR/branch_clean.XXXXXX")
    export BRANCH_CLEAN_CONFIG="$REPO_ROOT/configs/claude/config/command_config.yml"
    git_identity_begin "$SANDBOX"
    REMOTE=$(mktemp -d "$BATS_TMPDIR/branch_clean_remote.XXXXXX")
    git init -q --bare "$REMOTE/origin.git"
    cd "$SANDBOX"
    git init -q -b main .
    git commit -q --allow-empty -m init
    git remote add origin "$REMOTE/origin.git"
    git push -q -u origin main
    # merged branch (no extra commits -> fully merged into main)
    git branch merged-feature
    # protected branch
    git branch release/keepme
    # stale branch: a merged-state branch with an old commit date is still 'merged';
    # create a stale, unmerged-but-old branch instead
    GIT_AUTHOR_DATE="2020-01-01T00:00:00" GIT_COMMITTER_DATE="2020-01-01T00:00:00" \
        git checkout -q -b stale-spike
    GIT_AUTHOR_DATE="2020-01-01T00:00:00" GIT_COMMITTER_DATE="2020-01-01T00:00:00" \
        git commit -q --allow-empty -m "old spike"
    git checkout -q main
}

teardown() {
    cd /
    [[ -n "$SANDBOX" && -d "$SANDBOX" ]] && rm -rf "$SANDBOX"
    [[ -n "$REMOTE" && -d "$REMOTE" ]] && rm -rf "$REMOTE"
    git_identity_end
}

@test "merged branch is listed as a delete candidate" {
    run "$SCRIPT" --default main --protect 'release/*'
    assert_success
    assert_output --partial "merged-feature"
    assert_output --partial "Merged into main"
}

@test "stale branch beyond threshold is listed" {
    run "$SCRIPT" --default main --protect 'release/*' --stale-days 90
    assert_success
    assert_output --partial "stale-spike"
    assert_output --partial "stale"
}

@test "protected and current branches are never proposed" {
    run "$SCRIPT" --default main --protect 'release/*'
    assert_success
    refute_output --partial "- release/keepme"
    refute_output --partial "- main "
}

@test "dry-run is the default and deletes nothing" {
    run "$SCRIPT" --default main --protect 'release/*'
    assert_success
    assert_output --partial "dry-run, nothing deleted"
    run git branch --list merged-feature
    assert_output --partial "merged-feature"
}

@test "--apply --yes deletes a merged branch" {
    run "$SCRIPT" --default main --protect 'release/*' --apply --yes
    assert_success
    assert_output --partial "deleted  merged-feature"
    run git branch --list merged-feature
    assert_output ""
}

@test "[gone] branch (with unique commit) is classified as gone" {
    git checkout -q -b gone-feature
    git commit -q --allow-empty -m "unique work"   # not merged into main
    git push -q -u origin gone-feature
    git push -q origin --delete gone-feature
    git fetch -q -p
    git checkout -q main
    run "$SCRIPT" --default main --protect 'release/*'
    assert_success
    assert_output --partial "gone-feature"
    assert_output --partial "Gone upstream"
}

@test "safe delete refuses an unmerged branch and reports failure (FR-020)" {
    # stale-spike has an unmerged commit; --apply must not force-delete it.
    run "$SCRIPT" --default main --protect 'release/*' --stale-days 90 --apply --yes
    assert_success
    assert_output --partial "FAILED   stale-spike"
    run git branch --list stale-spike
    assert_output --partial "stale-spike"
}

@test "remote deletion is gated on a successful local safe-delete" {
    # A stale branch with a remote ref, then a further local-only commit so it is
    # unmerged vs its upstream -> `git branch -d` refuses it. With --include-remote
    # the failed local safe-delete must leave the remote branch intact.
    GIT_AUTHOR_DATE="2020-01-01T00:00:00" GIT_COMMITTER_DATE="2020-01-01T00:00:00" \
        git checkout -q -b stale-remote
    GIT_AUTHOR_DATE="2020-01-01T00:00:00" GIT_COMMITTER_DATE="2020-01-01T00:00:00" \
        git commit -q --allow-empty -m "pushed work"
    git push -q -u origin stale-remote
    GIT_AUTHOR_DATE="2020-01-02T00:00:00" GIT_COMMITTER_DATE="2020-01-02T00:00:00" \
        git commit -q --allow-empty -m "local-only ahead"
    git checkout -q main
    run "$SCRIPT" --default main --protect 'release/*' --stale-days 90 --apply --yes --include-remote
    assert_success
    assert_output --partial "FAILED   stale-remote"
    run git ls-remote --heads origin stale-remote
    assert_output --partial "stale-remote"
}

@test "--json emits candidates with reasons" {
    run "$SCRIPT" --default main --protect 'release/*' --json
    assert_success
    echo "$output" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert any(c["reason"]=="merged" for c in d["candidates"]); assert d["scope"]=="local"'
}
