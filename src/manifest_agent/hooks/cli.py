"""`manifest hook <client> <event>` — dispatch to the matching thin adapter.

Each adapter module owns its own stdin reading (bounded, then parsed) so this
file stays a pure dispatcher: no protocol knowledge lives here.

`manifest hook verify <client>` is a sibling subcommand (not an event): it
probes an installed client instead of reading stdin. See `verify.py`.
"""

from __future__ import annotations

import json
import os

import click

from . import claude_code, codex, cursor, gemini, verify

_ADAPTERS = {
    "claude-code": claude_code.main,
    "codex": codex.main,
    "cursor": cursor.main,
    "gemini": gemini.main,
}

# CLI dispatch names (hyphenated, matching `manifest hook <client> <event>`)
# to the adapter's own CLIENT constant (underscored, matching
# `config/hook-clients.json` keys and `ReceiptInput.client`).
_CLIENT_KEYS = {
    "claude-code": claude_code.CLIENT,
    "codex": codex.CLIENT,
    "cursor": cursor.CLIENT,
    "gemini": gemini.CLIENT,
}

_VERIFY_EXITS = {
    verify.STATUS_VERIFIED: 0,
    verify.STATUS_MISMATCH: 2,
    verify.STATUS_UNAVAILABLE: 3,
}


@click.group("hook", context_settings={"ignore_unknown_options": True})
def hook() -> None:
    """Run a thin native hook adapter, or verify one against a real client."""


for _name, _entry_point in _ADAPTERS.items():

    def _make_event_command(name: str, entry_point):
        @click.command(name, context_settings={"ignore_unknown_options": True})
        @click.argument("event")
        @click.pass_context
        def _run(context: click.Context, event: str) -> None:
            """Run this client's adapter for EVENT, reading its payload from stdin."""
            context.exit(entry_point([event]))

        return _run

    hook.add_command(_make_event_command(_name, _entry_point))


@hook.command("verify")
@click.argument("client", type=click.Choice(sorted(_CLIENT_KEYS)))
@click.option(
    "--write/--no-write",
    default=False,
    help="On a verified result, promote the fixture and update config/hook-clients.json.",
)
@click.pass_context
def verify_command(context: click.Context, client: str, write: bool) -> None:
    """Probe the installed CLIENT, compare its shape to the vendored fixture,
    and report verified/mismatch/unavailable. `unavailable` is BLOCKED, never
    a pass -- see docs/SHARED_CHECKS_HOOKS.md."""
    client_key = _CLIENT_KEYS[client]
    config = verify.VerifyConfig(
        repo_root=verify.default_repo_root(),
        matrix_path=verify.default_matrix_path(),
        resolver=verify.default_resolver(os.environ.get("PATH")),
    )
    entries = verify.load_matrix(config.matrix_path)
    entry = entries.get(client_key)
    if entry is None:
        result = verify.VerifyResult(
            verify.STATUS_UNAVAILABLE,
            client_key,
            None,
            None,
            reason=f"no config/hook-clients.json entry for {client_key!r}",
        )
    else:
        result = verify.verify_client(client_key, entry, config)
        if write and result.status == verify.STATUS_VERIFIED:
            verify.promote(result, entry, config)
    click.echo(json.dumps(result.to_dict(), sort_keys=True))
    context.exit(_VERIFY_EXITS[result.status])
