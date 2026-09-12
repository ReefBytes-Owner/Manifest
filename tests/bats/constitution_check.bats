#!/usr/bin/env bats
# CLI gate: check files against the Code Constitution (constitution_check.py).
#
# The exit contract is the whole product here — pre-commit and CI branch on it:
#   0  no blocking finding
#   1  at least one blocking finding
#   2  usage error, or the registry could not be read
# A gate that cannot read its own rules and still reports success is the false
# green this repo has a skill about, so exit 2 is asserted as a real outcome
# rather than lumped in with failure.
#
# The violating fixture is written OUTSIDE the repo, into BATS_TEST_TMPDIR. The
# committed baseline records violation counts by repo-relative path; a fixture
# planted inside the repo could be silently held at the ratchet, and the test
# would pass while proving nothing about detection.

bats_require_minimum_version 1.5.0

load '../test_helper/bats-support/load'
load '../test_helper/bats-assert/load'

setup() {
    CLI="$(cd "$(dirname "$BATS_TEST_FILENAME")/../../plugins/manifest-code-quality/skills/code-audit-constitution/scripts" && pwd)/constitution_check.py"
    SANDBOX="$BATS_TEST_TMPDIR/sandbox"
    mkdir -p "$SANDBOX"
}

@test "--help exits 0 and stays within the 15-line cap" {
    run python3 "$CLI" --help
    assert_success
    assert_output --partial "Usage: constitution_check.py"
    assert [ "${#lines[@]}" -le 15 ]
}

@test "--list prints every article in the registry" {
    run python3 "$CLI" --list
    assert_success
    for id in CON-001 CON-002 CON-003 CON-004 CON-005 CON-006 \
              CON-007 CON-008 CON-009 CON-010 CON-011 CON-012; do
        assert_output --partial "$id"
    done
}

@test "--list prints every check in the registry" {
    run python3 "$CLI" --list
    assert_success
    for id in C-SIZE C-DUPE C-DATA C-TYPE C-ERR C-TEST C-STRUCT C-DOC; do
        assert_output --partial "$id"
    done
}

@test "a clean python file exits 0" {
    printf '"""Add two numbers."""\n\n\ndef add(a: int, b: int) -> int:\n    """Return the sum of a and b."""\n    return a + b\n' \
        > "$SANDBOX/clean.py"
    run --separate-stderr python3 "$CLI" "$SANDBOX/clean.py"
    assert_success
    assert_output ""
}

@test "info findings stay hidden by default" {
    printf 'def undocumented(value):\n    return value\n' > "$SANDBOX/info.py"
    run --separate-stderr python3 "$CLI" "$SANDBOX/info.py"
    assert_success
    assert_output ""
    assert_stderr ""
}

@test "--show-info exposes advisory info findings" {
    printf 'def undocumented(value):\n    return value\n' > "$SANDBOX/info.py"
    run --separate-stderr python3 "$CLI" --show-info "$SANDBOX/info.py"
    assert_success
    assert_stderr --partial "info: [C-DOC/CON-010]"
}

@test "--strict retains info findings for deliberate audits" {
    printf 'def undocumented(value):\n    return value\n' > "$SANDBOX/info.py"
    run --separate-stderr python3 "$CLI" --strict "$SANDBOX/info.py"
    assert_success
    assert_stderr --partial "info: [C-DOC/CON-010]"
}

@test "a new literal data table exits 1 and cites C-DATA" {
    # 90 lines of pure literal dict: over the 15-line container ceiling and over
    # the 80-line error tier, so it must block rather than warn.
    python3 - "$SANDBOX/payload.py" <<'PY'
import sys
body = [f'    "key{i}": "value{i}",' for i in range(88)]
open(sys.argv[1], "w", encoding="utf-8").write("\n".join(["TABLE = {", *body, "}"]) + "\n")
PY
    run bash -c "python3 '$CLI' '$SANDBOX/payload.py' 2>&1"
    assert_failure 1
    assert_output --partial "C-DATA"
    assert_output --partial "CON-004"
}

@test "a nonexistent path is a usage error (exit 2), not a silent pass" {
    run bash -c "python3 '$CLI' '$SANDBOX/no-such-file.py' 2>&1"
    assert_failure 2
    assert_output --partial "no files to check"
}

@test "no arguments at all is a usage error (exit 2)" {
    run bash -c "python3 '$CLI' 2>&1"
    assert_failure 2
    assert_output --partial "no files to check"
}
