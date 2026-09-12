#!/usr/bin/env bats
# ci_mirror_drift.bats — asserts the pr-smoke regression mirror
# (.apm/skills/pr-smoke/scripts/run_pr_regression.sh) stays a
# superset of the canonical checks declared in .github/workflows/ci.yml.
#
# Intent: nothing else catches ci.yml growing a check the mirror doesn't
# run — a contributor can get a clean local `run_pr_regression.sh` while a
# check that only lives in ci.yml silently regresses on push. Each check
# below is its own @test so a failure names exactly which mirrored step
# is missing.
#
# Matching is TOKEN-based (binary + distinctive arg), never whole-line, so
# cosmetic reformatting of either file (line wraps, `python3 -m X` vs a
# bare `X`, quoting) does not false-fail. Two greps per check:
#   1. a sanity grep against ci.yml itself — if THIS fails, ci.yml no
#      longer runs the check the way this test assumes; update the
#      ci-tokens for that @test to match the new ci.yml step, don't just
#      delete the test.
#   2. a grep against run_pr_regression.sh for the mirrored tokens — if
#      THIS fails, the mirror script is missing (or renamed) the step.
#      Either add the invocation to run_pr_regression.sh (and its
#      SKILL.md "What it checks" list), or, if the check is intentionally
#      unmirrored (e.g. a structural symlink/case-collision check that
#      has no meaningful "run it again locally" form), record that
#      decision by deleting the @test with a comment explaining why —
#      never leave it silently red.
#
# How to update when CI legitimately changes: edit the `ci_tokens` /
# `mirror_tokens` pipe-separated lists below to the new command's
# distinctive tokens, or add a new @test for a wholly new check. Do not
# widen a token to something generic ("python3", "run") that would match
# unrelated commands and mask real drift.

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"
CI_YML="$REPO_ROOT/.github/workflows/ci.yml"
MIRROR="$REPO_ROOT/.apm/skills/pr-smoke/scripts/run_pr_regression.sh"

setup() {
    [ -f "$CI_YML" ] || { echo "missing $CI_YML" >&2; return 1; }
    [ -f "$MIRROR" ] || { echo "missing $MIRROR" >&2; return 1; }
}

# has_tokens FILE token... — true iff every token appears as a fixed
# substring SOMEWHERE in FILE's non-comment lines (whole-file, not
# per-line, so a step whose tokens are spread across a multi-line `run:`
# block still matches). Comment lines are stripped first: prose like
# "# bash -n mirrors CI's ..." must not satisfy a deleted step's tokens.
has_tokens() {
    local file="$1"
    shift
    local tok stripped
    stripped="$(grep -vE '^[[:space:]]*#' "$file")"
    for tok in "$@"; do
        printf '%s\n' "$stripped" | grep -qF -- "$tok" || return 1
    done
}

# assert_mirrored LABEL ci_tokens [mirror_tokens]
#   Token lists are '|'-separated strings (not arrays) so a token may
#   contain spaces (e.g. "-S warning") without extra quoting gymnastics,
#   and so this stays portable across the bash 3.2 (macOS) / bash 5
#   (Linux CI) split this repo targets. mirror_tokens defaults to
#   ci_tokens — pass it only when the mirror legitimately spells the
#   step differently.
assert_mirrored() {
    local label="$1" ci_tokens="$2" mirror_tokens="${3:-$2}"
    local -a ci_arr mirror_arr
    IFS='|' read -r -a ci_arr <<< "$ci_tokens"
    IFS='|' read -r -a mirror_arr <<< "$mirror_tokens"

    if ! has_tokens "$CI_YML" "${ci_arr[@]}"; then
        echo "SETUP DRIFT: ci.yml no longer matches the expected tokens for '$label' (${ci_tokens//|/, }) — update this test's ci_tokens to the current ci.yml step instead of deleting the test." >&2
        return 1
    fi
    if ! has_tokens "$MIRROR" "${mirror_arr[@]}"; then
        echo "MIRROR DRIFT: run_pr_regression.sh is missing the '$label' step. ci.yml runs it, but no invocation in the mirror matches all of: ${mirror_tokens//|/, }" >&2
        return 1
    fi
}

@test "shellcheck mirrors configs/claude/scripts/*.sh (-S warning)" {
    assert_mirrored "shellcheck configs/claude/scripts/" \
        "shellcheck|-S warning|configs/claude/scripts/*.sh" \
        "shellcheck|-S warning|manifest_scripts_dir"
}

# `pr-smoke` is a released plugin runtime and must not invoke retired bootstrap
# paths. CI remains the canonical gate for bootstrap scripts (runtime-path policy).

@test "lint guard: check_array_expansion.sh is mirrored" {
    assert_mirrored "check_array_expansion.sh" \
        "tests/lint/check_array_expansion.sh"
}

@test "lint guard: check_bats_assertions.sh is mirrored" {
    assert_mirrored "check_bats_assertions.sh" \
        "tests/lint/check_bats_assertions.sh"
}

# Fresh-checkout self-containment (tests/lint/check_fresh_checkout.sh) is
# CI-owned: the gate itself lives under tests/lint/, outside any bundle, so
# mirroring its invocation into run_pr_regression.sh would itself be a
# bundle-local-reference violation (a pr-smoke skill citing a monorepo-only
# tests/ path).

# Bundle-local reference gate (tools/check_bundle_link_references.py) is
# CI-owned: it needs uv/Python, which
# test_pr_smoke_has_no_project_runtime_dependency forbids in the released
# pr-smoke runtime.

# YAML linting is CI-owned: requiring yamllint would add a tool dependency to
# the portable pr-smoke plugin solely to mirror a project-config validation.

@test "markdownlint mirrors the key-docs globs" {
    assert_mirrored "markdownlint key docs" \
        "markdownlint-cli2|AGENTS.md|CLAUDE.md|README.md|docs/*.md"
}

# Python YAML validation is coordinator-owned in CI. The released pr-smoke
# plugin intentionally uses only its declared executables and no Python packages.

@test "generate_commands_doc.py --check (docs/COMMANDS.md drift) is mirrored" {
    assert_mirrored "generate_commands_doc.py --check" \
        "generate_commands_doc.py|--check" \
        "generate_commands_doc.py|manifest_scripts_dir|--check"
}

@test "bats tests/bats/ is mirrored" {
    assert_mirrored "bats tests/bats/" \
        "bats|tests/bats/"
}

@test "pytest tests/python/ is mirrored" {
    assert_mirrored "pytest tests/python/" \
        "pytest|tests/python/"
}

# Smoke execution is coordinator-owned (`manifest smoke`) and forbidden from
# plugin runtimes; CI runs this Verify gate after installing the coordinator.

@test "selected-interpreter shell-syntax validation is mirrored" {
    assert_mirrored "selected-interpreter shell syntax" \
        "bash -n" \
        "check_shell_syntax|PR_SMOKE_BASH"
}
