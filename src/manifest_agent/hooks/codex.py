"""Codex CLI adapter: `manifest hook codex <event>`.

`configs/codex/AGENTS.md` states plainly: "Codex and Antigravity have no
event-hook substrate." No Codex hook contract exists anywhere in this
repository to vendor a shape from — so unlike the other three clients, this
adapter declares zero supported events by construction, not by omission.
Bounded/structural stdin handling still applies (a client that later grows a
substrate must not get a worse contract than an unsupported one), and every
event still reports the same honest `{"coverage": "unsupported"}` shape.
`client_version_verified` is always false; see docs/SHARED_CHECKS_HOOKS.md.
"""

from __future__ import annotations

import json
import sys

from . import core

CLIENT = "codex"

# Intentionally empty: no documented event maps to a profile.
EVENT_PROFILE: dict[str, str | None] = {}

_STRING_FIELDS = ("session_id", "cwd", "event")


def _validate(payload: dict) -> None:
    core.require_string_fields(payload, _STRING_FIELDS)
    cwd = payload.get("cwd")
    if isinstance(cwd, str):
        core.reject_traversal(cwd, "cwd")


def main(argv: list[str]) -> int:
    try:
        payload = core.parse_event_object(core.read_bounded_stdin(sys.stdin.buffer))
        _validate(payload)
    except (core.ProtocolError, OSError, RuntimeError, ValueError) as error:
        print(
            json.dumps({"coverage": "unsupported", "reason": core.safe_reason(error)})
        )
        return 0
    print(json.dumps({"coverage": "unsupported"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
