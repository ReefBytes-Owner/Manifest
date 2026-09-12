#!/usr/bin/env python3
"""The always-on SessionStart hook must never break a user's session.

It runs on every session start, so any traceback or hang there is a failure
of the whole session rather than of the hook. These pin the two structural
guarantees: exit 0 on any exception type, and a bounded stdin read.

Run with: uv run --project configs/claude pytest tests/python/test_adhd_hook_fail_open.py -q
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.python.manifest_agent._subprocess_env import isolated_env

HOOK = (
    Path(__file__).resolve().parents[2]
    / "plugins/manifest-i-have-adhd/hooks/always_on.py"
)


def _hook_env(tmp_path: Path) -> dict[str, str]:
    return isolated_env(
        PATH="/usr/bin:/bin",
        HOME=str(tmp_path),
        MANIFEST_STATE_ROOT=str(tmp_path / "state"),
    )


def _run(payload: bytes, tmp_path: Path, timeout: int = 30):
    return subprocess.run(
        (sys.executable, str(HOOK)),
        input=payload,
        capture_output=True,
        timeout=timeout,
        env=_hook_env(tmp_path),
    )


def test_valid_session_start_renders_guidance(tmp_path):
    result = _run(
        b'{"hook_event_name":"SessionStart","session_id":"s",'
        b'"cwd":"/tmp","source":"startup"}',
        tmp_path,
    )
    assert result.returncode == 0
    assert b"Manifest ADHD guidance" in result.stdout


@pytest.mark.parametrize(
    "payload",
    [
        b"",  # empty
        b"not json at all",  # unparseable
        b"[1, 2, 3]",  # wrong top-level type
        b'{"hook_event_name":"Other"}',  # wrong event
        b'{"hook_event_name":"SessionStart","cwd":42}',  # wrong field type
        b"\xff\xfe\x00binary",  # undecodable bytes
    ],
)
def test_malformed_payloads_still_exit_zero(payload, tmp_path):
    """Fail-open: a bad payload degrades to silence, never a traceback."""
    result = _run(payload, tmp_path)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"Traceback" not in result.stderr


def test_hook_does_not_hang_when_stdin_is_never_closed(tmp_path):
    """An unclosed write end must hit the deadline, not stall the session."""
    process = subprocess.Popen(
        (sys.executable, str(HOOK)),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_hook_env(tmp_path),
    )
    try:
        # Write nothing and never close stdin: the read would block forever.
        returncode = process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        pytest.fail("hook hung on an unclosed stdin instead of timing out")
    assert returncode == 0
