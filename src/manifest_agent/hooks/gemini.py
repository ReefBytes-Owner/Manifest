"""Gemini CLI adapter: `manifest hook gemini <event>`.

Shape taken from this repository's
`plugins/manifest-workspace/skills/ai-hooks-integration/references/contracts/
gemini-beforetool-contract.yaml` and `gemini-session-contract.yaml`.
`hook_name` in the payload names the event; `SessionStart`/`SessionEnd` are
lifecycle-only (unsupported here — no file-edit or stop/handoff semantics),
`BeforeTool` is the only documented gating point.
`client_version_verified` is always false; see docs/SHARED_CHECKS_HOOKS.md.
"""

from __future__ import annotations

import json
import os
import sys

from . import core

CLIENT = "gemini"

EVENT_PROFILE: dict[str, str | None] = {
    "BeforeTool": core.QUICK,
    "SessionStart": None,
    "SessionEnd": None,
}

_STRING_FIELDS = ("hook_name", "tool_name", "session_id")
_OBJECT_FIELDS = ("tool_input",)


def _validate(payload: dict) -> None:
    core.require_string_fields(payload, _STRING_FIELDS)
    core.require_object_fields(payload, _OBJECT_FIELDS)
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict) and isinstance(tool_input.get("file_path"), str):
        core.reject_traversal(tool_input["file_path"], "tool_input.file_path")


def _cwd(payload: dict) -> str:
    # GEMINI_CWD is the documented source; the payload carries no cwd field.
    return os.environ.get("GEMINI_CWD") or payload.get("cwd") or os.getcwd()


def _format(outcome: core.AdapterOutcome) -> dict:
    if outcome.coverage == "unsupported":
        return {"coverage": "unsupported"}
    blocked = outcome.action == "block"
    body = {"decision": "deny" if blocked else "allow"}
    if blocked:
        body["reason"] = outcome.reason
    return body


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(json.dumps({"coverage": "unsupported"}))
        return 0
    event = argv[0]
    try:
        payload = core.parse_event_object(core.read_bounded_stdin(sys.stdin.buffer))
        _validate(payload)
        payload = dict(payload)
        payload["cwd"] = _cwd(payload)
        if event not in EVENT_PROFILE:
            print(json.dumps({"coverage": "unsupported"}))
            return 0
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
    except core.ProtocolError as error:
        print(json.dumps({"decision": "deny", "reason": str(error)}))
        return 0
    print(json.dumps(_format(outcome)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
