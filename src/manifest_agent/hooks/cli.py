"""`manifest hook <client> <event>` — dispatch to the matching thin adapter.

Each adapter module owns its own stdin reading (bounded, then parsed) so this
file stays a pure dispatcher: no protocol knowledge lives here.
"""

from __future__ import annotations

import click

from . import claude_code, codex, cursor, gemini

_ADAPTERS = {
    "claude-code": claude_code.main,
    "codex": codex.main,
    "cursor": cursor.main,
    "gemini": gemini.main,
}


@click.command("hook", context_settings={"ignore_unknown_options": True})
@click.argument("client", type=click.Choice(sorted(_ADAPTERS)))
@click.argument("event")
@click.pass_context
def hook(context: click.Context, client: str, event: str) -> None:
    """Run the CLIENT adapter for EVENT, reading its payload from stdin."""
    context.exit(_ADAPTERS[client]([event]))
