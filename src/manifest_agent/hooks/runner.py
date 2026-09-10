"""Invoke `manifest check` via argv, no shell, with the real deadline and
process-group kill `checks/process.py::run_argv` already implements and
tests — this module never reimplements that mechanism."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from manifest_agent.process import redact_text

from ..checks.process import run_argv

DIAGNOSTIC_CAP = 4096
# The env var a check body must see, transitively, to refuse a recursive
# `manifest hook` re-entry (e.g. a check body that shells out to a client
# CLI). `checks/cli.py::ENVIRONMENT_KEYS` forwards this same name into every
# check body's own subprocess, so setting it here is what makes the
# recursion guard in core.py reach descendants, not just this one child.
RECURSION_ENV_VAR = "MANIFEST_HOOK_ACTIVE"
# XDG_STATE_HOME travels with the rest so the invoked `manifest check`
# subprocess resolves state paths against the same sink this adapter is
# configured against, matching a direct (non-hook) invocation -- omitting it
# would default the child to the real, un-isolated HOME-derived state
# directory. The child no longer writes its own telemetry record for a
# hook-driven run (it sees MANIFEST_HOOK_ACTIVE below and defers to this
# adapter's own record; see checks/cli.py::_record_check_telemetry), but the
# rest of its state resolution should still agree with the adapter's.
FORWARDED_ENV_KEYS = (
    "HOME",
    "PATH",
    "PYTHONPATH",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "XDG_STATE_HOME",
)


def run_manifest_check(
    *, profile: str, project_config: Path, base: str, cwd: Path, timeout_seconds: float
) -> tuple[str, str]:
    argv = (
        sys.executable,
        "-m",
        "manifest_agent",
        "check",
        profile,
        "--project-config",
        str(project_config),
        "--base",
        base,
        "--json",
    )
    env = {key: os.environ[key] for key in FORWARDED_ENV_KEYS if key in os.environ}
    env[RECURSION_ENV_VAR] = "1"
    result = run_argv(argv, cwd=cwd, env=env, timeout_seconds=timeout_seconds)
    if result.timed_out:
        return "BLOCKED", "manifest check exceeded the adapter deadline"
    if result.error:
        return "BLOCKED", redact_text(result.error)[:DIAGNOSTIC_CAP]
    try:
        status = json.loads(result.stdout).get("status", "BLOCKED")
    except ValueError:
        status = "PASS" if result.returncode == 0 else "BLOCKED"
    diagnostics = redact_text(result.stdout + result.stderr)[:DIAGNOSTIC_CAP]
    return status, diagnostics
