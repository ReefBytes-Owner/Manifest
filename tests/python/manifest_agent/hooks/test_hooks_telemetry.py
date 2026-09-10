"""`manifest hook` writes append-only run telemetry (phase-3-5-decisions.md
5c). Reuses `hook_harness` (real subprocess, real isolated XDG_STATE_HOME) --
nothing here mocks the inner `manifest check` invocation or fakes a cost
figure. A supported event that actually runs a check produces exactly ONE
record: the adapter-level one `hooks/telemetry.py` writes, carrying the
client identity. The inner `manifest check` subprocess sees
`MANIFEST_HOOK_ACTIVE` in its own environment and skips its own write
(`checks/cli.py::_record_check_telemetry`) so attempts/repair cycles never
double for a hook-driven run.
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
    adapter_records = [
        record for record in records if record["runtime"]["client"] == "claude_code"
    ]
    assert len(adapter_records) == 1
    record = adapter_records[0]
    assert record["profile"] == "quick"
    assert record["status"] == "PASS"
    assert record["model_id"] == "unknown"
    assert record["runtime"] == {"client": "claude_code", "version": "unknown"}
    assert record["cost"] == {"status": "unknown"}


def test_a_hook_run_writes_exactly_one_record_total_not_a_second_inner_one(
    hook_harness,
):
    """Regression for the double-write defect: `hooks/runner.py` forwards
    `XDG_STATE_HOME` to the inner `manifest check` subprocess so it can write
    to the same sink, but the inner process must see `MANIFEST_HOOK_ACTIVE`
    in its own environment and skip its own write. Asserts over ALL records
    in the file -- not a subset filtered to `runtime.client == "claude_code"`,
    which is exactly the filter that let the inner record hide undetected."""
    hook_harness.invoke("claude-code", "PostToolUse", _payload(hook_harness.root))

    all_records = _telemetry_records(hook_harness)
    assert len(all_records) == 1


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
    adapter_records = [
        record for record in records if record["runtime"]["client"] == "claude_code"
    ]
    assert len(adapter_records) == 1
