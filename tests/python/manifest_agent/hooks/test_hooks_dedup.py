"""Dedup and concurrency: a burst of 10 identical events collapses to
exactly one `manifest check` run, concurrent events on one candidate
serialize (no torn write), and a Stop continuation fires at most once per
unchanged candidate digest. Every process here is real: real subprocesses
racing a real fcntl-locked state file, not a mock of `check_and_record`.
"""

from __future__ import annotations

import concurrent.futures
import json


def _payload(root):
    return {
        "session_id": "s1",
        "cwd": str(root),
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "a.txt"},
    }


def _stop_payload(root):
    return {
        "session_id": "s1",
        "cwd": str(root),
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": "done",
    }


def test_ten_identical_events_run_the_check_exactly_once(hook_harness):
    payload = _payload(hook_harness.root)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(
            pool.map(
                lambda _: hook_harness.invoke("claude-code", "PostToolUse", payload),
                range(10),
            )
        )

    for result in results:
        assert result.returncode == 0
        json.loads(result.stdout.decode())  # every reply is well-formed JSON

    receipts = hook_harness.receipts()
    assert len(receipts) == 1, f"expected exactly one receipt, got {receipts}"
    # The marker file gets one appended byte per real check invocation.
    assert hook_harness.marker.read_text(encoding="utf-8") == "x"


def test_concurrent_events_on_one_candidate_serialize_no_torn_write(hook_harness):
    payload = _payload(hook_harness.root)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: hook_harness.invoke("claude-code", "PostToolUse", payload),
                range(8),
            )
        )
    for result in results:
        assert result.returncode == 0
    receipts = hook_harness.receipts()
    assert len(receipts) == 1
    # A torn concurrent write would fail to parse or be missing required keys.
    receipt = receipts[0]
    assert set(receipt) >= {"schema_version", "client", "event", "status", "candidate_digest"}


def test_stop_continuation_fires_at_most_once_per_unchanged_digest(hook_harness):
    payload = _stop_payload(hook_harness.root)
    first = hook_harness.invoke("claude-code", "Stop", payload)
    second = hook_harness.invoke("claude-code", "Stop", payload)

    first_body = json.loads(first.stdout.decode())
    second_body = json.loads(second.stdout.decode())

    receipts = hook_harness.receipts()
    assert len(receipts) == 1, "second Stop on an unchanged candidate must not re-run"
    assert first_body != {} or second_body == {}
