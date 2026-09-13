---
name: ci-audit-triggers
description: "Audit a GitHub Actions/GitLab CI workflow for attacker-influenceable triggers (pull_request_target, issue_comment, workflow_run) — pwn-request issues: fork head-ref checkout, ${{ }} injection, author_association gaps. Analysis-only; harden via ci-harden-workflow."
---
# Security-Review a CI Workflow on Untrusted Triggers

Generic source→sink review (`security-review-diff`) and `mcp-audit` miss CI-specific
escalations: the sink is a runner holding the **base repo's secrets**, the substitution happens in the
runner's own `${{ }}`/shell semantics, and the attacker controls a fork PR's code or a comment's text while a
privileged trigger runs in base-repo context. Apply this whenever a `.github/workflows/*.yml` (or
`.gitlab-ci.yml`) fires on events an outsider can influence, or exposes `secrets.*`. To *build or lock down*
such a workflow (CODEOWNERS, branch protection, environments), use `ci-harden-workflow`.

### Step 0: Detect platform

Run `../../runtime/bin/ci_platform.sh` relative to this skill directory. The shared audit method below (classify
trigger trust → enumerate attacker-controlled inputs → trace the ref/code the job
operates on → hunt injection → audit secret reach → check cheap hardening) applies on
either platform — only the vocabulary changes:

- `github-actions` → the trigger/variable/injection vocabulary in steps 1-9 below
  applies as written.
- `gitlab-ci` → load `../../runtime/references/ci/gitlab-ci-triggers.md` for the real GitLab
  equivalents (pipelines for merge requests from forks, `CI_MERGE_REQUEST_*`
  variables, `$[[ inputs.* ]]` interpolation, protected variables/branches in place of
  `author_association`) and apply the same method through that vocabulary instead.
- `none` → report that no CI configuration was detected and stop; don't guess at a
  platform or improvise generic advice.

1. **Classify the trigger's trust level.** `pull_request_target`, `issue_comment`, `issues`, `workflow_run`,
   and `discussion_comment` run with the **base repo's secrets and a writable token**, and are reachable by
   untrusted actors (any commenter, any fork-PR author). Plain `pull_request` from a fork runs **without**
   secrets and with a read-only token *by default* (a repo can opt to send secrets/write tokens to fork PRs;
   same-repo `pull_request` does get them). State which untrusted inputs the chosen trigger exposes; flag
   every secret-using job on the privileged triggers.
2. **Enumerate attacker-controlled inputs.** Treat each as hostile: `github.event.comment.body`,
   `issue.title`/`issue.body`, `pull_request.title`/`body`/`head.ref`/`head.label`/`head.sha`, and
   branch/tag names.
3. **Check what the `if:` gate actually authenticates.** `author_association ∈ {OWNER, MEMBER, COLLABORATOR}`
   is server-set and non-spoofable, but it has two gaps: (a) it gates the **commenter/actor — not the code/PR
   author** (a trusted maintainer commenting on an outsider's fork PR still passes the gate while the job
   points at fork-controlled content); and (b) it is **not a write-access check** — `COLLABORATOR` includes
   read-/triage-only collaborators and `MEMBER` is any org member, so a gate on this set can admit principals
   with no write access. For a privileged trigger, confirm the real permission level (e.g. `gh api
   repos/{owner}/{repo}/collaborators/{user}/permission` → `admin`/`write`) rather than `author_association`
   alone. Verify the gate covers the principal whose *code or data* the job consumes, not just who typed the
   trigger phrase.
4. **Trace the ref/code the job operates on (pwn-request).** Look for a checkout or operation on
   `pull_request.head.ref`/`head.sha`. If a secret-bearing step (deploy, publish, an agent CLI with an API
   key, `gh` with a PAT, `npm install` running fork postinstall) acts on that fork-controlled tree (Makefile,
   postinstall, git filters, lint/CI configs) → **pwn-request, high severity**: attacker code executes with
   secrets in scope. Require `head.repo.full_name == base.repo.full_name` (or explicitly refuse fork PRs)
   before any checkout/operate step.
5. **Hunt `${{ }}` expression injection.** Any `github.event.*` value interpolated directly into a `run:`
   block or a `with:`/`github-script` input is substituted into the YAML/shell **before** execution; a `"`,
   backtick, `$(...)`, or newline breaks quoting and can bleed into adjacent keys or execute commands. The fix
   is the distinction that matters: **bind the value to `env:` and reference the quoted `"$VAR"` inside
   `run:`** — once it arrives through the environment the `${{ }}` expansion never touches the script text, so
   it is data, not code (for `with:`/`github-script` inputs, read it from `process.env`/`core.getInput`, don't
   inline `${{ }}`). Flag the inline interpolation, not the env-bound passing. **Caveat — a `with:` input is
   only data if the *receiving* action doesn't shell-template it.** A composite action can re-introduce the
   injection by inline-templating an input into its own `run:` (e.g. `jq --arg x "${{ inputs.x }}"`), so an
   attacker-controlled value you hand off via `with:` (e.g. a fork PR's `head.ref` as a `starting_branch`
   input) still detonates inside the action. Fetch the action's metadata at the pinned SHA and read its
   `runs:` steps — the file is `action.yml` (GitHub's preferred name) or `action.yaml`, and a subdirectory
   action (`owner/repo/path@sha`) keeps it under that path, so derive the path from `uses:` and try both
   names: `gh api repos/OWNER/REPO/contents/PATH/action.yml?ref=SHA --jq .content | base64 -d` (retry with
   `action.yaml` on 404). Confirm each untrusted input it receives is env-bound, not interpolated —
   SHA-pinning fixes the version, not this. When in doubt, sanitize the value (strict allowlist, e.g. a ref
   against `^[A-Za-z0-9._/-]+$`) before the hand-off.
6. **Audit `permissions:` and secret reach.** Demand least privilege (`contents: read` unless writes are
   needed); identify exactly which steps see `secrets.*`. A secret-bearing step gated only on untrusted input
   is the escalation target — narrow the gate or move the secret behind a manual approval / GitHub Environment.
7. **Check the cheap hardening.** Trigger `types: [created]` only (no `edited` replay of an approved-looking
   comment), bot exclusion (`github.event.comment.user.type != 'Bot'` to stop self-trigger loops), and
   third-party actions pinned to a **full commit SHA**, not a mutable tag.
8. **Prefer the structural remedy over a smarter gate.** Either refuse fork PRs before any secret is exposed
   (`head.repo.fork == true` → fail), or **split into two jobs**: an unprivileged job that builds/tests the
   fork code with no secrets, and a privileged job (triggered via `workflow_run` or gated on same-repo) that
   never checks out fork code. Don't rely on the author gate alone.
9. **Report only findings with a concrete attacker path.** Rank pwn-request and secret-exfil as high; rank
   pure expression-injection without a secret/command sink as medium. For each, name the trigger, the
   untrusted input, and the sink it reaches.

## Sub-agent dispatch

Follow the [CI audit dispatch rules](references/ci-audit-triggers-dispatch.md). Use the
pinned `sonnet` model. For three or more workflow files, audit one workflow per
review unit and merge the findings. Below that threshold, audit inline. If
structured output is unavailable, perform the same review inline and report
`DEGRADED`.

When dispatching three or more workflows, invoke
`manifest-workspace:parallel-agent --analyze <workflow> --validate --json` once
per workflow and merge its structured findings. Dispatched reviewers do not
re-dispatch.
