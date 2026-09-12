#!/usr/bin/env bats
# Tests for bootstrap/lib/install.sh — install_uv_verified_release()
#
# C7f: replaced `curl astral.sh/uv/install.sh | sh` with a real download of
# the pinned uv release plus its independently-published `.sha256` sidecar,
# verified before extraction. These tests fake `curl`/`uname` on a real PATH
# (no network) and use the real `tar`/checksum tools against a real archive,
# proving the verify-then-extract path both accepts a matching digest and
# blocks a tampered one -- the exact property CON-013 exists to protect.

load '../test_helper/bats-support/load'
load '../test_helper/bats-assert/load'

REPO_ROOT="$BATS_TEST_DIRNAME/../.."
INSTALL_LIB="$REPO_ROOT/bootstrap/lib/install.sh"

setup() {
    export BATS_TMPDIR="${BATS_TMPDIR:-/tmp}"
    SANDBOX=$(mktemp -d "$BATS_TMPDIR/install_uv_verified_release.XXXXXX")
    export HOME="$SANDBOX/home"
    mkdir -p "$HOME/.local/bin"

    MOCK_BIN="$SANDBOX/bin"
    mkdir -p "$MOCK_BIN"

    # A real gzip archive shaped like a genuine uv release tarball: a top
    # directory `uv-<target>/` containing executable `uv` and `uvx` files.
    ARCHIVE_ROOT="$SANDBOX/archive-src"
    mkdir -p "$ARCHIVE_ROOT/uv-aarch64-apple-darwin"
    printf '#!/bin/sh\necho real-uv\n' \
        > "$ARCHIVE_ROOT/uv-aarch64-apple-darwin/uv"
    printf '#!/bin/sh\necho real-uvx\n' \
        > "$ARCHIVE_ROOT/uv-aarch64-apple-darwin/uvx"
    chmod +x "$ARCHIVE_ROOT/uv-aarch64-apple-darwin/uv" \
        "$ARCHIVE_ROOT/uv-aarch64-apple-darwin/uvx"
    ARCHIVE="$SANDBOX/uv-aarch64-apple-darwin.tar.gz"
    tar -czf "$ARCHIVE" -C "$ARCHIVE_ROOT" uv-aarch64-apple-darwin
    if command -v sha256sum >/dev/null 2>&1; then
        REAL_SHA="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
    else
        REAL_SHA="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
    fi
    export ARCHIVE REAL_SHA

    cat > "$MOCK_BIN/uname" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  -s) echo "Darwin" ;;
  -m) echo "arm64" ;;
esac
STUB
    chmod +x "$MOCK_BIN/uname"

    # Fake curl: `-o dest url` copies the fixture archive for the .tar.gz
    # request and writes SHA_TO_SERVE (set per test) for the .sha256 request.
    # No network I/O happens in this test at all.
    cat > "$MOCK_BIN/curl" <<'STUB'
#!/usr/bin/env bash
dest="" url=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "-o" ]]; then
        dest="${args[$((i + 1))]}"
    elif [[ "${args[$i]}" != -* && -z "$url" ]]; then
        url="${args[$i]}"
    fi
done
if [[ "$url" == *.sha256 ]]; then
    printf '%s  %s\n' "${SHA_TO_SERVE:-$REAL_SHA}" "$(basename "${url%.sha256}")" > "$dest"
else
    cp "$ARCHIVE" "$dest"
fi
exit 0
STUB
    chmod +x "$MOCK_BIN/curl"
    export PATH="$MOCK_BIN:$PATH"

    print_step()    { :; }
    print_success() { :; }
    print_info()    { :; }
    print_warning() { echo "WARN: $*"; }
    print_error()   { echo "ERR: $*"; }
    command_exists() { command -v "$1" >/dev/null 2>&1; }
    # shellcheck disable=SC1090
    source "$INSTALL_LIB"
}

teardown() {
    [[ -n "$SANDBOX" && -d "$SANDBOX" ]] && rm -rf "$SANDBOX"
}

@test "install_uv_verified_release installs uv/uvx when the checksum matches" {
    run install_uv_verified_release
    assert_success
    assert [ -x "$HOME/.local/bin/uv" ]
    assert [ -x "$HOME/.local/bin/uvx" ]
    run "$HOME/.local/bin/uv"
    assert_output "real-uv"
}

@test "install_uv_verified_release refuses a tampered archive (checksum mismatch)" {
    export SHA_TO_SERVE="0000000000000000000000000000000000000000000000000000000000000"
    run install_uv_verified_release
    assert_failure
    assert_output --partial "checksum mismatch"
    assert [ ! -e "$HOME/.local/bin/uv" ]
}

@test "install_uv_verified_release fails closed on an unrecognized platform" {
    cat > "$MOCK_BIN/uname" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  -s) echo "Plan9" ;;
  -m) echo "mips" ;;
esac
STUB
    chmod +x "$MOCK_BIN/uname"

    run install_uv_verified_release
    assert_failure
    assert_output --partial "no known release target"
    assert [ ! -e "$HOME/.local/bin/uv" ]
}
