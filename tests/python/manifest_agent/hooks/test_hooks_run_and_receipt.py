"""End-to-end: a supported event actually runs `manifest check` (real
subprocess), writes exactly one receipt, and the receipt is honest about
what has not been verified."""

from __future__ import annotations

import json


def _payload(root):
    return {
        "session_id": "s1",
        "cwd": str(root),
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "a.txt"},
    }


def test_supported_event_runs_the_real_check_and_writes_one_receipt(hook_harness):
    result = hook_harness.invoke("claude-code", "PostToolUse", _payload(hook_harness.root))
    assert result.returncode == 0
    body = json.loads(result.stdout.decode())
    assert "hookSpecificOutput" in body

    receipts = hook_harness.receipts()
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["client"] == "claude_code"
    assert receipt["event"] == "PostToolUse"
    assert receipt["profile"] == "quick"
    assert receipt["status"] == "PASS"
    assert receipt["client_version_verified"] is False
    assert hook_harness.marker.read_text(encoding="utf-8") == "x"


def test_stdout_is_exactly_one_json_document_no_stray_output(hook_harness):
    result = hook_harness.invoke("claude-code", "PostToolUse", _payload(hook_harness.root))
    assert result.stderr == b""
    lines = result.stdout.decode().splitlines()
    assert len(lines) == 1
    json.loads(lines[0])  # must parse as one document


def test_recursion_marker_refuses_without_running_a_check(hook_harness):
    result = hook_harness.invoke(
        "claude-code",
        "PostToolUse",
        _payload(hook_harness.root),
        MANIFEST_HOOK_ACTIVE="1",
    )
    assert result.returncode == 0
    assert not hook_harness.receipts()
    assert not hook_harness.marker.exists()
