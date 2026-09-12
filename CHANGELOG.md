# Changelog

## Unreleased

- Add reviewed Linux toolchain attestation output and align local
  markdownlint-cli2 policy with the CI action's bundled 0.23.2 engine.
- Make refactor and security review fan-out risk-based: one reviewer is now the
  default, with independent review reserved for consequential or uncertain changes.
- Make Context7 authentication persistent across harnesses: device OAuth now
  stores one long-lived API key, Manifest writes private bearer-authenticated
  `/mcp` entries, and deploys preserve existing MCP credentials.
- Reconcile the complete Codex plugin marketplace before retiring flat skills.
- Add the pinned, cross-harness `manifest-i-have-adhd` plugin and reversible upstream migration.
- Add portable skill model chains, bounded failure classification, explicit fallback authorization, and `manifest skill-run`.

> Version history for the Manifest parallel agent orchestration framework

**Last Updated**: 2026-07-26

All notable changes are documented here in reverse chronological order.

---

## [Unreleased]

### Retired capabilities

- `manifest-workspace`: `workspace-context-budget-reminder` — the advisory
  `hooks/manifest-hooks.json` catalog invented a `context-budget` event with
  no Claude Code or Codex hook surface, no command handler, and no executable
  `session-checkpoint` hook script (`configs/claude/prompts/context_monitor.md`
  states outright that no automatic trigger exists); it never fired. Deleted
  rather than rewritten to the real schema — reinstating it is a separate
  spec. See `docs/superpowers/specs/2026-08-19-marketplace-restructure-design.md`
  §4 Phase 1 item 1.3.
- `manifest-workspace`: `workspace-learning-advisory` — the same catalog
  invented a `task-completed` event routed to `learning-capture`. Claude Code
  requires `{"hooks": {"<PostToolUse|Stop|SessionStart|…>": [...]}}`; the flat
  list here never loaded there, and Codex independently rejected it at parse
  time ("invalid type: map, expected a sequence"). Deleted alongside
  `workspace-context-budget-reminder`; reinstating it is a separate spec.

### Delegation gains Cursor and Devin backends

`plugins/manifest-delegate/config/backends.json` registers `cursor` (alias
`cursor-agent`) and `devin`, so `/delegate` and `/delegate-setup` can dispatch
to them like any other backend. No dispatcher code changed — FR-016's
entry-only extension contract held — and each entry ships the prompting
reference it names (`prompting-cursor.md`, `prompting-devin.md`).

Both profiles were measured against the real CLIs rather than inferred from
docs. Cursor runs `-p --output-format json`, reading the prompt from stdin and
returning `result` + `session_id`, so resume works; it also refuses to start in
an untrusted directory and cannot answer that prompt non-interactively, so the
invocation carries `--trust` — which answers the trust gate only, leaving
`--sandbox enabled` on in both modes and never using `--yolo`/`--force`. Devin
runs `devin -p <prompt>` over the bounded argv transport with
`--permission-mode auto --sandbox` (read-only) or `accept-edits` (write); its
print mode emits no session id and leaves no entry in `devin list`, so `resume`
is `null` and the dispatcher discloses that rather than faking a handle.

Validating the entries also caught pre-existing contract drift: nothing checked
`backends.json` against the `backend-registry.schema.json` it declares in
`$schema`, so `response_capture` — implemented and shipped on the claude entry
— had never been added to the contract. The schema now documents it, and a new
test validates every shipped entry against it, alongside gates asserting that
each `prompting_ref` resolves, each `services_key` is one bootstrap writes, and
no entry declares a resume template without a way to capture the session id.

### New plugin: adversarial-design-loop, and the repo becomes a plugin marketplace

`plugins/adversarial-design-loop/` codifies the Lumient One design-pass
process as a reusable Claude Code plugin: an orchestrating `design-loop`
skill plus five phase skills (scaffold, colorless screen prompts, faithful
render gate, adversarial review rounds, spec amendments), two sonnet-pinned
agents (`design-lens-reviewer`, `skeptic-verifier`), and a generalized
`render_and_scan.py` (fonts-ready hard-fail capture + per-ink radial scan)
with unit tests under `tests/python/`. A root `.claude-plugin/marketplace.json`
registers the repo as a directory marketplace named `manifest`, mirroring the
part-forge convention. Plugins are installed via `claude plugin marketplace
add` or `--plugin-dir`; bootstrap does not deploy them.

### The cwd guard resolves relative deletion targets against the directory the command is actually in

`#671` exempted a `cd` argument from being read as a deletion *target* — correct,
and it killed a large class of false denials. But that argument is also the
directory a later relative target resolves against, and only the first half
landed: `.` was still resolved against the hook payload's cwd, a directory the
command had already left.

Because a session's own cwd is always among the holders, the effect was that
**every** `cd <anywhere> && rm -rf .` denied and named the session's own cwd:

- A scratch cleanup — `cd /tmp/scratch && rm -rf .`, the shape that follows every
  `mktemp -d` — was denied outright.
- A genuine cross-session deletion — `cd <other session> && rm -rf .` — denied,
  but blamed the actor's own cwd.

The second is the dangerous one. The sanctioned response to a denial is
`# cwd-verified`, and an operator who can see the named directory is wrong will
reach for it — suppressing the check for the whole command, including the real
target. A guard that mis-attributes is worse than one that stays quiet.

- `_cd_destination()` reads a leading `cd`/`pushd`/`chdir` clause and advances the
  base that `deletion_targets()` resolves against; the base persists into later
  clauses. An argument that cannot be resolved (absent, an option, an unexpanded
  `$VAR`) leaves the base alone rather than guessing.
- `mentioned_paths()` is unchanged — a `cd` argument is still not a target.

Six bats tests cover both directions, and mutation-verified in both: removing the
base advance fails the scratch-dir false positive and the
denial-names-the-right-directory assertion; removing the unresolvable-token guard
fails the `cd $UNSET` case. The pre-existing 26 tests still pass — the four that
already denied these shapes were passing for the wrong reason, which is why the
new attribution assertion, not the verdict, is the load-bearing one.

### Adding a hook no longer changes who can read a config, or skips a tool when another one fails

Two defects in the `#671` hardening, found by review of the same change:

- `save_json()` wrote through `mkstemp` + `os.replace`, which swaps inodes — so a
  `0644` config silently became `0600` while the `copy2` backup kept `0644`,
  leaving the `.bak` **more** permissive than the live file. The prior mode is now
  carried onto the replacement; a newly created config gets the mode an ordinary
  `open()` would have produced instead of mkstemp's private `0600`.
- `install_all.py`'s `run()` used a bare `check=True`. Now that `merge_hooks`
  correctly exits non-zero on a config it cannot parse, that aborted the install
  partway: whether Cursor got its hook depended on whether Gemini's file happened
  to be valid, and the child's actionable message was buried under a
  `CalledProcessError` traceback. All four targets are now attempted and the run
  fails once, naming each that failed.

Six new unit tests, mutation-verified.

### Model pins re-verified against live provider CLIs, and tiers established for cursor/devin

Every `model_tiers` pin was re-checked by a **real one-shot call through the
provider's own CLI** on 2026-07-29, not against documentation. Each pin now
carries a VERIFIED or UNVERIFIED status inline, because the two are not
interchangeable — doc-sourced IDs are what produced this repo's 404ing Gemini
tiers.

- **claude** — `opus` was a generation stale at `claude-opus-4-8`; now
  `claude-opus-5`. `haiku` drops its needless date suffix
  (`claude-haiku-4-5-20251001` → `claude-haiku-4-5`), matching the canonical name
  the API itself reports. All four pins verified via `--output-format json`'s
  `modelUsage.canonicalModel`, which echoed the pinned string exactly. Full IDs
  are pinned rather than the `opus`/`sonnet` aliases: an alias is a moving target
  the provider can remap, which would change a tier with no diff here.
- **cursor** — all three tiers were the literal string `"auto"`, which made the
  tier abstraction inert (every role resolved to the same model) while
  `model_check.sh` reported `OK`, because `auto` is genuinely in the listing — a
  green check on a placeholder. Now a verified grok-4.5 effort ladder
  (`low`/`medium`/`high`). The newer premium ladder
  (`claude-opus-5-thinking-*`, `claude-fable-5-thinking-*`, `gpt-5.6-sol-*`,
  `kimi-k3-high`) is deliberately **not** pinned: all of it returned an account
  usage-limit `ActionRequiredError` (resets 2026-08-12), making it unverifiable
  rather than broken.
- **antigravity** — format migration. `agy models` emits **slugs** under 1.1.8
  (`gemini-3.6-flash-low`) where 1.1.1 emitted display labels
  (`Gemini 3.5 Flash (Low)`). agy still *accepts* the labels — re-probed, they
  answer — so the old pins were never broken at runtime; they had silently stopped
  matching the catalog, so `model_check.sh` scored all three STALE for a purely
  cosmetic reason. `mini`/`flash` also move up to the 3.6 flash family now that it
  exists; `advanced` is the same model in slug form, so the `spec_review.sh` agy
  reviewer is unchanged.
- **gemini — UNVERIFIED, and the CLI is non-functional.** Every invocation now
  fails at the eligibility layer, before model selection: `IneligibleTierError`,
  free-tier Gemini Code Assist for individuals is discontinued, "migrate to the
  Antigravity suite". With no API key set the REST listing is unavailable too, so
  neither pin can be confirmed. Left in place so tier lookups resolve, marked
  unproven, with Antigravity documented as Google's own stated remedy.
- **codex — UNVERIFIED.** `codex login status` reports "Not logged in" and probes
  return HTTP 401. Re-confirmed the CLI still has no listing command.

`model_check.sh` gained the verification paths those gaps exposed:

- **devin is now reported instead of silently absent.** It had no check line at
  all, and a provider missing from the report reads as "checked and fine" in the
  `check_status.sh` summary. Reported as `SKIPPED` rather than a new label,
  because `check_status.sh` cases on exactly OK/STALE/SKIPPED/UNSUPPORTED and
  would drop a fifth word uncounted — turning an unchecked provider green.
- **devin is deliberately never probed.** While logged out, `devin --model X -p`
  does not fail — it *launches an interactive login* ("Welcome to Devin CLI!" →
  "Error: Login canceled"). A health check must never try to log the operator in.
- **cursor and codex gained probe shapes.** Codex has no listing command, so a
  probe is the *only* way to verify its pins. The cursor probe runs inside a
  throwaway temp dir: `cursor-agent` aborts with "Workspace Trust Required" and
  needs `--trust`, and trusting whatever directory the operator ran `/env-check`
  from is not this script's call. Neither probe passes `--full-auto` or
  `--permission-mode auto` — a staleness check must not be able to execute code.
- **A dead cursor pin now reports STALE instead of SKIPPED.** cursor-agent says
  `Cannot use this model: X`, which the classifier didn't recognise, so a broken
  pin hid behind the same "couldn't check" label as a transient auth failure.
- Sourcing the script no longer trips `set -u` on an unset `BASH_SOURCE`.

Also: `test_get_dot_notation` asserted a hardcoded haiku model ID, so it failed on
every model refresh for a reason unrelated to what it covers. It now asserts the
lookup mechanism (verified by mutation: breaking `get()` still fails it).

### Two audit findings fixed: config wipe on unparseable JSON, and a guard that cried wolf

**An unparseable `settings.json` was silently replaced with only the hook block.**
Measured before the fix: a file holding `model`, `statusLine`, `env`, `permissions`
and a user `PreToolUse` hook, with one trailing comma, went 309 bytes → 256 bytes
on a `merge_hooks.py` run — exit 0, one stderr warning, no backup. Valid JSON that
was not an object (`["a","b"]`) took the same path with *no warning at all*.
`load_json()` collapsed absent, unreadable, and not-an-object into `{}`, and
callers read → insert → rewrite the whole file, so "empty" meant "truncate".
Blast radius was three files per run (`~/.claude/settings.json`,
`~/.gemini/settings.json`, `~/.cursor/hooks.json`), and `remove_hooks.py` shared
the same pair, so uninstall wiped identically. `check=True` could not catch it,
because the wipe exits 0.

- `load_json` returns `{}` only for an absent or empty file, and raises
  `ConfigUnreadable` otherwise; both callers abort with the file intact.
- `save_json` writes atomically (same-directory temp + `os.replace`) after
  copying prior content to a sibling `.bak`, so an interrupted write can no
  longer truncate a config and a bad merge is undoable. Symlinked configs are
  updated at their target rather than replaced by a regular file.

**`block_cwd_delete` denied commands that only *mentioned* a deletion verb.** Its
loose scan treated a `cd` argument as a deletion target, so any command that
cd'd into a live session's cwd — the shape agent shells emit constantly — was
denied if a deletion verb appeared anywhere in it, including inside a `grep`
search pattern, a comment, or an echo string. The docstring already stated the
rule ("a `cd` argument … must not be treated as a deletion target"), but
restricting loose matching to equality implemented it only for *ancestors*. The
cost was not merely friction: the sanctioned workaround is appending
`# cwd-verified`, so a guard that cries wolf on read-only greps trains the
operator to disarm it by reflex.

- `mentioned_paths` drops the argument of `cd`/`pushd`/`chdir`. `deletion_targets`
  is untouched, so `cd /x && rm -rf <live cwd>` still denies, as does the
  original sweep-loop incident shape.
- Closes a separate pre-existing hole: `rm -rf .` / `rm -rf ..` were discarded as
  non-path-shaped and slipped past **both** passes. They now resolve against the
  cwd — but only when the clause is *led* by a deletion command, since
  `grep -r rmdir .` contains both a deletion verb and a dot and must stay allowed.

Both fixes are mutation-verified in both directions.

### `manifest` CLI hardened against install, deletion, and drift edge cases

- **The wrapper no longer gates on `uv`, which was a live outage.** Measured on a
  healthy install: `env PATH=/usr/bin:/bin manifest doctor` → `manifest: uv not
  found`, exit 1. `check_uv()` installs uv to `/opt/homebrew/bin` (macOS) and
  `~/.local/bin/uv` exists only on the portable-installer path, so every
  launchd/cron/minimal-PATH caller refused to run against a runtime that worked —
  while the wrapper only ever `exec`s the venv console script and never invokes
  uv. uv absence is now a `manifest doctor` **warning** (needed to re-sync, not to
  run). Checks are ordered by what the exec path needs, so the first failure names
  the real cause instead of blaming uv.
- **Present-but-unusable runtimes are diagnosed instead of exec'd.** The wrapper
  distinguishes: `~/.claude` deleted · venv deleted · console script missing
  (interrupted sync) · script not executable · script truncated to zero bytes
  (ENOEXEC made bash reinterpret it as a shell script) · shebang interpreter gone
  (Python upgraded away, or the tree copied from another machine/home — previously
  `bad interpreter: … Undefined error: 0`) · `exec` failing outright (wrong
  architecture after a machine migration, `noexec` mount), via `shopt -s
  execfail`. `HOME` unset no longer aborts with `HOME: unbound variable`; it
  resolves through the passwd entry, and `MANIFEST_HOME` relocates the root.
- **Remediation names the clone**: `re-run /path/to/Manifest/bootstrap.sh`, read
  from `$MANIFEST_STATE_ROOT/runtime.env` — written outside `~/.claude` so it
  survives that tree being deleted — falling back to `config/deploy_stamp`.
- **Wrapper install is collision-safe and idempotent.** `install_manifest_wrapper()`
  replaces a plain `cp` that wrote *through* a symlink at `~/.local/bin/manifest`
  (silently overwriting whatever it pointed at) and clobbered a same-named CLI
  from another project. Now: symlinks are replaced not followed, a foreign file is
  backed up to `manifest.pre-manifest.bak` (existing backups are never
  overwritten), a directory at the destination is refused with a message, writes
  go to a temp file and are renamed then re-verified with `cmp` (a short write can
  no longer become the wrapper), an unchanged wrapper reports "already current"
  and only heals a lost `+x` bit, and an unwritable `~/.local/bin` reports instead
  of aborting the bootstrap under `set -e`.
- **`uv sync` no longer runs against an incomplete deploy**: missing
  `pyproject.toml`/`uv.lock` is reported as such rather than surfacing as a raw uv
  error. A venv whose interpreter no longer runs is removed so uv rebuilds it, and
  the playwright/browser step is skipped with a message when the smoke group did
  not land.
- **Optional dependency groups can no longer be dropped silently.** The three
  `python3 -c "import yaml…" | grep -q 1` probes made a probe failure (host
  `python3` without PyYAML) indistinguishable from "service disabled": an enabled
  smoke/browser-use/claude service got no deps and failed later, inside the
  runtime. One probe now prefers the runtime's own interpreter, and an
  unresolvable `services.yml` warns and reuses the previous sync's groups from the
  runtime stamp instead of downgrading to core.
- **`browser_use.enabled` now also installs the Chromium binary.** It pulls in the
  `smoke` group (playwright the package) but left `install_playwright` false, so a
  browser-use-only install had the driver and no browser. The two toggles now fold
  into one deduplicated group list, which is also what lands in the runtime stamp.
- **Scripts that shell out to `manifest`** (`skillclaw_promote.sh`,
  `verification_gate.sh`, `lifecycle.sh`, `spec_review.sh`) put `~/.local/bin` back
  on PATH first: their command seams are word-split strings, so prepending the
  directory is safe where hardcoding a path would break on a `$HOME` with spaces.
  The block uses `${HOME:-}` — a bare `$HOME` above a `--help` path aborts under
  `set -u` in a clean env, which `spec_review_mode.bats` catches and
  `PATH=/usr/bin:/bin` does not (it still carries `HOME`). `manifest_wrapper.bats`
  now gates the idiom at source level so the unguarded form cannot return.
- **`manifest --version`** (previously exit 2, `No such option`) reports runtime
  version, interpreter, root, and deploy sha/dirty flag — the drift-detection
  surface `env-check` lacked.
- **A missing module is one line, not a traceback**: optional groups name their
  toggle (`--enable-smoke`, `--enable-browser-use`, `--enable-claude`) and a
  missing core module reports an incomplete runtime.
- **`manifest doctor` stopped false-greening.** A missing `services.yml` read as
  "all services disabled", so a half-deleted tree passed; malformed YAML and a
  non-mapping document raised tracebacks; the sole core check (`yaml`) was vacuous
  because the module imported it at module scope. Doctor now sweeps every core
  module, validates `services.yml` shape, and audits install integrity —
  pyproject/uv.lock present, venv interpreter and console script present, wrapper
  installed/executable/unshadowed/undrifted, uv availability, deploy provenance —
  with an explicit failure-vs-warning split, `--json` output, and an announcement
  when `--services` puts the install audit out of scope rather than skipping it
  silently.

### Task-completion audit of the shipped manifest CLI — two gaps closed

A `/spec-audit-tasks` pass over `docs/superpowers/plans/2026-07-13-manifest-uv-cli.md`
verified all 11 tasks against the tree (the plan's 50 step boxes had never been ticked, so
nothing rested on them) and found two real gaps:

- **`deploy_reconcile.sh` contradicted the spec's "no fail-open to system `python3`".** Line 91
  falls back to `python3` when the venv is absent. Resolved by scoping the constraint rather
  than failing closed, because failing closed was the wrong fix: `bootstrap.sh` runs
  `reconcile_deploy_report` (line 293) *before* `uv_sync_home_runtime` (line 297), so on a first
  bootstrap the venv does not exist yet and the orphan review would vanish from every fresh
  install. It is also safe — `reconcile_core.py` is deliberately dependency-free (`yaml` is a
  lazy import behind a PyYAML-free fallback parser; inline snippets use `json`/`os`/`sys`), so
  there is no third-party resolution that could differ between interpreters. The exception is
  now **enforced**: `test_reconcile_core_has_no_hard_third_party_imports` fails if a hard
  dependency is added, at which point the fallback must go. The old code comment claimed the
  module "only needs PyYAML, which python3 provides" — wrong on both halves, and the same false
  premise that made the CLI's uv gate look reasonable.
- **Six skill sites still routed through the deprecated `parallel_agent.py` shim** and would
  have broken at the planned "Release N+1: delete shims": three copy-paste commands
  (`issue-triage/references/workflow.md` ×2, `issue-prioritize/references/workflow.md`) and
  three dispatch-guidance mentions (`issue-triage/SKILL.md` ×2, `issue-prioritize/SKILL.md`),
  all repointed to `manifest parallel-agent`. Seven other references are correct as written and
  left alone: a historical-failure lesson quoted verbatim (`test-isolate-ambient`), an explicit
  "this shim is deprecated" note (`smoke-manage`), a prohibition (`spec-implement-loop`), and
  provider-role prose (`config-audit`, `env-check`).
- **Traceability fix for why the first gap of this release survived one:** the design's
  error-handling row for an uninstalled optional group had no entry in the plan's spec↔task
  table, so no audit that trusted that table could see it was never built. The row is now in
  the table, alongside a note to add design error-handling rows to it in the same change.
- Plan marked **COMPLETED** with per-task evidence instead of 50 bulk-ticked boxes — several
  are process steps ("Run tests — expect FAIL") whose occurrence cannot be verified after the
  fact, and ticking them would assert knowledge nobody has. The stale
  "wrapper (checks uv + venv)" constraint is struck with a pointer to the revision that
  removed it.

- Coverage: `tests/bats/manifest_wrapper.bats` 1 → 16 cases,
  `tests/bats/uv_sync_home_runtime.bats` 8 → 25,
  `tests/python/manifest_cli/` 21 → 58. Shim tests now build a real runtime tree
  under `MANIFEST_HOME` instead of monkeypatching `os.path` internals. Each new
  guard was mutation-verified: reinstating the uv gate, dropping the shebang
  check, restoring the plain-`cp` install, removing the pyproject precheck,
  restoring doctor's missing-file default, and removing the import guard each fail
  exactly their own tests.

### Devin CLI support (6th parallel agent, opt-in)

- **`agent_roster.yml`** — adds `devin` (binary `devin`, home `~/.config/devin`,
  `enabled_default: false`) plus a new `skills_sync` field on every agent.
  `parallel_agent.yml` / `agents/config.py` gain matching rate limits, a
  `cli_agents.devin` command shape (`devin --permission-mode auto -p <prompt>`),
  and a synthesis `provider_order` slot; `--devin-only`, `--no-devin`, and
  `--devin-model` are generated from the roster.
- **Bootstrap** — `--enable-devin` / `--disable-devin`, `check_devin` (Homebrew
  cask, curl installer fallback), `check_devin_auth`, a `devin:` block in
  `services.yml`, and `deploy_devin_config`, which merges — never overwrites —
  `~/.config/devin/config.json`.
- **Skills, rules, and MCP servers are inherited, not copied.** Measured against
  devin 3000.2.17 (2026-07-29): `devin skills list` already resolves every
  `~/.claude/skills/<name>/SKILL.md`, and returns none of them when
  `read_config_from.claude` is false — so deploy pins that one key and ships
  nothing else. A second copy under `~/.config/devin/skills` would register each
  skill TWICE (`/devin:<name>` beside `/claude:<name>`), which is why the roster
  marks devin `skills_sync: false` and `sync-skills.sh` honors it. MCP is the
  same story: `devin mcp list` returned 11 servers on a Manifest-configured home
  and 3 with `read_config_from.cursor` false — the other 8 are the ones
  `--install-mcp` writes to `~/.cursor/mcp.json` — so no `install_devin_mcp_server`
  exists by design.
- **Three measured traps, encoded rather than documented away**:
  `devin auth status` prints "Not logged in." and still exits 0, so the auth
  probe is `devin models list` (which also spends no tokens, unlike the agy/codex
  probes); `~/.devin` is the Devin *Desktop* app's data folder, so devin is
  excluded from reconcile's fleet tags, which every consumer resolves to
  `$HOME/.<tag>`; and `ServiceConfig.is_enabled()` now falls back to the roster's
  `enabled_default` so a `services.yml` predating an agent cannot silently
  ENABLE it.
- **No model tiers on purpose** — `devin models list` is login-gated, so nothing
  is pinned (`--devin-model` defaults to `auto` = no `--model` flag, any other
  value passes through verbatim) and `credit_fallback.devin` is empty. Pinning
  names read off a docs page is how the gemini tiers once 404'd.
- **Why opt-in**: Devin is login-gated behind a paid account, and an
  unauthenticated agent does not abstain from the panel — it errors, dragging the
  consensus metric into a verdict that is not a finding.

### Bootstrap stopped failing on the skills domain it handed to apm

- **`verify_installation` counted three files bootstrap no longer writes.**
  SC-006 gated the `skills` domain, so `deploy_home_skills` stands down — but the
  verify step still listed `~/.cursor|.gemini|.codex/skills/code-audit/SKILL.md`
  among its own required files. A correct stand-down therefore exited 1 with
  three `Missing:` errors and no remediation. The checks stay (a home with no
  skills is genuinely broken) but degrade to warnings naming `apm-dev-sync` when
  apm owns the domain.
- **Nothing populated the domain.** `./bootstrap.sh` now runs the dev loop for an
  APM-owned skills domain **only when it is empty**, so a fresh machine whose
  registry already gates `skills` is no longer left with none. A populated domain
  is never touched — overwriting apm's published-tag deploy with a working tree
  on every run is the double-claim the registry exists to prevent.
- **`apm-dev-sync` diagnostics.** A wrong root now names *where the path came
  from* (`MANIFEST_ROOT` vs the enclosing checkout) and how to override it, and
  says so explicitly when the checkout predates the `.apm` migration — the old
  "is this a Manifest checkout?" was unanswerable on a machine with two clones.
  Its closing note about `bootstrap.sh`/`sync-skills` also writing the tree now
  reads the registry instead of hardcoding the migration's mid-state; post-gating
  it was false and pointed at commands that no longer refresh skills.
- `docs/DEPLOY_OWNERSHIP.md` said the APM pipeline was switched off and
  `apm_domains.yml` empty — untrue since 2026-07-28. Corrected, with the
  multi-clone `MANIFEST_ROOT` recipe and who-populates-what.

### Retired-tree references swept (`.retired skill supply/` → `.apm/`, `linear_triage.yml`)

- **Four skills carried paths into the tree deleted on 2026-07-27**, two of them
  as instructions that could not work: `pr-smoke/SKILL.md` handed out
  `.retired skill supply/skills/pr-smoke/scripts/run_pr_regression.sh` as a copy-paste
  command, and `spec-implement-loop/prompts/reviewer-dispatch.md` sent every
  critic sub-agent to read a verdict-format file at a path that no longer
  exists. Also fixed: `skill-evolve`'s always-loaded description (it advertised
  PRs into `.retired skill supply/skills/`), `graphify`, and `test-isolate-ambient` —
  whose isolation recipe stubbed `sync_retired skill supply_targets()`, a function removed
  with the tree, so the recipe silently no longer covered what it claimed.
- Same dead stub removed from `smoke-catalog/manifest.yaml`; stale comments in
  `bootstrap/lib/{common,deploy}.sh`, `.gitignore` and `.pre-commit-config.yaml`
  now name `.apm/skills`, and `deploy.sh`'s "home deploy is unaffected" note
  records that SC-006 has since handed that domain to apm.
- **`configs/claude/config/linear_triage.yml` deleted** — it was marked
  "deletion in Phase 2 cleanup", and Phase 2 closed. `tracker_triage.yml` is a
  strict superset (same thresholds; `same_team_boost` → `same_scope_boost`,
  `cancel_issue` → `close_issue`), and nothing in `tests/`, `bootstrap/` or
  `configs/claude/scripts/` ever read either file. References repointed in
  `issue-triage/README.md`, `env-check/SKILL.md`, `references/layout.md` and the
  root `CLAUDE.md` key-file table.

### Feature 522 status corrected (session-loaded facts)

- **`CLAUDE.md`** — the "Active Spec Kit Feature" block still said **"Nothing
  activated"** after SC-006 landed (#654). It now records what is live: apm owns
  `~/.claude/skills`, `deploy_home_skills`/`sync-skills` stand down, sibling
  homes inherit by symlink, and the tested undo is
  `apm_ungate_domain.sh skills --apply` + `./bootstrap.sh`. This block is
  always-loaded, so a stale fact there is the most expensive kind.
- **`specs/522-apm-deploy-migration/HANDOFF.md`** — superseded rather than
  rewritten: a status banner corrects the three claims that no longer hold
  (nothing activated; Phase 3 pending; the constitution blocker), the ⛔ section
  is marked resolved by constitution v3.0.0 (V.4 → detect-and-report), and the
  reasoning is kept because it is why the sequence was safe. The banner is
  explicit that Phase 3's **deletions were refused** — T028/T030 closed VOID, so
  the four generators and 109 `.mdc` files are retained on purpose and a future
  reader must not "finish" them.
- Loose ends reconciled: the `apm-spike-522` throwaway repo is gone, T017/T018
  landed together, and a new one is recorded — a retired skill is **not** pruned
  from an already-deployed home (`print-tune-bambu` survived #656 locally).

### Live-cwd deletion guard (`block_cwd_delete.py`)

- **`configs/claude/scripts/block_cwd_delete.py`** — PreToolUse:Bash hook that
  denies a directory removal whose target is, or contains, the working
  directory of any live Claude session. Registered in
  `settings.runtime.json` → `~/.claude/settings.json`, so it is armed in every
  repository. Override: append `# cwd-verified` to the command.
- **Why**: deleting a session's cwd breaks every later process spawn in it with
  `ENOENT ... posix_spawn '/bin/sh'` — the shell is present; the missing path is
  the *child's* working directory, which Node reports against the binary.
  Measured 2026-07-28: a `git worktree remove` sweep in one repo took out the
  live cwd of a session in another, because its exclusion list held only its own
  cwd. The pre-existing `block-cwd-delete` hookify rule could not fire there —
  hookify globs `.claude/hookify.*.local.md` relative to cwd, with no user-scope
  fallback.
- **Detection** is two-strength: literal arguments to a delete verb match on
  equality or containment; a bare path mention anywhere in a command containing
  a delete verb matches on equality only. The loose pass is what catches sweeps
  that pass `"$wt"` to the verb and keep their targets in a `for` list; the
  equality restriction keeps a `cd` argument from reading as a delete target.
- Fail-open by construction, and verified by observation in a fresh `claude -p`
  rather than by reading settings: a decoy session cwd was blocked, an ordinary
  directory still deleted.

### Doc Concision Contract (docs-* skills)

- **`configs/claude/scripts/docs_lint.py`** — per-type line caps for a docs set,
  read from `configs/claude/config/doc_limits.yml`. Exit 1 when a doc is over
  cap; fluff phrases are advisory only (a wording blocklist that fails a build
  is one people route around). `wc -l` parity, code blocks included.
- **Caps**: hub 120, root README 200, tutorial/how-to 200, explanation 250,
  reference 400, diagram page 300 (max 4 diagrams). Generated files, vendored
  trees, and dated records (specs, plans, reports, ADRs) are exempt — rewriting
  a record to fit a cap falsifies it. In-file `<!-- doc-type: -->` and
  `<!-- doc-limit: N — why -->` overrides; a limit override without a rationale
  is a hard failure, same contract as help-coverage exemptions.
- **`configs/claude/references/doc-concision.md`** — the fan-out rule (split by
  subject into a hub plus sub-pages, never `-part-2`; caps apply recursively),
  the fluff list, and the rewrite order (cut before you split). Indexed in the
  Claude and Cursor Reference Indexes.
- **All four docs-* skills** now measure before and after and report a
  line-count delta instead of asserting improvement. `docs-improve` trades its
  100-point health score for the linter's numbers; `docs-generate-diagrams`
  moves Mermaid syntax traps to a loaded-on-demand reference. Skill bodies:
  616 → 359 lines.
- **Policy follows the code**: the three skills that run the linter have `Bash`
  un-forbidden (scoped to `docs_lint.py`), and `docs-improve` moves
  `subagents: never` → `conditional` because its unit of work is now an
  independently-capped topic directory, not one holistic score.
- **Edit-time enforcement, not CI.** `lint_on_edit_hook.sh` now runs the cap
  check on `.md` writes, so a doc that crosses its cap says so in-session where
  the fix is one edit — rather than at merge time, when it is already written
  and reviewed. Opt-in per repo (only fires where a `doc_limits.yml` or
  `.doc-limits.yml` is present, so unrelated projects are never nagged),
  reports only when over cap, advisory as ever. `docs_lint.py` is deliberately
  NOT wired into CI: the changed-file gate would fail unrelated PRs on the 10
  pre-existing over-cap docs.
- **Tests**: `tests/bats/docs_lint.bats` (19 cases) pins classification, cap
  arithmetic, override rationale enforcement, exempt handling, and the
  `**`-vs-`*` glob distinction that `fnmatch` would collapse.
  `lint_on_edit_hook.bats` gains 6 covering opt-in, silence-when-clean,
  per-type classification, `.mdc` exclusion, and non-mutation.

### APM Publish Gates (feature 522, Phase 0 — T048–T050)

- **Supply-chain gates now precede any APM registry publish** — implemented
  before the first (throwaway spike) publish because publication is
  irreversible; they block T004 and are the one sanctioned FR-001 exception.
- **`apm_publish_gate.sh scan|provenance|all`** — blocking pre-publish
  content scan (repo gitleaks config + machine-local-path/private-material
  checks; gitleaks absent or erroring REJECTS, never degrades to regex-only)
  and a provenance gate (clean working tree at an exact tag). `all` appends
  one JSONL gate record per attempt — pass *or* fail — so SC-011 ("every
  publish has a preceding gate record") is auditable. Allowlist:
  `configs/claude/config/apm_publish_allowlist.txt`.
- **`apm_install_verify.sh verify TREE --ref REF`** — fail-closed package
  integrity verification on install: re-derives the canonical tree hash and
  compares it to the publish-time gate record, trusting nothing the `apm`
  binary claims about itself. Zero matching records, conflicting hashes, or
  an unreadable subject are all indeterminate → reject.
- **`apm_hash_lib.sh`** — single shared definition of the NUL-safe file walk,
  canonical tree hash, and gate-record contract constants, so the publish
  writer and install reader cannot silently drift apart.
- **Threat controls** opened in `specs/522-apm-deploy-migration/decision-record.md`
  (typosquatting, dependency confusion, registry-account compromise — each
  naming its enforcing mechanism, FR-018).
- **Tests**: 43 bats cases across two suites, including mutation-proven
  fail-closed behavior (blank-line allowlist fail-open, embedded-newline
  filename bypass) and a shared `tests/test_helper/git_fixture.bash`.

### Model-Routing Verification (class x model matrix)

- **`opus_attribution_report.py` is no longer Opus-only.** A hardcoded
  `if "opus" not in model: continue` meant no committed script could reproduce the
  baseline's own headline row (Fable 5 sub-agents: 4,531 requests, $919.32) — that
  figure came from ad-hoc analysis that was not kept, while the Reproduce section
  claimed otherwise. The filter is now `--models` (default `opus`, `all` to widen),
  and the report emits a **class x model matrix** with per-cell cost.
- **`--since <change-point> --models all` is the lever-verification query.**
  `subagent_policy.bats` T7/T8 prove `command_config.yml` *says* Sonnet; nothing
  proved a dispatch *ran* Sonnet. One command now answers it. First reading:
  283 sub-agent requests on Opus 5 in the change-point interval, so lever 1 is
  marked **declared, not landed** in `docs/MODEL-POLICY.md`.
- **Shared price table** `configs/claude/scripts/model_pricing.py` (`--json` to
  dump), used by both cost-reporting CLIs so their figures cannot diverge. An
  unknown model is reported **unpriced** and excluded from totals — never costed
  at $0. `token_cost_report.py` gained the per-model cost table that reproduces
  the baseline's $6,141.64 scope-correction figures.
- **Tests**: 7 new cases in `tests/python/test_measurement_reports.py` covering
  matrix cost math, unpriced-not-zero, `--models` filtering, and the
  cell-goes-to-zero verification query.

### Credit Measurement Baseline

- **Three measurement CLIs** in `configs/claude/scripts/` — `token_cost_report.py`,
  `skill_usage_report.py`, `opus_attribution_report.py`. All take `--since`/`--until`
  so a committed snapshot is reproducible against an append-only transcript corpus.
- **Corrected a 2.24x measurement error.** Prior credit figures counted JSONL *lines*,
  not API requests: Claude Code writes each content block of one response as its own
  `assistant` line and every sibling repeats the same `usage` object. Deduping by
  `requestId` (first value for input/cache fields, **max** for the cumulative
  `output_tokens`) puts the real total at 47,185 requests, not 105,728 — and Opus at
  16,873, not 41,527.
- **Dated baseline** in `docs/baselines/` with the Opus task-class attribution
  (98.04% classified) and a costed routing proposal. Headline findings: cache reads
  are 53% of Opus spend; per-turn model downgrades are net-**negative** (-$1,499)
  because caches are model-scoped; sub-agents are the only cache-neutral lever
  ($845, 13.8% of spend); Fable 5 is 39.9% of total spend on 18% of requests.
- **Sub-agent model-selection rule** documented in
  `configs/claude/references/sub-agent-dispatch.md`, including why the intuitive
  per-turn downgrade is rejected on evidence.

### Fixed

- **Duplicate PostToolUse hook.** `install_issue_hooks.sh` deduped by exact command
  string, so installing once from a repo clone and once from the deployed
  `~/.claude/scripts` copy registered the same hook twice and it fired twice on every
  matching tool call. Matching is now by script name.
- **Stale-clone drift was undetectable.** `deploy_stamp_check.sh` compared the clone
  against the deploy stamp, so a clone many commits *behind* its remote (stamp
  matching HEAD exactly) never warned. It now also checks the already-fetched
  remote-tracking ref — no `git fetch`, preserving the fail-open SessionStart design.
- **Git-invisible directories could deploy as skills.** A directory under
  `.retired skill supply/skills/` containing only ignored files (e.g. `__pycache__` left by a
  rename) is reported by git as ignored, never untracked, but `deploy_home_skills`
  rsyncs the filesystem. Directories without a `SKILL.md` are now warned about and
  excluded.
- **Five CLI defects** in the new measurement scripts, all found by probing and now
  covered by regression tests: an unparseable `--until` was silently ignored (leaving
  the scan unbounded at exit 0), a nonexistent `--root` returned a clean zero at exit 0,
  an empty result set raised a `ZeroDivisionError` traceback, an unwritable `--json`
  path raised a raw `FileNotFoundError`, and unbounded scan counters made committed
  snapshots drift on every regeneration.

### Added

- Exit-code and empty-result conventions for new Python CLI entry points in
  `docs/CODING_STANDARDS.md`; `--help` coverage for Python entry points in
  `tests/bats/help_coverage.bats`.

### Agent Frameworks Expansion

- **New Role-Agents** — Added 4 new high-precision role-agents with detailed operational
  execution rules, prompts, and validation criteria:
  - `context-chronicler`: Memory optimization utility with a strict JSON state checkpoint schema.
  - `compatibility-translator`: Cross-platform configuration sync engine (Cursor `.mdc`,
    Antigravity `agy`, Claude Code).
  - `performance-auditor`: Continuous CDDL critic verifying Big-O complexity, batching
    efficiency, and resource leak prevention.
  - `dependency-guardian`: Supply-chain security audit tool detecting typosquatting and
    restrictive licenses.
- **Auto-Sync & Parity** — Registered roles in the bootstrap configuration arrays, documented
  them in delegation policies, and regenerated all matching Cursor configurations automatically.

### specs/482 — Critic-Driven Development Loop (CDDL)

- **`/spec-implement-loop`** — sub-agent CDDL: developer writes; developer reviewer,
  QA critic, and architecture critic review until each approves with zero findings.
  Role prompts at `configs/claude/prompts/cddl/`.
- **CDDL sunset** — removed `manifest cddl` and the `cddl/` Python package; `cddl_loop.py`
  is a deprecation stub pointing at `/spec-implement-loop`.
- **Agent-agnostic synthesis** — low-consensus merge in `parallel-agent` uses any
  configured `cli_agents` provider (`synthesis.provider` / `SYNTH_PROVIDER` /
  `SYNTH_CLI`); default order prefers antigravity → cursor → gemini → codex → claude.
- **Cross-platform parity seams** — shared `agents/cli_invoke.py` for synthesis,
  `cddl_invoke.py` (CDDL critics on Gemini/Codex/Agy), and SkillClaw evolve
  (`EVOLVE_CLI` / `EVOLVE_PROVIDER`); `anthropic` moved to optional `uv --group claude`;
  Gemini hooks aligned with version-pin / spec-review / lint-on-edit; `/pr-smoke`
  orchestration probe tries the first available provider.
- **Shared infra** — `spec_review.sh` `discover_artifacts` now handles FILE
  targets (paired within their own layout tree); `audit_log.sh` gains a generic
  `AUDIT_LOG_FILE` env; deploy-reconcile now covers the `prompts/` namespace.

### specs/457 — Proactive Code Guardrails

- **Guardrail registry** — `knowledge_base.yml` seeded with 33 curated
  anti-pattern entries across 6 categories (`arch`, `async-state`,
  `error-handling`, `security`, `dependency`, `iteration`), each with severity,
  per-language detection cues, and a positive prevention rule; schema pinned by
  `knowledge_base_registry.bats`.
- **Write-time prevention** — "Proactive Coding Guardrails (always on)" digest
  in all deployed guides (budget-checked) with full detail in
  `references/antipatterns.md`; `code-quality` now flags registry anti-patterns
  inline as non-blocking advisory feedback.
- **`/ai-code-audit`** — dedicated seven-pass audit skill (inventory →
  architecture → async/state → security → logic → quality → iterative
  regression) with evidence-traced findings, adversarial cross-verification of
  critical/high candidates, and APPROVED/NEEDS_REVIEW/BLOCKED verdicts; smoke
  harness at `tests/fixtures/audit-seeded/`.
- **Capture loop** — `learning_capture.sh add` accepts `--severity`,
  `--detection-cue`, `--prevention-rule`, `--provenance`;
  `antipattern-detect`/`learning-loop` captures become active in guidance and
  audits in one step.

---

## [2026-06]

### specs/368 — Deploy Reconciliation Review (shipped 2026-06-30, PR #443)

- **`/deploy-reconcile`** — compares what Manifest deployed into the assistant
  homes (`~/.claude` + mirrors) against what the project would deploy, listing
  orphaned deployed items KEEP/REMOVE (`deploy_reconcile.sh` + `reconcile_core.py`,
  realpath-deduped `skills/` + `config/` namespaces).
- Preview by default; removal is opt-in and recoverable (timestamped backup,
  never a hard delete).

### specs/367 — Sub-Agent Dispatch Guidance (shipped 2026-06-30, PR #441)

- **One documented home for dispatch rules** — `references/sub-agent-dispatch.md`
  (native Task sub-agents vs `parallel_agent.py`, the ≥3-independent-units
  threshold, no recursion, cross-platform fallback); skills link there instead of
  restating the rules.
- Every skill carries a `subagents: always|conditional|never` disposition in
  `command_config.yml` `tool_policies`, enforced by `subagent_policy.bats`.

### specs/365 — Codified State-Gated Dev Lifecycle (shipped 2026-06-30, PR #432)

- **`/lifecycle`** drives a feature/issue through the codified
  specify→…→verify phases with hard phase-gating; entry is a ticket URL/issue
  key, and the Verify gate requires a smoke test.

### specs/366 — Coding Standards & No-Bypass CI Gate (shipped 2026-06-29, PR #440)

- **`docs/CODING_STANDARDS.md`** — per-language standards with explicit
  enforcement layers; `lint_on_edit_hook.sh` gives edit-time lint feedback.
- **Changed-file pre-commit gate in CI** — the full hook suite runs on every
  file a PR touches, with no bypass path.

### specs/364 — Graphify Integration (shipped 2026-06-29, PR #433)

- **`/graphify`** maps a codebase, docs set, or GitHub repo into a queryable
  knowledge graph (`graph.html`, `GRAPH_REPORT.md`, `graph.json`); the
  `graphify` CLI is installed by bootstrap behind
  `--enable-graphify`/`--disable-graphify` (default: enabled).
- Managed *tool*, not an orchestration agent — never part of
  `parallel_agent.py` consensus.

### specs/363 — Smoke-Test Orchestrator (shipped 2026-06-28, PR #431)

- **smoke-orchestrator skill + `smoke_test.py`** — catalog-driven smoke tests
  (`smoke-catalog/`) with `append`/`run`/`list`/`prune`; UI steps run via
  `mode: agent` (browser-use).
- **`/browser-test` deprecated** — superseded by the orchestrator, with a
  documented migration path.

### specs/362 — Command Discovery & Workflow Guidance (shipped 2026-06-22, PR #396)

- **`/help` command discovery** — a read-only skill that lists and searches every
  command by category with a one-line description + when-to-use cue, marking
  commands unavailable in the current environment. Ranked, deterministic, offline.
- **Generated, drift-free `docs/COMMANDS.md`** — `command_catalog.py` builds a
  machine catalog from `SKILL.md` frontmatter (the single source of truth);
  `generate_commands_doc.py` renders it and `--check` fails CI on drift (FR-004).
- **Curated category taxonomy** (`command_categories.yml`) — 8 categories assigned
  via frontmatter > overrides map > `uncategorized` (no mass SKILL.md rename).
- **Event-driven, one-shot workflow hints** (`guidance_hint.py` +
  `hint_registry.yml`) at recognized moments (pre-commit, PR-open, refactor-start,
  high-context), deduped + priority-ordered, fail-open, never added to
  always-loaded context. Delivered via Claude Code + Gemini hooks; Codex/Antigravity
  use a documented standing-line fallback in `AGENTS.md`.
- **Tunable best-practice reminders** — `guidance.yml` shipped defaults (all on) ←
  gitignored `~/.claude/config/guidance_local.yml` override (local wins); global +
  per-category opt-out, verbosity, and rate-limiting. A single opt-out never dirties
  the tracked tree (SC-004).
- **Cross-platform parity** — compact, description-less command index injected into
  `GEMINI.md`/`AGENTS.md` (budget-bounded, drift-checked) and a Cursor
  `commands-index.mdc` rule; full descriptions stay in `/help` and `docs/COMMANDS.md`.

### specs/003 — Skill Library Consolidation & Repo Health (shipped 2026-06-11, PRs #289 #291 #293 #294 #296)

- **Skill library consolidated 81 → 69** (specs/003) — six duplicate clusters
  resolved (five merged, one cross-anchored): `address-pr-comments` (absorbs 2), `session-memory-compress`
  (absorbs 1), `live-data-validation` (absorbs 3, mode subsections), new
  `verify-premise` (absorbs 5), new `retire-component-cleanup` (absorbs 3);
  `reset-reapply-clean-pr`/`clean-pr-from-stale-base` gain mutual decision
  anchors. Evolve's `{{LIBRARY}}` prompt now carries `name — description`
  lines so duplicates are suppressed at the source.
- **Prune-on-deploy** — `deploy_home_skills` prunes previously-deployed skills
  removed from the source of truth via a `.deployed-skills` manifest; skills
  added by other tools are never touched (does NOT reintroduce the PR #255
  blind `--delete` data-loss bug).
- **Docs accuracy** — command tables unified to canonical `docs/COMMANDS.md`
  (33 rows mirrored byte-identically in CLAUDE.md, AGENTS.md,
  configs/claude/CLAUDE.md); skill counts corrected to 69.
- **Tests & CI (US4)** — behavioral bats suites for learning_capture,
  check_status, generate_cursor_rules, browser_test (+ browser-use
  bootstrap toggle); CI pins matching pre-commit + dependency caching;
  cursor-rules drift check now catches untracked files.
- **Hygiene (US5)** — specs/002 marked Delivered; canonical `err()`
  convention swept across configs/claude/scripts/; `--help` on all
  user-facing scripts (+ help_coverage.bats); records/ and
  package-lock.json untracked and gitignored.

### Added

- **SkillClaw promote audit log + live status/ETA** (PR #284) — new `skillclaw_audit.py`
  writes an append-only `~/.skillclaw/promote.log` (JSONL history, self-trimmed to
  ~50 runs) and a live `status.json` snapshot. `skillclaw_promote.sh --status`
  reports where a run is and a rough ETA; the evolve stage prints per-chunk
  progress. Fail-open: audit I/O never blocks a promote run.
- **45 SkillClaw-evolved skills** (PR #285) — promoted via the proxy-free
  evolve pipeline, one commit per skill.
- **spec-review reviewer: Gemini → agy (Antigravity)** (PR #282) — seam renamed
  to `SPEC_REVIEW_CLI`, default reviewer `agy`.

### Fixed

- **label_sync.sh Bash 3.2 `set -u` crash** — empty `team_args[@]` expansion
  guarded; sync no longer aborts after the first label.
- **SkillClaw promote review fixes** (PRs #284/#285) — truthful `run_start`
  config, dropped-candidate reasons, evolve `stage_start` on empty sessions,
  ingest count in `stage_end`, `trim()` clamp.

## [2026-05]

### Added

- **`agents/` package** (PR #260) — `parallel_agent.py` modularized into a proper Python package:
  `agents/cli.py`, `config.py`, `orchestrator.py`, `runners.py`, `synthesis.py`, `validation.py`
- **`sync-skills` CLI command** (PR #258) — native binary at `~/.local/bin/sync-skills` for daily
  skill development; deploys `.retired skill supply/skills/` to all home targets with `MANIFEST_ROOT` support
- **CI drift guard** (PR #259) — detects stale cursor rules and config drift in CI
- **speckit integration** — spec/plan/task workflow tooling initialized

### Changed

- **retired skill supply centralization** (PR #255) — `.retired skill supply/skills/` is now the source of truth;
  `configs/claude/skills/` is a compatibility symlink; bootstrap uses additive rsync (no `--delete`)
- **CLAUDE.md tiered** (PR #255) — core guide + reference index pattern adopted

### Removed

- **`parallel_agent.sh`** (PR #257) — retired in favor of `parallel_agent.py`; all 56+ call sites
  updated; -1939 lines

### Fixed

- **rsync `--delete` data-loss bug** (PR #255) — removed `--delete` from skill deploy to prevent
  wiping home skills on bootstrap

---

## [2026-02]

### Added

- **Codex CLI support** — fourth agent alongside Cursor, Gemini, Claude
- **Python parallel agent** (PR #49) — feature parity with shell script: async orchestration,
  structured JSON logging, Tier 1/2 validation engine, consensus scoring, Rich streaming display
- **`browser-test` skill** — AI-powered E2E testing via browser-use YAML prompts
- **GitLab CI** and multi-language prompt templates

### Fixed

- Command injection in `CursorAgent` (CWE-78)
- `git_ops.sh` GitLab flag translation for `pr-create`
- MCP re-registration guard (skip when already configured)

---

## [2026-01]

### Added

- Unified label management across GitHub, GitLab, and Linear (`labels.yml`, `label_sync.sh`)
- `issue-prioritize` and `issue-triage` commands with Linear integration
- Bootstrap modularization (`bootstrap/lib/`) with hookable module system
- Production-grade permission templates (Django, Express, Go microservices, Python monorepo)
- `docs/` documentation hub with Getting Started, Configuration, Troubleshooting, Architecture

### Changed

- Deployment configs moved to `configs/` to prevent session config override

---

## Related Documents

- [README.md](README.md) — Project overview
- [docs/README.md](docs/README.md) — Documentation hub
