#!/usr/bin/env bash
# spec_review.sh — cross-reference spec/plan/tasks artifacts for consistency via
# the parallel-agent panel (parallel_agent.py --no-claude), synthesizing the
# reviewers' findings into one deduped list. Analysis-only: never edits artifacts.
# The single-CLI SPEC_REVIEW_CLI seam is reused as the synthesizer and as the
# fallback when the panel is unavailable. Front-end-agnostic — the /spec-review
# skill, the save hook, and any future CLI all wrap this script.
#
# Usage: spec_review.sh [--spec F] [--plan F] [--tasks F] [--silent] [--format tree|json] [ROOT]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPEC_REVIEW_CLI="${SPEC_REVIEW_CLI:-agy}"
SPEC_REVIEW_MODEL="${SPEC_REVIEW_MODEL:-}"
SPEC_REVIEW_CONFIG="${SPEC_REVIEW_CONFIG:-${HOME:-}/.claude/config/parallel_agent.yml}"
SPEC_REVIEW_TEMPLATE="${SPEC_REVIEW_TEMPLATE:-${SCRIPT_DIR}/../prompts/spec_review.md}"
SPEC_REVIEW_STATE="${SPEC_REVIEW_STATE:-.spec-review}"
SPEC_REVIEW_NO_DETACH="${SPEC_REVIEW_NO_DETACH:-}"
# Parallel-agent panel engine. PANEL_CMD fans the prompt across the panel; the
# single-CLI SPEC_REVIEW_CLI seam is reused as the synthesizer (SYNTH_CLI) and as
# the 0-agent fallback. Both injectable so tests can stub external CLIs.
# The `manifest` CLI lives in ~/.local/bin, which a login shell gets from the
# user's profile but hooks, launchd/systemd jobs and cron do not. Put it back on
# PATH rather than hardcoding the path: the command seams below are word-split
# strings, so an absolute path would break on a $HOME containing spaces.
# ${HOME:-} because a clean env (env -i, some CI/hook contexts) has no HOME and
# these scripts run under `set -u` — the --help path must not need it.
case ":${PATH:-}:" in
    *":${HOME:-}/.local/bin:"*) ;;
    *) [[ -n "${HOME:-}" ]] && PATH="$HOME/.local/bin:${PATH:-}" ;;
esac

SPEC_REVIEW_PANEL_CMD="${SPEC_REVIEW_PANEL_CMD:-manifest parallel-agent}"
SPEC_REVIEW_SYNTH_CLI="${SPEC_REVIEW_SYNTH_CLI:-$SPEC_REVIEW_CLI}"
SPEC_REVIEW_MERGE_TEMPLATE="${SPEC_REVIEW_MERGE_TEMPLATE:-${SCRIPT_DIR}/../prompts/spec_review_merge.md}"
SPEC_REVIEW_TIMEOUT="${SPEC_REVIEW_TIMEOUT:-600}"

SPEC=""
PLAN=""
TASKS=""
SILENT=false
FORMAT="tree"
ROOT="."
MODE=""

err() { if [[ -t 2 ]]; then printf '\033[0;31m%s\033[0m\n' "spec-review: $*" >&2; else printf '%s\n' "spec-review: $*" >&2; fi; }
usage() {
    cat << 'EOF'
spec-review — cross-reference spec/plan/tasks for consistency (Antigravity/agy, analysis-only)

Usage: spec_review.sh [--spec F] [--plan F] [--tasks F] [--silent] [--format tree|json] [ROOT]

  --spec/--plan/--tasks F  explicit artifact paths (else auto-discover under ROOT)
  --silent                 hook mode: hash-gated, detached, writes .spec-review/feedback.md
  --format tree|json       output format (default: tree)
  --mode product|technical lifecycle pass (distinct state dir / template); default: neither
  ROOT                     project root to discover in (default: .)
EOF
}

parse_args() {
    # shellcheck disable=SC2034  # vars consumed by future discovery/invoke tasks
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --spec)
                SPEC="$2"
                shift 2
                ;;
            --plan)
                PLAN="$2"
                shift 2
                ;;
            --tasks)
                TASKS="$2"
                shift 2
                ;;
            --silent)
                SILENT=true
                shift
                ;;
            --format)
                FORMAT="$2"
                shift 2
                ;;
            --mode)
                # Sugar over the SPEC_REVIEW_STATE/TEMPLATE seams so the lifecycle's product
                # (phase 3) and technical (phase 7) passes are distinct + auditable (FR-002).
                case "${2:-}" in
                    product) SPEC_REVIEW_STATE="${SPEC_REVIEW_STATE%/}/product" ;;
                    technical)
                        SPEC_REVIEW_STATE="${SPEC_REVIEW_STATE%/}/technical"
                        [[ -f "${SCRIPT_DIR}/../prompts/spec_review_technical.md" ]] &&
                            SPEC_REVIEW_TEMPLATE="${SCRIPT_DIR}/../prompts/spec_review_technical.md"
                        ;;
                    *)
                        err "invalid --mode: '${2:-}' (use product|technical)"
                        return 2
                        ;;
                esac
                MODE="$2"
                shift 2
                ;;
            -h | --help)
                usage
                return 0
                ;;
            -*)
                err "unknown flag: $1"
                return 2
                ;;
            *)
                ROOT="$1"
                shift
                ;;
        esac
    done
}

# Print "role\tpath" lines for discovered artifacts. speckit: spec/plan/tasks.md
# (cwd or specs/<n>/). superpowers: newest *-design.md + newest plans/*.md (tasks
# are embedded in the plan, so no tasks line). Newest = name sort (date-prefixed).
# A FILE root is an explicit spec (FR-001 precedence for "point at the design
# doc" targets) paired within its OWN layout tree — a co-existing speckit
# layout must not hijack a superpowers design doc's plan (feature 482 US3).
discover_artifacts() {
    local root="${1:-.}" sp pl
    if [[ -f "$root" ]]; then
        printf 'spec\t%s\n' "$root"
        case "$root" in
            */docs/superpowers/specs/*.md)
                local sp_base="${root%/specs/*}"
                # shellcheck disable=SC2012  # ls intentional; date-prefixed names
                pl=$(ls -1 "$sp_base"/plans/*.md 2> /dev/null | sort | tail -1 || true)
                [[ -n "$pl" ]] && printf 'plan\t%s\n' "$pl"
                ;;
            *)
                local d
                d="$(dirname "$root")"
                [[ -f "$d/plan.md" ]] && printf 'plan\t%s\n' "$d/plan.md"
                [[ -f "$d/tasks.md" ]] && printf 'tasks\t%s\n' "$d/tasks.md"
                ;;
        esac
        return 0
    fi
    # speckit: specs/<n>/ first, then cwd
    # shellcheck disable=SC2012  # ls used intentionally; files are date-prefixed, no special chars
    sp=$(ls -1 "$root"/specs/*/spec.md 2> /dev/null | sort | tail -1 || true)
    [[ -z "$sp" && -f "$root/spec.md" ]] && sp="$root/spec.md"
    if [[ -n "$sp" ]]; then
        local d
        d="$(dirname "$sp")"
        printf 'spec\t%s\n' "$sp"
        [[ -f "$d/plan.md" ]] && printf 'plan\t%s\n' "$d/plan.md"
        [[ -f "$d/tasks.md" ]] && printf 'tasks\t%s\n' "$d/tasks.md"
        return 0
    fi
    # superpowers: newest design + newest plan (tasks embedded in plan)
    # shellcheck disable=SC2012  # ls used intentionally; files are date-prefixed, no special chars
    sp=$(ls -1 "$root"/docs/superpowers/specs/*-design.md 2> /dev/null | sort | tail -1 || true)
    # shellcheck disable=SC2012  # ls used intentionally; files are date-prefixed, no special chars
    pl=$(ls -1 "$root"/docs/superpowers/plans/*.md 2> /dev/null | sort | tail -1 || true)
    [[ -n "$sp" ]] && printf 'spec\t%s\n' "$sp"
    [[ -n "$pl" ]] && printf 'plan\t%s\n' "$pl"
    return 0
}

# assemble_prompt TEMPLATE "role\tpath"...  ->  full prompt on stdout
assemble_prompt() {
    local template="$1"
    shift
    local artfile line role path
    artfile="$(mktemp)"
    for line in "$@"; do
        role="${line%%$'\t'*}"
        path="${line#*$'\t'}"
        {
            printf '=== %s: %s ===\n' "$(printf '%s' "$role" | tr '[:lower:]' '[:upper:]')" "$path"
            cat "$path"
            printf '\n\n'
        } >> "$artfile"
    done
    # Substitute {{ARTIFACTS}} inline: print lines before the marker, inject
    # artifact file contents, then continue with lines after the marker.
    # Capture awk's status so the temp file is removed even on failure (set -e
    # would otherwise abort before cleanup). bash 3.2-safe (no RETURN trap).
    local rc=0
    awk -v artfile="$artfile" '
        /\{\{ARTIFACTS\}\}/ {
            while ((getline ln < artfile) > 0) print ln
            next
        }
        { print }
    ' "$template" || rc=$?
    rm -f "$artfile"
    return "$rc"
}

# assemble_merge_prompt TEMPLATE REVIEWS_TEXT -> merge prompt on stdout.
# Substitutes {{REVIEWS}} inline with the reviewer-findings block. Mirrors
# assemble_prompt's awk substitution; bash 3.2-safe (no RETURN trap).
assemble_merge_prompt() {
    local template="$1" reviews="$2" reviewfile rc=0
    reviewfile="$(mktemp)"
    printf '%s\n' "$reviews" > "$reviewfile"
    awk -v reviewfile="$reviewfile" '
        /\{\{REVIEWS\}\}/ {
            while ((getline ln < reviewfile) > 0) print ln
            next
        }
        { print }
    ' "$template" || rc=$?
    rm -f "$reviewfile"
    return "$rc"
}

# resolve_review_model -> model name on stdout, or empty. Precedence:
# explicit SPEC_REVIEW_MODEL env always wins; otherwise, only for the default
# agy reviewer, fall back to model_tiers.antigravity.advanced from the shared
# parallel_agent.yml registry (a non-agy CLI would reject agy model names).
# Fail-open: any read/parse problem yields empty (reviewer uses its default).
resolve_review_model() {
    if [[ -n "$SPEC_REVIEW_MODEL" ]]; then
        printf '%s' "$SPEC_REVIEW_MODEL"
        return 0
    fi
    [[ "$SPEC_REVIEW_CLI" == "agy" ]] || return 0
    [[ -f "$SPEC_REVIEW_CONFIG" ]] || return 0
    python3 - "$SPEC_REVIEW_CONFIG" 2> /dev/null << 'PY' || true
import sys

import yaml

try:
    with open(sys.argv[1]) as f:
        cfg = yaml.safe_load(f) or {}
    model = (cfg.get("model_tiers") or {}).get("antigravity", {}).get("advanced", "")
    if model:
        print(model, end="")
except Exception:
    pass
PY
}

# run_reviewer PROMPT -> raw reviewer output. stdin carries the prompt body; the -p
# instruction is short. Model comes from resolve_review_model (may be empty).
# Errors propagate (caller decides fail-open vs surface). An absent reviewer
# CLI is checked explicitly and reported before invocation — never left to a
# bare "command not found" that could be swallowed downstream (false green).
run_reviewer() {
    local prompt="$1" model
    if ! command -v "$SPEC_REVIEW_CLI" > /dev/null 2>&1; then
        err "reviewer CLI not found: $SPEC_REVIEW_CLI (set SPEC_REVIEW_CLI or install it)"
        return 2
    fi
    model="$(resolve_review_model)"
    local cli_args=()
    [[ -n "$model" ]] && cli_args+=(--model "$model")
    cli_args+=(-p "Cross-reference the artifacts above per the instructions; output only the specified blocks or NO_ISSUES.")
    printf '%s' "$prompt" | "$SPEC_REVIEW_CLI" "${cli_args[@]}" # array-safe (unconditional += above)
}

# parse_panel_json JSON_FILE BLOCKS_OUT RAW_OUT  (parallel_agent.py --json file)
# -> stdout: "<count>\t<all_no_issues 0|1>"; writes labeled blocks to BLOCKS_OUT
# and raw outputs (blank-line joined) to RAW_OUT, for the successful agents.
# all_no_issues=1 iff >=1 successful agent and NONE contain "CLARIFICATION
# REQUIRED". Fail-open: malformed/absent JSON yields count 0 (caller falls back).
# JSON is read from a file (not stdin) because the heredoc occupies stdin.
parse_panel_json() {
    python3 - "$1" "$2" "$3" << 'PY'
import json, sys
json_file, blocks_path, raw_path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    text = open(json_file).read()
except Exception:
    text = ""
try:
    data = json.loads(text)
except Exception:
    # tolerate console preamble/trailing noise around the JSON object
    i, j = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[i:j + 1]) if i != -1 and j != -1 else {}
    except Exception:
        data = {}
agents = data.get("agents", {}) or {}
ok = [(n, (a.get("output") or "").strip())
      for n, a in sorted(agents.items())
      if a.get("status") == "complete" and (a.get("output") or "").strip()]
all_ni = bool(ok) and all("CLARIFICATION REQUIRED" not in o for _, o in ok)
with open(blocks_path, "w") as f:
    for n, o in ok:
        f.write("=== REVIEWER: %s ===\n%s\n\n" % (n.upper(), o))
with open(raw_path, "w") as f:
    f.write("\n\n".join(o for _, o in ok))
print("%d\t%d" % (len(ok), 1 if all_ni else 0))
PY
}

# run_synthesizer  (labeled reviews block on stdin) -> merged findings on stdout.
# Builds the merge prompt from the template and pipes it to the single-CLI synth
# seam. Errors propagate so run_panel can fall back to a labeled concat.
run_synthesizer() {
    local reviews prompt model
    if ! command -v "$SPEC_REVIEW_SYNTH_CLI" > /dev/null 2>&1; then
        err "synthesizer CLI not found: $SPEC_REVIEW_SYNTH_CLI"
        return 2
    fi
    reviews="$(cat)"
    prompt="$(assemble_merge_prompt "$SPEC_REVIEW_MERGE_TEMPLATE" "$reviews")"
    model="$(resolve_review_model)"
    local cli_args=()
    [[ -n "$model" ]] && cli_args+=(--model "$model")
    cli_args+=(-p "Merge the reviewer findings above into one deduped list per the instructions; output only the specified blocks or NO_ISSUES.")
    printf '%s' "$prompt" | "$SPEC_REVIEW_SYNTH_CLI" "${cli_args[@]}" # array-safe (unconditional += above)
}

# run_panel PROMPT -> findings text (raw blocks or NO_ISSUES) on stdout.
# Fans the prompt to the parallel-agent panel (excluding the author, claude),
# then aggregates. Any panel/JSON problem falls back to the single-CLI seam;
# a synth failure falls back to a labeled concat so findings are never lost.
run_panel() {
    local prompt="$1" tmpjson tmpblocks tmpraw meta count all_ni out
    local -a panel_cmd
    read -ra panel_cmd <<< "${SPEC_REVIEW_PANEL_CMD}"
    tmpjson="$(mktemp)"
    tmpblocks="$(mktemp)"
    tmpraw="$(mktemp)"
    # Prompt is passed as the trailing positional arg (parallel_agent.py reads no
    # stdin); `--` guards a prompt that might start with '-'. Planning artifacts
    # are bounded, so ARG_MAX is not a concern.
    if ! "${panel_cmd[@]}" --json --no-claude --no-synthesize \
        --no-stream --timeout "$SPEC_REVIEW_TIMEOUT" -- "$prompt" \
        > "$tmpjson" 2> /dev/null; then
        rm -f "$tmpjson" "$tmpblocks" "$tmpraw"
        run_reviewer "$prompt"
        return $?
    fi
    meta="$(parse_panel_json "$tmpjson" "$tmpblocks" "$tmpraw" 2> /dev/null)" || meta="0	0"
    count="${meta%%$'\t'*}"
    all_ni="${meta##*$'\t'}"
    if [[ -z "$count" || "$count" == "0" ]]; then
        rm -f "$tmpjson" "$tmpblocks" "$tmpraw"
        run_reviewer "$prompt"
        return $?
    fi
    if [[ "$all_ni" == "1" ]]; then
        rm -f "$tmpjson" "$tmpblocks" "$tmpraw"
        printf 'NO_ISSUES\n'
        return 0
    fi
    if [[ "$count" == "1" ]]; then
        cat "$tmpraw"
    else
        if ! out="$(run_synthesizer < "$tmpblocks")"; then
            out="$(cat "$tmpblocks")" # synth failed: keep labeled findings
        fi
        printf '%s\n' "$out"
    fi
    rm -f "$tmpjson" "$tmpblocks" "$tmpraw"
    return 0
}

# format_findings RAW FORMAT [COUNT] -> formatted output. NO_ISSUES -> clean
# message (with the artifact count when COUNT is supplied). Empty RAW is NOT
# treated as clean here — callers (review/run_silent) must reject an empty or
# failed reviewer result before calling this, so an absent/broken reviewer CLI
# can never be reported as "no inconsistencies found" (false green).
format_findings() {
    local raw="$1" fmt="${2:-tree}" count="${3:-}"
    if [[ "$raw" == *NO_ISSUES* ]]; then
        if [[ "$fmt" == "json" ]]; then
            echo "[]"
        elif [[ -n "$count" ]]; then
            echo "✓ No inconsistencies found across ${count} artifacts."
        else
            echo "✓ No inconsistencies found."
        fi
        return 0
    fi
    if [[ "$fmt" == "json" ]]; then
        # Minimal: wrap raw blocks as a single JSON string element (tolerant).
        printf '[%s]\n' "$(printf '%s' "$raw" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
    else
        printf '%s\n' "$raw"
    fi
}

# Stable combined-content hash of the given files.
content_hash() {
    cat "$@" 2> /dev/null | shasum | awk '{print $1}'
}

# Gating for silent/hook mode. Returns 0 (run, hash on stdout) or 1 (skip,
# with reason on stdout). The hash is recorded in .last-run only after a
# *successful* review (_silent_review_inline) — recording it here meant one
# transient reviewer failure permanently skipped that content (issue #317).
should_run_silent() {
    local root="${1:-.}" state="$SPEC_REVIEW_STATE"
    # Collect paths into an array (bash 3.2-safe) so paths with spaces hash
    # correctly and the count is exact (no printf/grep off-by-one).
    local paths=() line
    while IFS= read -r line; do [[ -n "$line" ]] && paths+=("$line"); done \
        < <(discover_artifacts "$root" | cut -f2)
    if [[ "${#paths[@]}" -lt 2 ]]; then
        echo "skip: fewer than 2 artifacts"
        return 1
    fi
    mkdir -p "$state"
    local now prev="$state/.last-run"
    now="$(content_hash "${paths[@]+"${paths[@]}"}")"
    if [[ -f "$prev" && "$(cat "$prev")" == "$now" ]]; then
        echo "skip: unchanged"
        return 1
    fi
    echo "$now"
    return 0
}

# Emit role<TAB>path lines from explicit --spec/--plan/--tasks if any were given,
# else auto-discover under ROOT.
resolve_artifacts() {
    local root="${1:-.}"
    if [[ -n "$SPEC" || -n "$PLAN" || -n "$TASKS" ]]; then
        [[ -n "$SPEC" ]] && printf 'spec\t%s\n' "$SPEC"
        [[ -n "$PLAN" ]] && printf 'plan\t%s\n' "$PLAN"
        [[ -n "$TASKS" ]] && printf 'tasks\t%s\n' "$TASKS"
        return 0
    fi
    discover_artifacts "$root"
}

# review ROOT FORMAT -> resolve, assemble, run reviewer, format. Used on-demand.
review() {
    local root="${1:-.}" fmt="${2:-tree}"
    local arts=() line
    while IFS= read -r line; do [[ -n "$line" ]] && arts+=("$line"); done < <(resolve_artifacts "$root")
    if [[ "${#arts[@]}" -eq 0 ]]; then
        echo "spec-review: nothing to review (no artifacts found)"
        return 0
    fi
    echo "[spec-review] Cross-referencing project artifacts with the parallel agent panel…"
    local prompt raw
    prompt="$(assemble_prompt "$SPEC_REVIEW_TEMPLATE" "${arts[@]+"${arts[@]}"}")"
    # An absent/failed reviewer is BLOCKED, never formatted as a clean verdict:
    # both a non-zero run_panel and an empty result (e.g. a reviewer that ran
    # but produced nothing) are explicit errors here, not "no inconsistencies".
    if ! raw="$(run_panel "$prompt")"; then
        err "reviewer unavailable ($SPEC_REVIEW_CLI); no verdict produced"
        return 2
    fi
    if [[ -z "${raw//[[:space:]]/}" ]]; then
        err "reviewer produced no output ($SPEC_REVIEW_CLI); no verdict produced"
        return 2
    fi
    format_findings "$raw" "$fmt" "${#arts[@]}"
}

# The actual review for hook mode, fail-open. Writes feedback.md atomically.
# Records the content hash (2nd arg) in .last-run only on success, so a failed
# review is retried on the next save of the same content (issue #317).
_silent_review_inline() {
    local root="$1" review_hash="${2:-}" state="$SPEC_REVIEW_STATE"
    mkdir -p "$state"
    local arts=() line prompt raw
    while IFS= read -r line; do [[ -n "$line" ]] && arts+=("$line"); done < <(discover_artifacts "$root")
    [[ "${#arts[@]}" -eq 0 ]] && return 0 # defensive: nothing to review (set -u safe)
    prompt="$(assemble_prompt "$SPEC_REVIEW_TEMPLATE" "${arts[@]+"${arts[@]}"}")"
    if ! raw="$(run_panel "$prompt" 2>> "$state/error.log")"; then
        return 0 # fail-open: reviewer failed, never block; hash not recorded
    fi
    # Record the hash only when feedback.md was actually written — a failed
    # write (disk full, permissions) must stay retryable, same as a failed
    # reviewer run (issue #317)
    if format_findings "$raw" "tree" > "$state/feedback.md.tmp" &&
        mv "$state/feedback.md.tmp" "$state/feedback.md"; then
        [[ -n "$review_hash" ]] && echo "$review_hash" > "$state/.last-run"
    fi
    return 0
}

# Silent/hook entry: gate, single-flight lock, detach the reviewer call.
run_silent() {
    local root="${1:-.}" state="$SPEC_REVIEW_STATE" review_hash
    if ! review_hash="$(should_run_silent "$root")"; then
        return 0 # gate said skip (fewer than 2 artifacts / unchanged)
    fi
    mkdir -p "$state"
    # Self-heal a stale lock left by a crashed prior run (older than 10 min), so a
    # killed detached review can never permanently disable the hook.
    if [[ -d "$state/.lock" ]] && find "$state/.lock" -maxdepth 0 -mmin +10 2> /dev/null | grep -q .; then
        rmdir "$state/.lock" 2> /dev/null || true
    fi
    # Single-flight lock: skip if a review is already in flight.
    if ! mkdir "$state/.lock" 2> /dev/null; then return 0; fi
    # `|| true` after the review so an unexpected non-zero (disk full, etc.) never
    # skips the lock release or breaks the fail-open contract.
    if [[ -n "$SPEC_REVIEW_NO_DETACH" ]]; then
        _silent_review_inline "$root" "$review_hash" || true
        rmdir "$state/.lock" 2> /dev/null || true
    else
        # Detach so the agent loop never waits on the reviewer; release lock when done.
        (
            _silent_review_inline "$root" "$review_hash" || true
            rmdir "$state/.lock" 2> /dev/null || true
        ) > /dev/null 2>&1 &
        disown 2> /dev/null || true
    fi
    return 0
}

main() {
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        usage
        return 0
    fi
    parse_args "$@" || return $?
    if [[ "$SILENT" == true ]]; then
        run_silent "$ROOT"
        return 0
    fi
    review "$ROOT" "$FORMAT"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
