# constitution: exempt C-SIZE — shared test-only helper, no check body.
"""Shared child-process env builder for tests that spawn Python subprocesses.

Every test under `tests/python` that hand-builds a `subprocess.run`/`Popen`/
`check_output` `env=` dict for a child that can import `manifest_agent` or
`tools` from this tree (directly, or via `PYTHONPATH`/`cwd`) must build that
dict through `isolated_env()` instead of a bare literal.

Why: `manifest check`'s own strict per-check candidate walk (C7d) runs the
`test.python` project check -- this very suite -- inside a temporary
candidate copy with `PYTHONDONTWRITEBYTECODE=1` and `PYTHONPYCACHEPREFIX`
set on ITS child env, so a minimal `pytest --collect-only` never writes
`__pycache__` into the candidate. But when a test inside the suite spawns
its OWN nested Python subprocess with a hand-built `env={...}` dict that
does not carry those two variables forward, that nested interpreter reverts
to the platform default and writes bytecode straight into
`candidate/src/manifest_agent/**` or `candidate/tools/project_checks/**`.
The strict walk then (correctly) reports "candidate identity changed" --
the walk is not wrong, the leak is.

`isolated_env()` always carries `PYTHONDONTWRITEBYTECODE=1` and a
`PYTHONPYCACHEPREFIX` (taken from the current process env when the parent
runner already set one, else a private tmp dir) into the child, on top of
whatever the caller explicitly passes. It never adds or removes any other
key -- a test that deliberately narrows to `PATH=os.defpath` or a bare
`{"PATH": ..., "HOME": ...}` keeps exactly that plus the two isolation
keys; it does not silently gain everything from `os.environ`.
"""

from __future__ import annotations

import os
import tempfile

__all__ = ["isolated_env"]

# A single process-wide fallback prefix so repeated calls within one test
# run share a cache dir instead of each minting a fresh tempdir.
_FALLBACK_PYCACHE_PREFIX = os.path.join(
    tempfile.gettempdir(), f"manifest-test-pycache-{os.getpid()}"
)


def isolated_env(**overrides: str) -> dict[str, str]:
    """Build a child-process env that never writes bytecode into the tree.

    Always sets `PYTHONDONTWRITEBYTECODE=1` and `PYTHONPYCACHEPREFIX`
    (mirroring the current process's value when set, else a private tmp
    dir), then applies `overrides` on top -- callers that need every other
    ambient variable should spread `os.environ` themselves and pass the
    result as `overrides`; this helper only guarantees the two isolation
    keys are present, not absent from override.
    """
    env: dict[str, str] = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": os.environ.get(
            "PYTHONPYCACHEPREFIX", _FALLBACK_PYCACHE_PREFIX
        ),
    }
    env.update(overrides)
    return env
