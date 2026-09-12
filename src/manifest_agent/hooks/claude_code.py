"""Claude Code adapter: `manifest hook claude-code <event>`.

Event/profile mapping and field shapes are taken from this repository's own
`plugins/manifest-workspace/skills/ai-hooks-integration/references/` contract
files and `configs/claude/scripts/constitution_hook.py` — not from a live
client. `client_version_verified` is always false; see
docs/SHARED_CHECKS_HOOKS.md.
"""

from __future__ import annotations

import json
import sys

from . import core

CLIENT = "claude_code"

# Every event Claude Code's own hooks schema documents. `quick` covers
# file-edit / pre-commit-style moments; `full` only the explicit stop/handoff
# events; everything else is unsupported, never emulated.
EVENT_PROFILE: dict[str, str | None] = {
    "PreToolUse": core.QUICK,
    "PostToolUse": core.QUICK,
    "PostToolUseFailure": core.QUICK,
    "Stop": core.FULL,
    "SubagentStop": core.FULL,
    "TaskCompleted": core.FULL,
    "SessionStart": None,
    "UserPromptSubmit": None,
    "PermissionRequest": None,
    "Notification": None,
    "SubagentStart": None,
    "TeammateIdle": None,
    "ConfigChange": None,
    "WorktreeCreate": None,
    "WorktreeRemove": None,
    "PreCompact": None,
    "SessionEnd": None,
}

_STRING_FIELDS = (
    "session_id",
    "transcript_path",
    "cwd",
    "permission_mode",
    "tool_name",
)
_OBJECT_FIELDS = ("tool_input", "tool_response")


def _validate(payload: dict, event: str) -> None:
    core.require_string_fields(payload, _STRING_FIELDS)
    core.require_object_fields(payload, _OBJECT_FIELDS)
    declared = payload.get("hook_event_name")
    if isinstance(declared, str) and declared and declared != event:
        raise core.ProtocolError(
            f"hook_event_name {declared!r} does not match invoked event {event!r}"
        )
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict) and isinstance(tool_input.get("file_path"), str):
        core.reject_traversal(tool_input["file_path"], "tool_input.file_path")


def _format(event: str, outcome: core.AdapterOutcome) -> dict:
    if outcome.coverage == "unsupported":
        return {"coverage": "unsupported"}
    blocked = outcome.action == "block"
    if event == "PreToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny" if blocked else "allow",
                "permissionDecisionReason": outcome.reason,
            }
        }
    if event in ("PostToolUse", "PostToolUseFailure"):
        body: dict = {"hookSpecificOutput": {"hookEventName": event}}
        if blocked:
            body["decision"] = "block"
            body["reason"] = outcome.reason
        return body
    body = {}  # Stop / SubagentStop / TaskCompleted
    if blocked:
        body["decision"] = "block"
        body["reason"] = outcome.reason
    return body


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
        _validate(payload, event)
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
        print(json.dumps({"decision": "block", "reason": core.safe_reason(error)}))
        return 0
    print(json.dumps(_format(event, outcome)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
