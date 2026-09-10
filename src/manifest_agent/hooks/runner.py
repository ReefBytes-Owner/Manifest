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
# XDG_STATE_HOME travels with the rest so the invoked `manifest check`
# subprocess writes its own run-telemetry record (5c) to the same sink this
# adapter itself is configured against -- omitting it would default the
# child to the real, un-isolated HOME-derived state directory.
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
