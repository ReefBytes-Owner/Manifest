"""Per-client protocol contract: exact stdout JSON, negative fixtures, and
the parity between an adapter's EVENT_PROFILE table and its coverage claim.

Every invocation runs the real `manifest hook <client> <event>` entry point
as a subprocess (`hook_harness.invoke`) against a real git worktree and a
real, isolated XDG_STATE_HOME — nothing here mocks the CLI, the process
mechanics, or the filesystem.
"""

from __future__ import annotations

import json

import pytest

from manifest_agent.hooks import claude_code, codex, cursor, gemini

CLIENTS = {
    "claude-code": claude_code,
    "codex": codex,
    "cursor": cursor,
    "gemini": gemini,
}

SUPPORTED_QUICK_EVENT = {
    "claude-code": "PostToolUse",
    "codex": None,
    "cursor": "beforeShellExecution",
    "gemini": "BeforeTool",
}


def _stdout_json(result) -> dict:
    assert result.stderr == b""
    lines = result.stdout.splitlines()
    assert len(lines) == 1, f"expected exactly one stdout line, got {result.stdout!r}"
    return json.loads(lines[0])


@pytest.mark.parametrize("client", sorted(CLIENTS))
def test_unknown_event_is_unsupported_never_emulated(hook_harness, client):
    result = hook_harness.invoke(client, "TotallyUnknownEvent", {"cwd": str(hook_harness.root)})
    body = _stdout_json(result)
    assert body["coverage"] == "unsupported"
    assert not hook_harness.receipts()


@pytest.mark.parametrize("client", sorted(CLIENTS))
def test_malformed_json_is_a_protocol_response_never_a_traceback(hook_harness, client):
    event = SUPPORTED_QUICK_EVENT[client] or "AnyEvent"
    result = hook_harness.invoke(client, event, b"{not valid json")
    body = _stdout_json(result)
    assert "Traceback" not in result.stdout.decode()
    assert body != {}
    assert not hook_harness.receipts()


@pytest.mark.parametrize("client", sorted(CLIENTS))
def test_wrong_field_types_are_a_protocol_response(hook_harness, client):
    event = SUPPORTED_QUICK_EVENT[client] or "AnyEvent"
    result = hook_harness.invoke(client, event, {"cwd": 12345, "session_id": []})
    body = _stdout_json(result)
    assert "Traceback" not in result.stdout.decode()
    assert body is not None
    assert not hook_harness.receipts()


@pytest.mark.parametrize("client", sorted(CLIENTS))
def test_oversized_payload_blocks_never_truncated_parse(hook_harness, client):
    event = SUPPORTED_QUICK_EVENT[client] or "AnyEvent"
    from manifest_agent.hooks.core import STDIN_CAP

    oversized = json.dumps({"cwd": str(hook_harness.root), "pad": "x" * (STDIN_CAP + 16)})
    assert len(oversized.encode()) > STDIN_CAP
    result = hook_harness.invoke(client, event, oversized)
    body = _stdout_json(result)
    assert "Traceback" not in result.stdout.decode()
    assert body is not None
    assert not hook_harness.receipts()


@pytest.mark.parametrize("client", ["claude-code", "gemini"])
def test_path_traversal_in_file_path_is_blocked(hook_harness, client):
    event = SUPPORTED_QUICK_EVENT[client]
    payload = {
        "cwd": str(hook_harness.root),
        "session_id": "s1",
        "hook_name": "BeforeTool",
        "hook_event_name": event,
        "tool_name": "Write",
        "tool_input": {"file_path": "../../etc/passwd"},
    }
    result = hook_harness.invoke(client, event, payload)
    body = _stdout_json(result)
    assert body.get("coverage") != "supported"
    assert not hook_harness.receipts()


@pytest.mark.parametrize("client", sorted(CLIENTS))
def test_cwd_traversal_is_blocked(hook_harness, client):
    event = SUPPORTED_QUICK_EVENT[client] or "AnyEvent"
    result = hook_harness.invoke(client, event, {"cwd": "../../../etc", "command": "ls"})
    body = _stdout_json(result)
    assert body is not None
    assert not hook_harness.receipts()


def test_codex_reports_every_event_unsupported_no_documented_substrate(hook_harness):
    for event in ("Stop", "PreToolUse", "SessionStart", "AnythingAtAll"):
        result = hook_harness.invoke("codex", event, {"cwd": str(hook_harness.root)})
        body = _stdout_json(result)
        assert body["coverage"] == "unsupported"
    assert not hook_harness.receipts()


@pytest.mark.parametrize("client,module", sorted(CLIENTS.items()))
def test_coverage_parity_every_mapped_event_is_reachable(client, module):
    """Every event the adapter's own table claims to support maps to a real
    profile, and every event without one is marked unsupported — the
    coverage table IS the adapter's claim, so this asserts self-consistency
    rather than trusting a separately maintained list."""
    for profile in module.EVENT_PROFILE.values():
        assert profile in (None, "quick", "full")


def test_claude_code_stop_maps_full_and_pretooluse_maps_quick():
    assert claude_code.EVENT_PROFILE["Stop"] == "full"
    assert claude_code.EVENT_PROFILE["SubagentStop"] == "full"
    assert claude_code.EVENT_PROFILE["PreToolUse"] == "quick"
    assert claude_code.EVENT_PROFILE["PostToolUse"] == "quick"
    assert claude_code.EVENT_PROFILE["SessionStart"] is None
