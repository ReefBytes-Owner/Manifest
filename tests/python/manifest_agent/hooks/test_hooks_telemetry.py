"""`manifest hook` writes append-only run telemetry (phase-3-5-decisions.md
5c). Reuses `hook_harness` (real subprocess, real isolated XDG_STATE_HOME) --
nothing here mocks the inner `manifest check` invocation or fakes a cost
figure. A supported event that actually runs a check produces two records:
the inner `manifest check` subprocess's own (profile-level) record, and the
adapter-level one carrying the client identity the inner run cannot know.
"""

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


def _telemetry_records(hook_harness) -> list[dict]:
    path = hook_harness.state_home / "manifest" / "telemetry" / "runs.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_supported_event_writes_an_adapter_level_telemetry_record(hook_harness):
    hook_harness.invoke("claude-code", "PostToolUse", _payload(hook_harness.root))

    records = _telemetry_records(hook_harness)
    adapter_records = [record for record in records if record["runtime"]["client"] == "claude_code"]
    assert len(adapter_records) == 1
    record = adapter_records[0]
    assert record["profile"] == "quick"
    assert record["status"] == "PASS"
    assert record["model_id"] == "unknown"
    assert record["runtime"] == {"client": "claude_code", "version": "unknown"}
    assert record["cost"] == {"status": "unknown"}


def test_recursion_refusal_never_invokes_a_check_and_writes_no_telemetry(hook_harness):
    hook_harness.invoke(
        "claude-code",
        "PostToolUse",
        _payload(hook_harness.root),
        MANIFEST_HOOK_ACTIVE="1",
    )

    assert _telemetry_records(hook_harness) == []


def test_duplicate_burst_collapses_to_one_run_and_one_adapter_telemetry_record(
    hook_harness,
):
    payload = _payload(hook_harness.root)
    for _ in range(5):
        hook_harness.invoke("claude-code", "PostToolUse", payload)

    records = _telemetry_records(hook_harness)
    adapter_records = [record for record in records if record["runtime"]["client"] == "claude_code"]
    assert len(adapter_records) == 1
