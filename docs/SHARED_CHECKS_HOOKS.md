# Shared Checks — native hook adapters

> `manifest hook <client> <event>`: thin adapters that run `manifest check`
> from a live client's own hook substrate. Split out of
> [SHARED_CHECKS.md](SHARED_CHECKS.md), which is already over its line cap.

## What this is

Four thin, per-client modules — `src/manifest_agent/hooks/{claude_code,codex,
cursor,gemini}.py`, each ≤200 lines — plus shared mechanics in `core.py`,
`state.py`, `receipt.py`, and `runner.py`. Each adapter:

- reads **bounded** stdin (256 KiB cap; over the cap is a protocol block, never
  a truncated parse);
- parses JSON structurally, with per-client field-type validation (malformed
  input is a protocol response, never a traceback);
- rejects `..` traversal segments in `cwd` and any `tool_input.file_path`
  (string-checked, not resolved — a value can be rejected for containing a
  traversal segment even where `Path.resolve()` would land somewhere safe;
  this is deliberately the stricter direction);
- maps the event to a profile and invokes `manifest check <profile>` via
  **argv only** (no shell). The **adapter's own deadline** — the time budget
  for the `manifest check` subprocess as a whole — is enforced by the real
  process-group kill `checks/process.py::run_argv` already implements and
  tests. **This does not cascade into a check body's own subprocess.** Every
  `run_argv` call, including the ones each check body's execution makes
  *inside* `manifest check`, starts its own new session
  (`start_new_session=True`), and `manifest check` installs no signal handler
  of its own — so killing the adapter's `manifest check` process group does
  not reach a check body (pytest, semgrep, …) already running in its own,
  separate group. A nested `run_argv` under a short outer deadline can leave
  its own inner body running well past that deadline. Closing this gap
  requires either propagating the deadline into `manifest check` so it
  enforces its own budget against its check bodies, or unifying the process
  group across nesting levels — neither is done by this chunk;
- deduplicates repeated events by `(client, event, candidate_digest)` under an
  `fcntl`-locked state file. The lock is held for the full duration of an
  uncached event, including the `manifest check` invocation itself: a
  duplicate that arrives while the first event's verdict is still being
  computed blocks on the same lock rather than answering `allow` for an
  unknown state, and once unblocked replays the exact cached verdict. A
  burst of identical events therefore runs the check exactly once, and every
  member of the burst gets the same decision the first one produced —
  including a `block`, not just a `PASS`. Cached entries expire after 24h
  or once the newest 500 outgrow the rest, so `state.json` does not grow
  without bound;
- writes a receipt (same `fcntl` lock idiom as `preparation.py`) that always
  carries `"client_version_verified": false`;
- refuses to re-enter when `MANIFEST_HOOK_ACTIVE` is already set. This
  adapter sets it on the `manifest check` child it spawns, and
  `checks/cli.py::ENVIRONMENT_KEYS` forwards it into every check body's own
  subprocess in turn — so a check body that itself shells out to a client
  CLI (which could re-invoke `manifest hook`) sees the marker too, not just
  the direct child.

State lives under `$XDG_STATE_HOME/manifest/hooks/` (`state.json` for dedup +
stop-continuation, `receipts/` for receipts). Nothing is written to `~/`
outside that directory.

## Event → profile mapping

`quick` covers file-edit / pre-commit-style moments; `full` only the explicit
stop/handoff events; everything else is `{"coverage": "unsupported"}` —
**never emulated**.

| Client | quick | full | unsupported (examples) |
|---|---|---|---|
| `claude-code` | `PreToolUse`, `PostToolUse`, `PostToolUseFailure` | `Stop`, `SubagentStop`, `TaskCompleted` | `SessionStart`, `UserPromptSubmit`, `PermissionRequest`, `Notification`, `SubagentStart`, `TeammateIdle`, `ConfigChange`, `WorktreeCreate`, `WorktreeRemove`, `PreCompact`, `SessionEnd` |
| `cursor` | `beforeShellExecution` | — | every other event name (Cursor's documented hook surface in this repo is `beforeShellExecution` only) |
| `gemini` | `BeforeTool` | — | `SessionStart`, `SessionEnd` (lifecycle-only, no gating semantics) |
| `codex` | — | — | **every** event — see below |

## Codex: no substrate, by construction

`configs/codex/AGENTS.md` states plainly: "Codex and Antigravity have no
event-hook substrate." No Codex hook contract exists anywhere in this
repository. `codex.py`'s `EVENT_PROFILE` is the empty dict; every event
reports `{"coverage": "unsupported"}`. Bounded/structural stdin handling
still applies uniformly (oversized/malformed input still yields a protocol
response, never a traceback), so a client that later grows a real substrate
does not inherit a worse contract than an unsupported one gets today.

## Unsupported-event contract

An event absent from an adapter's `EVENT_PROFILE` table (or present but
mapped to `None`) always produces exactly `{"coverage": "unsupported"}` on
stdout — no check runs, no receipt is written, no other adapter is asked to
cover it. `tests/python/manifest_agent/hooks/test_hooks_fixture_parity.py`
asserts both directions: every vendored fixture event appears in the
adapter's own table, and every event the table claims to support has a
vendored fixture backing that claim.

## Nothing here has been verified against a real client

This is the honesty constraint the chunk was built under, not a hedge:

- The per-client protocol shapes are vendored as
  `tests/fixtures/hooks/<client>/unverified/*.json`, derived from what this
  repository already implements and documents (the
  `ai-hooks-integration` skill's contract/schema files,
  `configs/claude/scripts/constitution_hook.py`). Each client's `SOURCE.md`
  says so explicitly. The directory name is `unverified`, not a version
  number, because no pinned client version was read to produce these shapes.
- No adapter has been run under an actual Fable/Astra/Opus/Sol-mapped client.
  The model/client mapping itself is unresolved (see the phase-3–5 decision
  doc, open question 3).
- Every receipt this adapter writes carries `"client_version_verified":
  false`. This is not a placeholder to fill in later automatically — it is
  the accurate statement of what a receipt from this chunk can attest to.
  Flipping it requires a human running the adapter under a real, version-
  pinned client with network access (tracked as C10). Building the adapters
  is this chunk's scope; claiming they work with a real client is not.
- Exit-code semantics each client documents (e.g. "exit 2 blocks") are
  **not** relied on: every adapter always exits 0 and encodes the decision in
  stdout JSON, because assuming an unverified exit-code contract is exactly
  the kind of claim this chunk cannot make yet.

## Related Documents

- [SHARED_CHECKS.md](SHARED_CHECKS.md) — the `manifest check` / `check-aggregate` entry
- [config/project-checks.json](../config/project-checks.json) — the check registry
- `plugins/manifest-workspace/skills/ai-hooks-integration/` — the vendored contract/schema source material
- `tests/fixtures/hooks/` — per-client fixtures, source-labeled `unverified`
- `tests/python/manifest_agent/hooks/` — the fixture harness and negative-fixture suite
