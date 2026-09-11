"""Append a `not ok` failure trailer to a captured bats TAP stream.

Correction 15 rule 3 (phase-3-5-decisions.md): unlike pytest, bats prints its
own `ok`/`not ok` lines interleaved throughout a run with no trailing
summary. `run_argv`'s tail-preserving truncation (`process.tail_bounded`)
was built on the assumption that a test runner's own summary lives at the
END of its output -- true for pytest, not for bats -- so a long TAP stream
truncated from the head can silently hide exactly which tests failed.

This lives in `manifest_agent.checks` (the trusted runner side, never a
candidate-side check body) and is called by `runner.execute_check` AFTER
`run_argv` returns, on the already-captured `ProcessResult`. Wrapping
`test.bats`'s own argv in a body script was the first approach tried, but
`registry._validate_tool_reference` requires `check["argv"][0]` to exactly
equal the declared tool's `executable` (`store:node-env/bin/bats`) -- a real
trust invariant (a check can never point argv[0] at something other than
its audited tool), not something to relax for this. Post-processing the
captured result after the fact keeps that invariant intact.
"""

from __future__ import annotations

_NOT_OK_PREFIX = "not ok "


def append_not_ok_trailer(stdout: str) -> str:
    """Return `stdout` with a `# not ok summary: N` trailer plus every
    `not ok ` line, in the order they appeared. A no-failures stream still
    gets a `# not ok summary: 0` trailer, so its presence or absence is
    never itself evidence either way -- only the count is."""
    not_ok_lines = [
        line for line in stdout.splitlines() if line.startswith(_NOT_OK_PREFIX)
    ]
    trailer_lines = [f"# not ok summary: {len(not_ok_lines)}", *not_ok_lines]
    trailer = "\n".join(trailer_lines) + "\n"
    if stdout and not stdout.endswith("\n"):
        return f"{stdout}\n{trailer}"
    return f"{stdout}{trailer}"
