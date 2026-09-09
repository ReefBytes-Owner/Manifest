#!/usr/bin/env bash
set -uo pipefail

usage() {
    cat << 'EOF'
Usage: run_pr_regression.sh [--quick] [--help]

Runs repository-local, offline regression gates.
Quick profile: tracked staged + unstaged whitespace only; untracked files excluded.
Full profile: portable regression subset; not full CI verification.

Exit 0=PASS, 2=FAIL, 3=BLOCKED.
EOF
}

QUICK=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick) QUICK=1 ;;
        --help | -h)
            usage
            exit 0
            ;;
        *)
            echo "pr-smoke: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [[ "$QUICK" -eq 1 ]]; then
    PROFILE='Profile: quick (tracked staged + unstaged whitespace only; untracked files excluded)'
else
    PROFILE='Profile: portable regression subset; not full CI verification'
fi

if ! command -v git > /dev/null 2>&1; then
    echo "$PROFILE"
    echo '| Gate | Result |'
    echo '|---|---|'
    echo '| Git | BLOCKED (missing git) |'
    echo 'Verdict: BLOCKED (1 required checks unavailable)'
    exit 3
fi

if ! git rev-parse --show-toplevel > /dev/null 2>&1; then
    echo "pr-smoke: run from a git repository" >&2
    exit 2
fi

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT" || exit 2

# Discover Manifest's optional project-local tools without binding this bundle to
# a deployed home or a fixed source-tree path. A successful empty result means
# the feature is absent; a failed query means its required gates are unknown.
manifest_generator=""
generator_discovery_blocked=0
if [[ "$QUICK" -eq 0 ]]; then
    if manifest_generators="$(git ls-files -- '*/generate_commands_doc.py')"; then
        manifest_generator="${manifest_generators%%$'\n'*}"
    else
        generator_discovery_blocked=1
    fi
fi
manifest_scripts_dir=""
if [[ -n "$manifest_generator" ]]; then
    manifest_scripts_dir="${manifest_generator%/generate_commands_doc.py}"
fi

# Resolve Bash before rendering results so an isolated child PATH does not hide
# the interpreter selected by the caller. Any unavailable result is emitted as
# a gate row below, after the Markdown table header.
requested_bash="${PR_SMOKE_BASH:-bash}"
resolved_bash="$(command -v "$requested_bash" 2> /dev/null || true)"
PR_SMOKE_BASH="${resolved_bash:-$requested_bash}"
BASH_BIN="${PR_SMOKE_BASH:-bash}"
bash_syntax_available=1
if ! command -v "$BASH_BIN" > /dev/null 2>&1; then
    bash_syntax_available=0
fi

FAILURES=0
BLOCKED=0

run_gate() {
    local name="$1"
    shift
    if "$@"; then
        printf '| %s | PASS |\n' "$name"
    else
        printf '| %s | FAIL |\n' "$name"
        FAILURES=$((FAILURES + 1))
    fi
}

check_shell_syntax() {
    local directory="$1" file failed=0 seen=0
    for file in "$directory"/*.sh; do
        [[ -f "$file" ]] || continue
        seen=$((seen + 1))
        if ! "$BASH_BIN" -n "$file"; then
            failed=1
        fi
    done
    if [[ "$seen" -eq 0 ]]; then
        printf 'shell syntax: no shell files in %s\n' "$directory" >&2
        return 1
    fi
    return "$failed"
}

required_gate() {
    local name="$1" binary="$2"
    shift 2
    if ! command -v "$binary" > /dev/null 2>&1; then
        printf '| %s | BLOCKED (missing %s) |\n' "$name" "$binary"
        BLOCKED=$((BLOCKED + 1))
        return 0
    fi
    run_gate "$name" "$@"
}

required_path_gate() {
    local name="$1" executable="$2"
    shift 2
    if [[ ! -x "$executable" ]]; then
        printf '| %s | BLOCKED (missing executable %s) |\n' "$name" "$executable"
        BLOCKED=$((BLOCKED + 1))
        return 0
    fi
    run_gate "$name" "$executable" "$@"
}

required_python_module_gate() {
    local name="$1" binary="$2" module="$3"
    shift 3
    if ! command -v "$binary" > /dev/null 2>&1; then
        printf '| %s | BLOCKED (missing %s) |\n' "$name" "$binary"
        BLOCKED=$((BLOCKED + 1))
        return 0
    fi
    if ! "$binary" -c \
        'import importlib.util,sys; sys.exit(importlib.util.find_spec(sys.argv[1]) is None)' \
        "$module"; then
        printf '| %s | BLOCKED (missing Python module %s) |\n' "$name" "$module"
        BLOCKED=$((BLOCKED + 1))
        return 0
    fi
    run_gate "$name" "$@"
}

echo "$PROFILE"
echo '| Gate | Result |'
echo '|---|---|'
if [[ "$QUICK" -eq 0 && -n "$manifest_scripts_dir" && "$bash_syntax_available" -eq 0 ]]; then
    printf '| shell syntax | BLOCKED (missing %s) |\n' "$BASH_BIN"
    BLOCKED=$((BLOCKED + 1))
fi
if [[ "$generator_discovery_blocked" -eq 1 ]]; then
    echo '| command generator discovery | BLOCKED (git ls-files failed) |'
    BLOCKED=$((BLOCKED + 1))
fi
run_gate 'unstaged whitespace' git diff --check
run_gate 'staged whitespace' git diff --cached --check

if [[ "$QUICK" -eq 0 ]]; then
    if [[ -n "$manifest_scripts_dir" ]]; then
        required_gate 'ShellCheck Manifest scripts' shellcheck \
            shellcheck -S warning "$manifest_scripts_dir"/*.sh
    fi
    required_path_gate 'empty-array expansion lint' \
        tests/lint/check_array_expansion.sh
    required_path_gate 'Bats assertion lint' tests/lint/check_bats_assertions.sh
    required_gate 'Markdown lint' markdownlint-cli2 \
        markdownlint-cli2 AGENTS.md CLAUDE.md README.md docs/*.md
    if [[ -n "$manifest_scripts_dir" ]]; then
        required_path_gate 'command guide drift' \
            "$manifest_scripts_dir/generate_commands_doc.py" --check
        if [[ "$bash_syntax_available" -eq 1 ]]; then
            run_gate 'shell syntax' check_shell_syntax "$manifest_scripts_dir"
        fi
    fi
fi

if [[ "$QUICK" -eq 0 && -d tests/python ]]; then
    required_python_module_gate 'python tests' python3 pytest \
        python3 -m pytest tests/python/ -q
fi
if [[ "$QUICK" -eq 0 && -d tests/bats ]]; then
    required_gate 'bats tests' bats bats tests/bats/
fi
if [[ "$FAILURES" -gt 0 ]]; then
    printf 'Verdict: FAIL (%s failed, %s blocked)\n' "$FAILURES" "$BLOCKED"
    exit 2
fi
if [[ "$BLOCKED" -gt 0 ]]; then
    printf 'Verdict: BLOCKED (%s required checks unavailable)\n' "$BLOCKED"
    exit 3
fi
echo 'Verdict: PASS (selected profile only)'
