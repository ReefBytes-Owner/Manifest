"""Cursor CLI adapter: `manifest hook cursor <event>`.

Shape taken from this repository's
`plugins/manifest-workspace/skills/ai-hooks-integration/references/schemas/cursor-hooks.schema.json`
and `.../contracts/cursor-beforeShellExecution-contract.yaml`. Cursor's
documented hook surface here is `beforeShellExecution` only; every other
event name is reported unsupported, never emulated.
`client_version_verified` is always false; see docs/SHARED_CHECKS_HOOKS.md.
"""

from __future__ import annotations

import json
import sys

from . import core

CLIENT = "cursor"

EVENT_PROFILE: dict[str, str | None] = {
    "beforeShellExecution": core.QUICK,
}

_STRING_FIELDS = ("command", "cwd")


def _validate(payload: dict) -> None:
    core.require_string_fields(payload, _STRING_FIELDS)


def _format(outcome: core.AdapterOutcome) -> dict:
    if outcome.coverage == "unsupported":
        return {"coverage": "unsupported"}
    blocked = outcome.action == "block"
    return {
        "continue": not blocked,
        "permission": "deny" if blocked else "allow",
        "user_message": outcome.reason,
        "agent_message": outcome.reason,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(json.dumps({"coverage": "unsupported"}))
        return 0
    event = argv[0]
    try:
        payload = core.parse_event_object(core.read_bounded_stdin(sys.stdin.buffer))
        if event not in EVENT_PROFILE:
            print(json.dumps({"coverage": "unsupported"}))
            return 0
        _validate(payload)
        request = core.EventRequest(
            client=CLIENT,
            event=event,
            profile=EVENT_PROFILE[event],
            payload=payload,
            project_config=core.default_project_config(),
            state_dir=core.default_state_dir(),
            timeout_seconds=core.default_timeout_seconds(),
        )
        outcome = core.process_event(request)
    except (core.ProtocolError, OSError, RuntimeError, ValueError) as error:
        reason = core.safe_reason(error)
        print(
            json.dumps(
                {
                    "continue": False,
                    "permission": "deny",
                    "user_message": reason,
                    "agent_message": reason,
                }
            )
        )
        return 0
    print(json.dumps(_format(outcome)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
