"""End-to-end: a supported event actually runs `manifest check` (real
subprocess), writes exactly one receipt, and the receipt is honest about
what has not been verified."""

from __future__ import annotations

import json
import os
import stat

from .conftest import REPO_SRC, write_custom_check_project


def _payload(root):
    return {
        "session_id": "s1",
        "cwd": str(root),
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "a.txt"},
    }


def test_supported_event_runs_the_real_check_and_writes_one_receipt(hook_harness):
    result = hook_harness.invoke(
        "claude-code", "PostToolUse", _payload(hook_harness.root)
    )
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


def test_invoke_never_writes_pycache_into_the_real_src_tree(hook_harness):
    """`hooks/runner.py::run_manifest_check` invokes `python -m
    manifest_agent check ...` against `PYTHONPATH=<repo>/src` (via
    `HookHarness.env`), so a `FORWARDED_ENV_KEYS` that drops
    `PYTHONDONTWRITEBYTECODE`/`PYTHONPYCACHEPREFIX` writes real bytecode
    straight into this repo's own `src/manifest_agent/**` on every
    hook-driven run -- exactly what the strict per-check candidate walk
    (C7d) flags as "candidate identity changed" when this same argv runs
    against a candidate copy instead."""
    before = set(REPO_SRC.rglob("__pycache__"))

    result = hook_harness.invoke(
        "claude-code", "PostToolUse", _payload(hook_harness.root)
    )

    assert result.returncode == 0
    after = set(REPO_SRC.rglob("__pycache__"))
    new_dirs = after - before
    assert not new_dirs, (
        "hook-driven `manifest check` wrote __pycache__ into the real src "
        f"tree: {sorted(str(p) for p in new_dirs)}"
    )


def test_stdout_is_exactly_one_json_document_no_stray_output(hook_harness):
    result = hook_harness.invoke(
        "claude-code", "PostToolUse", _payload(hook_harness.root)
    )
    assert result.stderr == b""
    lines = result.stdout.decode().splitlines()
    assert len(lines) == 1
    json.loads(lines[0])  # must parse as one document


def test_recursion_guard_refuses_a_real_nested_invocation(hook_harness, tmp_path):
    """The recursion marker must reach a check body that itself spawns a
    nested `manifest hook` call -- not just a hand-set env var in the test
    process. This drives the real chain: adapter -> `manifest check` ->
    check body -> nested `manifest hook`, and inspects the NESTED call's
    own stdout, proving `MANIFEST_HOOK_ACTIVE` actually propagated through
    `checks/cli.py::ENVIRONMENT_KEYS` into the check body's environment."""
    nested_output = tmp_path / "nested-output.json"
    nested_payload = json.dumps(
        {
            "session_id": "nested",
            "cwd": str(hook_harness.root),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hi"},
        }
    )
    script = tmp_path / "spawn_nested_hook.py"
    script.write_text(
        "import json, os, subprocess, sys\n"
        "env = dict(os.environ)\n"
        f"env['XDG_STATE_HOME'] = {str(hook_harness.state_home)!r}\n"
        f"env['MANIFEST_HOOK_PROJECT_CONFIG'] = {str(hook_harness.project_config)!r}\n"
        "result = subprocess.run(\n"
        "    [sys.executable, '-B', '-m', 'manifest_agent', 'hook', 'claude-code', 'PreToolUse'],\n"
        f"    input={nested_payload!r}, capture_output=True, env=env, text=True,\n"
        ")\n"
        f"open({str(nested_output)!r}, 'w').write(result.stdout)\n",
        encoding="utf-8",
    )
    check_config = tmp_path / "recursion-project-checks.json"
    write_custom_check_project(check_config, script)

    result = hook_harness.invoke(
        "claude-code",
        "PostToolUse",
        _payload(hook_harness.root),
        MANIFEST_HOOK_PROJECT_CONFIG=str(check_config),
    )

    assert result.returncode == 0
    outer_body = json.loads(result.stdout.decode())
    assert outer_body["hookSpecificOutput"].get("hookEventName") == "PostToolUse"
    # The outer run is a real, non-recursive check run -- it must PASS and
    # produce exactly the nested call's own output file.
    assert nested_output.is_file(), "the check body never ran the nested invocation"
    nested_body = json.loads(nested_output.read_text(encoding="utf-8"))
    hook_specific = nested_body["hookSpecificOutput"]
    assert hook_specific["permissionDecision"] == "allow"
    assert "recursive" in hook_specific["permissionDecisionReason"].lower()
    # The recursion refusal must not itself have run a nested check or
    # written a second receipt for a different candidate/event pair.
    assert {r["event"] for r in hook_harness.receipts()} == {"PostToolUse"}


def test_read_only_state_dir_still_emits_protocol_json_not_a_traceback(hook_harness):
    """`state.py`'s `_lock` calls `state_dir.mkdir()` / `os.open(..., O_CREAT)`
    -- both raise `OSError` against a read-only `XDG_STATE_HOME`. That must
    still produce protocol JSON on stdout, exit 0, never a bare traceback on
    stderr with empty stdout (the fail-open contract every client's own
    documented exit-code semantics assumes)."""
    read_only_home = hook_harness.state_home.parent / "read-only-state"
    read_only_home.mkdir()
    os.chmod(read_only_home, stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = hook_harness.invoke(
            "claude-code",
            "PostToolUse",
            _payload(hook_harness.root),
            XDG_STATE_HOME=str(read_only_home),
        )
    finally:
        os.chmod(read_only_home, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    assert result.stdout.strip() != b""
    body = json.loads(result.stdout.decode())
    assert body.get("decision") == "block"
    assert "reason" in body


def test_two_distinct_events_on_one_digest_both_run(hook_harness):
    """Dedup keys on (client, event, digest) -- a *different* event for an
    unchanged candidate must not be treated as a duplicate of the first."""
    post_payload = _payload(hook_harness.root)
    pre_payload = {**post_payload, "hook_event_name": "PreToolUse"}
    first = hook_harness.invoke("claude-code", "PostToolUse", post_payload)
    second = hook_harness.invoke("claude-code", "PreToolUse", pre_payload)

    assert first.returncode == 0
    assert second.returncode == 0
    events = {r["event"] for r in hook_harness.receipts()}
    assert events == {"PostToolUse", "PreToolUse"}
    assert hook_harness.marker.read_text(encoding="utf-8") == "xx"
