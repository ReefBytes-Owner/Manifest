"""Run bats and append a `not ok` trailer so a truncated receipt still names failures.

Correction 15 rule 3 (phase-3-5-decisions.md): `bats` prints its own
`not ok`/`ok` lines interleaved throughout a run, not gathered at the end.
`run_argv` (manifest_agent.checks.process) already keeps the TAIL of a
truncated stream on the assumption that a test runner's own summary lives
there (true for pytest) -- but bats has no such trailing summary, so a
head-truncated receipt of a long TAP stream can silently hide exactly which
tests failed. This body streams bats's TAP output through UNCHANGED, then
appends a `# not ok summary: N` line plus every `not ok` line it saw, so that
summary survives any tail-preserving truncation regardless of where in the
stream the real failures occurred.

The registry's own `store:` executable resolution and `path_prepend` are
untouched: `tools.test.bats.executable` in config/project-checks.json still
names `store:node-env/bin/bats` for version-probing and PATH construction,
so this body's own `bats` invocation resolves via the SAME already-verified
PATH entries the runner built for it (`toolchain.resolve()`'s hash check),
not a second, independent lookup this script would have to trust on its own.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading

_NOT_OK_PREFIX = "not ok "


def _pump(source, sink, collected: list[str] | None) -> None:
    """Relay lines from `source` to `sink` verbatim, optionally collecting them."""
    for line in source:
        sink.write(line)
        sink.flush()
        if collected is not None and line.startswith(_NOT_OK_PREFIX):
            collected.append(line.rstrip("\n"))


def run_bats_with_trailer(arguments: list[str]) -> int:
    """Run bats against `arguments`, streaming TAP, then append the trailer.

    Returns bats's own exit code so the check's pass/fail signal is
    unchanged; the trailer is additive, never a substitute for it.
    """
    bats = shutil.which("bats")
    if bats is None:
        sys.stderr.write("bats_body.py: no `bats` on PATH -- toolchain unresolved\n")
        return 2
    process = subprocess.Popen(
        (bats, *arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    not_ok_lines: list[str] = []
    stdout_thread = threading.Thread(
        target=_pump, args=(process.stdout, sys.stdout, not_ok_lines)
    )
    stderr_thread = threading.Thread(
        target=_pump, args=(process.stderr, sys.stderr, None)
    )
    stdout_thread.start()
    stderr_thread.start()
    returncode = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    sys.stdout.write(f"# not ok summary: {len(not_ok_lines)}\n")
    for line in not_ok_lines:
        sys.stdout.write(f"{line}\n")
    sys.stdout.flush()
    return returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run bats and append a not-ok failure trailer."
    )
    parser.add_argument("bats_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str]) -> int:
    arguments = _parser().parse_args(argv).bats_args
    return run_bats_with_trailer(arguments)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
