#!/usr/bin/env bash
# git_ops.sh - Platform-agnostic Git operations wrapper
#
# Routes Git operations (issue/PR management) to the appropriate CLI tool:
# - GitHub: gh
# - GitLab: glab
# - Plain git: warn and suggest installation
#
# Usage: git_ops.sh <subcommand> [args...]
#
# Subcommands:
#   issue-view N          View issue/MR N
#   issue-list            List issues/MRs
#   issue-create          Create new issue/MR
#   issue-comment N       Add comment/note to issue/MR N
#   issue-comment-edit-last N  Edit last comment on issue/MR N
#   issue-close N         Close issue/MR N
#   issue-edit N          Edit issue/MR N
#   pr-create             Create pull/merge request
#   pr-view N             View PR/MR N
#   pr-edit N             Edit PR/MR N (e.g. --body for description)
#   pr-list               List PRs/MRs
#   pr-review N           Review/approve PR/MR N
#   pr-merge N            Merge PR/MR N
#   pr-close N            Close PR/MR N
#   pr-reopen N           Reopen a closed PR/MR N
#   pr-update-branch N    Sync PR/MR N's branch with its base (github: merge; gitlab: rebase)
#   pr-comment N TEXT     Add comment/note to PR/MR N
#   pr-comments N         List review comments on PR/MR N (JSON)
#   repo-admin-check      Print true/false: does the authenticated user have admin/maintainer rights
#   branch-protection     Print main's protection flags (enforce_admins/required_signatures/merge_queue); github-only
#   commit-checks SHA     List CI conclusions/statuses for a commit SHA (JSON array)
#   release-create        Create a release
#   release-list          List releases
#   label-create          Create label
#   label-delete          Delete label
#   label-list            List labels
#   label-sync            Sync labels from registry to platform

set -euo pipefail

err() { if [[ -t 2 ]]; then printf '\033[0;31m%s\033[0m\n' "git-ops: $*" >&2; else printf '%s\n' "git-ops: $*" >&2; fi; }

# issue_comment_args BODY_FLAG N [ARGS...] — sets ISSUE_COMMENT_ARGS.
# The documented invocation is `issue-comment <N> "<text>"` (issue #475): when
# the arg after N is a non-flag positional it becomes `BODY_FLAG <text>`;
# flag-style invocations (--body, --body-file, -R ...) pass through unchanged.
issue_comment_args() {
    local body_flag="$1"
    shift
    ISSUE_COMMENT_ARGS=("$@")
    if [[ $# -ge 2 && "${2:0:1}" != "-" ]]; then
        local n="$1" body="$2"
        shift 2
        ISSUE_COMMENT_ARGS=("$n" "$body_flag" "$body" "$@")
    fi
}

usage() {
    cat << 'USAGE'
Usage: git_ops.sh <subcommand> [args...]

Subcommands:
  issue-view N       View issue/MR N
  issue-list         List issues/MRs
  issue-create       Create new issue/MR
  issue-comment N    Add comment/note to issue/MR N
  issue-comment-edit-last N  Edit last comment on issue/MR N
  issue-close N      Close issue/MR N
  issue-edit N       Edit issue/MR N
  pr-create          Create pull/merge request
  pr-view N          View PR/MR N
  pr-edit N          Edit PR/MR N (e.g. --body for description)
  pr-list            List PRs/MRs
  pr-review N        Review PR/MR N (pass --approve/--comment/--request-changes)
  pr-approve N       Approve PR/MR N (shortcut for pr-review --approve)
  pr-diff N          View PR/MR N diff
  pr-checks N        View CI status for PR/MR N
  pr-merge N         Merge PR/MR N
  pr-close N         Close PR/MR N
  pr-reopen N        Reopen a closed PR/MR N
  pr-update-branch N Sync PR/MR N's branch with its base (github: merge; gitlab: rebase)
  pr-comment N TEXT  Add comment/note to PR/MR N
  pr-comments N      List review comments on PR/MR N (JSON)
  repo-admin-check   Print true/false: authenticated user has admin/maintainer rights
  branch-protection  Print main's protection flags (enforce_admins/required_signatures/merge_queue); github-only
  commit-checks SHA  List CI conclusions/statuses for a commit SHA (JSON array)
  release-create     Create a release
  release-list       List releases
  label-create       Create label
  label-delete       Delete label
  label-list         List labels
  label-sync         Sync labels from registry
USAGE
}

# Validate subcommand (before any platform/config/credential lookup)
if [[ $# -eq 0 ]]; then
    usage >&2
    exit 1
fi

if [[ "$1" == "--help" || "$1" == "-h" || "$1" == "help" ]]; then
    usage
    exit 0
fi

subcommand="$1"
shift

# Get script directory for sourcing git_platform.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source platform detection
if [[ ! -f "${SCRIPT_DIR}/git_platform.sh" ]]; then
    err "git_platform.sh not found in ${SCRIPT_DIR}"
    exit 1
fi

# Detect platform
if ! platform=$(bash "${SCRIPT_DIR}/git_platform.sh" 2>&1); then
    err "Failed to detect Git platform: ${platform}"
    exit 1
fi

# Helper: Check if command exists
command_exists() {
    command -v "$1" &> /dev/null
}

# Helper: Warn about missing CLI tool
warn_missing_tool() {
    local tool="$1"
    local install_hint="$2"
    err "Warning: ${tool} CLI not found. Install it to enable this operation."
    err "Install: ${install_hint}"
    exit 1
}

# Helper: Fallback for plain git (no issue tracker)
warn_no_tracker() {
    err "Warning: No issue tracker detected (plain git remote)."
    err "This operation requires GitHub (gh) or GitLab (glab) CLI."
    exit 1
}

# Route operations based on platform and subcommand
case "${platform}" in
    github)
        if ! command_exists gh; then
            warn_missing_tool "GitHub" "brew install gh  # or: npm install -g @github/gh-cli"
        fi

        case "${subcommand}" in
            issue-view)
                gh issue view "$@"
                ;;
            issue-list)
                gh issue list "$@"
                ;;
            issue-create)
                gh issue create "$@"
                ;;
            issue-comment)
                issue_comment_args --body "$@"
                gh issue comment "${ISSUE_COMMENT_ARGS[@]+"${ISSUE_COMMENT_ARGS[@]}"}"
                ;;
            issue-comment-edit-last)
                gh issue comment "$@" --edit-last
                ;;
            issue-close)
                gh issue close "$@"
                ;;
            issue-edit)
                gh issue edit "$@"
                ;;
            pr-create)
                gh pr create "$@"
                ;;
            pr-view)
                gh pr view "$@"
                ;;
            pr-edit)
                gh pr edit "$@"
                ;;
            pr-list)
                gh pr list "$@"
                ;;
            pr-review)
                gh pr review "$@"
                ;;
            pr-approve)
                gh pr review --approve "$@"
                ;;
            pr-diff)
                gh pr diff "$@"
                ;;
            pr-checks)
                gh pr checks "$@"
                ;;
            pr-merge)
                gh pr merge "$@"
                ;;
            pr-close)
                gh pr close "$@"
                ;;
            pr-reopen)
                gh pr reopen "$@"
                ;;
            pr-update-branch)
                gh pr update-branch "$@"
                ;;
            pr-comment)
                issue_comment_args --body "$@"
                gh pr comment "${ISSUE_COMMENT_ARGS[@]+"${ISSUE_COMMENT_ARGS[@]}"}"
                ;;
            pr-comments)
                pr_num="$1"
                shift
                gh api "repos/{owner}/{repo}/pulls/${pr_num}/comments" \
                    --jq '[.[] | {id, author: .user.login, path, line, body}]' "$@"
                ;;
            repo-admin-check)
                gh api "repos/{owner}/{repo}" -q '.permissions.admin' "$@" 2> /dev/null || echo false
                ;;
            branch-protection)
                gh api "repos/{owner}/{repo}/branches/main/protection" \
                    -q '"enforce_admins="+(.enforce_admins.enabled|tostring)+" required_signatures="+(.required_signatures.enabled|tostring)+" merge_queue=false"' \
                    "$@" 2> /dev/null ||
                    echo "enforce_admins=false required_signatures=false merge_queue=false"
                ;;
            commit-checks)
                sha="$1"
                shift
                gh api "repos/{owner}/{repo}/commits/${sha}/check-runs" -q '[.check_runs[]|.conclusion]' "$@"
                ;;
            release-create)
                gh release create "$@"
                ;;
            release-list)
                gh release list "$@"
                ;;
            label-create)
                gh label create "$@"
                ;;
            label-delete)
                gh label delete "$@"
                ;;
            label-list)
                gh label list "$@"
                ;;
            label-sync)
                bash "${SCRIPT_DIR}/label_sync.sh" "$@"
                ;;
            *)
                err "Unknown subcommand: ${subcommand}"
                exit 1
                ;;
        esac
        ;;

    gitlab)
        if ! command_exists glab; then
            warn_missing_tool "GitLab" "brew install glab  # or: pip install python-gitlab-cli"
        fi

        case "${subcommand}" in
            issue-view)
                glab issue view "$@"
                ;;
            issue-list)
                glab issue list "$@"
                ;;
            issue-create)
                glab issue create "$@"
                ;;
            issue-comment)
                # GitLab uses 'note' instead of 'comment'; glab's body flag is
                # -m/--message (mocked in tests; glab is not installed here)
                issue_comment_args --message "$@"
                glab issue note "${ISSUE_COMMENT_ARGS[@]+"${ISSUE_COMMENT_ARGS[@]}"}"
                ;;
            issue-comment-edit-last)
                issue_num="$1"
                shift
                # `// empty` so an empty notes array yields "" instead of the
                # literal "null" (which issued PUT to notes/null — issue #316)
                last_note_id=$(glab api "projects/:id/issues/${issue_num}/notes?sort=desc&per_page=1" --jq '.[0].id // empty')
                if [[ -n "${last_note_id}" && "${last_note_id}" != "null" ]]; then
                    body=""
                    while [[ $# -gt 0 ]]; do
                        case "$1" in
                            --body | -b)
                                body="$2"
                                shift 2
                                ;;
                            *) shift ;;
                        esac
                    done
                    glab api "projects/:id/issues/${issue_num}/notes/${last_note_id}" -X PUT -f "body=${body}"
                else
                    err "No comments found on issue ${issue_num}"
                    exit 1
                fi
                ;;
            issue-close)
                glab issue close "$@"
                ;;
            issue-edit)
                # GitLab uses 'update' instead of 'edit', and --label/--unlabel
                # instead of gh's --add-label/--remove-label.
                _translate_issue_edit_flags() {
                    local issue_num="$1"
                    shift
                    local -a issue_args=("${issue_num}")
                    while [[ $# -gt 0 ]]; do
                        case "$1" in
                            --add-label)
                                issue_args+=(--label "$2")
                                shift 2
                                ;;
                            --add-label=*)
                                issue_args+=(--label "${1#--add-label=}")
                                shift
                                ;;
                            --remove-label)
                                issue_args+=(--unlabel "$2")
                                shift 2
                                ;;
                            --remove-label=*)
                                issue_args+=(--unlabel "${1#--remove-label=}")
                                shift
                                ;;
                            *)
                                issue_args+=("$1")
                                shift
                                ;;
                        esac
                    done
                    glab issue update "${issue_args[@]}"
                }
                _translate_issue_edit_flags "$@"
                ;;
            pr-create)
                # GitLab uses 'mr' (merge request) instead of 'pr'
                # Translate GitHub-style flags to GitLab equivalents
                _translate_pr_flags() {
                    local -a mr_args=()
                    while [[ $# -gt 0 ]]; do
                        case "$1" in
                            --body)
                                mr_args+=(--description "$2")
                                shift 2
                                ;;
                            --body=*)
                                mr_args+=(--description "${1#--body=}")
                                shift
                                ;;
                            --base)
                                mr_args+=(--target-branch "$2")
                                shift 2
                                ;;
                            --base=*)
                                mr_args+=(--target-branch "${1#--base=}")
                                shift
                                ;;
                            *)
                                mr_args+=("$1")
                                shift
                                ;;
                        esac
                    done
                    glab mr create "${mr_args[@]+"${mr_args[@]}"}"
                }
                _translate_pr_flags "$@"
                ;;
            pr-view)
                glab mr view "$@"
                ;;
            pr-edit)
                # GitLab uses 'mr update' with --description for body edits.
                _translate_pr_edit_flags() {
                    local mr_num="$1"
                    shift
                    local -a mr_args=("${mr_num}")
                    while [[ $# -gt 0 ]]; do
                        case "$1" in
                            --body)
                                mr_args+=(--description "$2")
                                shift 2
                                ;;
                            --body=*)
                                mr_args+=(--description "${1#--body=}")
                                shift
                                ;;
                            --base)
                                mr_args+=(--target-branch "$2")
                                shift 2
                                ;;
                            --base=*)
                                mr_args+=(--target-branch "${1#--base=}")
                                shift
                                ;;
                            *)
                                mr_args+=("$1")
                                shift
                                ;;
                        esac
                    done
                    glab mr update "${mr_args[@]}" # array-safe: seeded with mr_num
                }
                _translate_pr_edit_flags "$@"
                ;;
            pr-list)
                glab mr list "$@"
                ;;
            pr-review)
                # GitLab uses 'mr approve' for review approval
                glab mr approve "$@"
                ;;
            pr-approve)
                glab mr approve "$@"
                ;;
            pr-diff)
                glab mr diff "$@"
                ;;
            pr-checks)
                # GitLab: show pipeline status for MR N. `glab ci view` only
                # shows the *current branch's* pipeline, so resolve the MR's
                # source branch first (issue #316).
                mr_num="$1"
                shift
                mr_branch=$(glab mr view "${mr_num}" --output json 2> /dev/null | jq -r '.source_branch // empty')
                if [[ -n "${mr_branch}" ]]; then
                    glab ci status --branch "${mr_branch}" "$@" 2> /dev/null ||
                        glab mr view "${mr_num}" --web
                else
                    glab mr view "${mr_num}" --web
                fi
                ;;
            pr-merge)
                glab mr merge "$@"
                ;;
            pr-close)
                # GitLab's `mr close` has no --comment flag (unlike `gh pr close`);
                # translate by posting a note first, then closing.
                _translate_pr_close_flags() {
                    local mr_num="$1"
                    shift
                    local -a mr_args=("${mr_num}")
                    local comment=""
                    while [[ $# -gt 0 ]]; do
                        case "$1" in
                            --comment)
                                comment="$2"
                                shift 2
                                ;;
                            --comment=*)
                                comment="${1#--comment=}"
                                shift
                                ;;
                            *)
                                mr_args+=("$1")
                                shift
                                ;;
                        esac
                    done
                    [[ -z "${comment}" ]] || glab mr note "${mr_num}" --message "${comment}"
                    glab mr close "${mr_args[@]}" # array-safe: seeded with mr_num
                }
                _translate_pr_close_flags "$@"
                ;;
            pr-reopen)
                glab mr reopen "$@"
                ;;
            pr-update-branch)
                # GitLab has no direct "merge base into branch" endpoint; the closest
                # equivalent is a server-side rebase (semantically rebase, not merge —
                # note the difference to callers expecting github's merge behavior).
                mr_num="$1"
                shift
                glab api "projects/:id/merge_requests/${mr_num}/rebase" -X PUT "$@"
                ;;
            pr-comment)
                issue_comment_args --message "$@"
                glab mr note "${ISSUE_COMMENT_ARGS[@]+"${ISSUE_COMMENT_ARGS[@]}"}"
                ;;
            pr-comments)
                # The Notes API cannot return inline diff comments at all
                # (docs.gitlab.com/api/notes/: "Notes are not attached to
                # specific lines... see the discussions API"); only the
                # Discussions API exposes each note's diff `position`. Flatten
                # every discussion's notes, keep only inline (position != null)
                # non-system notes — matching github's pulls/.../comments
                # inline-only semantics — and prefer new_path/new_line (the
                # current/new version of the file) over old_path/old_line.
                mr_num="$1"
                shift
                glab api "projects/:id/merge_requests/${mr_num}/discussions" \
                    --jq '[.[].notes[] | select(.system == false) | select(.position != null) | {id, author: .author.username, path: .position.new_path, line: .position.new_line, body}]' "$@"
                ;;
            repo-admin-check)
                # GitLab has no single "admin" boolean; approximate with
                # Maintainer(40)+ access, the closest analogue to github's
                # repo-admin permission for merge/branch-protection bypass.
                glab api "projects/:id" \
                    --jq '((.permissions.project_access.access_level // 0) >= 40) or ((.permissions.group_access.access_level // 0) >= 40)' \
                    "$@" 2> /dev/null || echo false
                ;;
            branch-protection)
                # github-only: github's enforce_admins/required_signatures/merge_queue
                # trio has no unified GitLab equivalent — GitLab models admin bypass via
                # separate push/merge access levels and a distinct signed-commit push
                # rule, not a single protection resource. Fail loud rather than fabricate
                # a false equivalence.
                err "branch-protection has no GitLab equivalent (github-only verb)"
                exit 1
                ;;
            commit-checks)
                sha="$1"
                shift
                glab api "projects/:id/repository/commits/${sha}/statuses" --jq '[.[].status]' "$@"
                ;;
            release-create)
                glab release create "$@"
                ;;
            release-list)
                glab release list "$@"
                ;;
            label-create)
                glab label create "$@"
                ;;
            label-delete)
                glab label delete "$@"
                ;;
            label-list)
                glab label list "$@"
                ;;
            label-sync)
                bash "${SCRIPT_DIR}/label_sync.sh" "$@"
                ;;
            *)
                err "Unknown subcommand: ${subcommand}"
                exit 1
                ;;
        esac
        ;;

    git)
        # Plain git remote - no issue tracker available
        case "${subcommand}" in
            issue-* | pr-* | release-* | label-*)
                warn_no_tracker
                ;;
            *)
                err "Unknown subcommand: ${subcommand}"
                exit 1
                ;;
        esac
        ;;

    *)
        err "Unknown platform: ${platform}"
        exit 1
        ;;
esac

exit 0
