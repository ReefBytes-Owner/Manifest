"""Per-run cache-directory lifecycle for check/probe child environments.

Split out of `toolchain.py` to keep it under the Code Constitution's 500-line
ceiling (Correction 4 / C7d). A check body that imports the candidate's own
`src/` writes CPython's `__pycache__` into the candidate by default; the
runner's strict identity check then (correctly) reports "candidate identity
changed" for a body that did nothing wrong except run under an interpreter
that caches bytecode where it happens to find the importing module. The fix
is not to weaken the identity check -- it is to make sure every cache a body
or probe might write lands outside the candidate, in one per-run temp
directory the runner owns, before the check ever runs.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def run_cache_directory():
    """One per-`manifest check` run temp directory for every cache-env
    override `cache_environment` writes below -- created outside the
    candidate and the repository checkout (`tempfile.mkdtemp` always makes a
    fresh sibling directory, never a path nested inside a directory the
    caller already controls) and removed unconditionally, even if a check
    raises."""
    run_tmp = Path(tempfile.mkdtemp(prefix="manifest-check-cache-"))
    try:
        yield run_tmp
    finally:
        shutil.rmtree(run_tmp, ignore_errors=True)


def cache_environment(env: Mapping[str, str], run_tmp: Path) -> dict[str, str]:
    """Every body/probe child env, with all cache locations redirected into
    `run_tmp` -- outside the candidate.

    The runner sets these, not the caller: the candidate-identity check must
    not depend on whatever the caller's ambient environment happened to
    forward. A caller that forgot `XDG_CACHE_HOME` (or set it to something
    that resolves inside the candidate) must never be able to make a check
    body write a cache file into the candidate -- that is a real defect the
    identity check exists to catch, and it must be reachable from exactly one
    place. Values here always override the caller's, they are never merged
    with or deferred to them (except `PYTEST_ADDOPTS`, which is appended to
    so a caller's own pytest options keep working).
    """
    result = dict(env)
    pycache_dir = run_tmp / "pycache"
    xdg_dir = run_tmp / "xdg"
    ruff_dir = run_tmp / "ruff"
    uv_dir = run_tmp / "uv"
    npm_dir = run_tmp / "npm"
    uv_env_dir = run_tmp / "uv-env"
    for directory in (pycache_dir, xdg_dir, ruff_dir, uv_dir, npm_dir):
        directory.mkdir(parents=True, exist_ok=True)
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    result["PYTHONPYCACHEPREFIX"] = str(pycache_dir)
    result["XDG_CACHE_HOME"] = str(xdg_dir)
    result["RUFF_CACHE_DIR"] = str(ruff_dir)
    result["UV_CACHE_DIR"] = str(uv_dir)
    result["npm_config_cache"] = str(npm_dir)
    # Correction 12 (C7k step 5b) rule 2: an escaped `uv run --project .` must
    # not be able to create `.venv` inside the candidate, nor download
    # anything. `UV_PROJECT_ENVIRONMENT` redirects the venv uv would
    # otherwise materialize at `<project>/.venv` into run-tmp instead
    # (outside the candidate); `UV_NO_SYNC` forbids uv from installing or
    # syncing regardless. This is a belt-and-braces backstop, not the
    # primary fix -- the primary fix is that no test invokes `uv run
    # --project .`/`uv sync` against the candidate at all (rule 1).
    result["UV_PROJECT_ENVIRONMENT"] = str(uv_env_dir)
    result["UV_NO_SYNC"] = "1"
    # A body's own outputs (never just its caches) belong outside the
    # candidate too -- e.g. `manifest smoke run`'s JUnit report, which
    # otherwise lands in cwd (the candidate) and trips the identity check.
    # `run_tmp` itself (not a subdirectory) so a body picks its own layout.
    result["MANIFEST_RUN_TMP"] = str(run_tmp)
    existing_addopts = result.get("PYTEST_ADDOPTS", "")
    no_cacheprovider = "-p no:cacheprovider"
    result["PYTEST_ADDOPTS"] = (
        f"{existing_addopts} {no_cacheprovider}"
        if existing_addopts
        else no_cacheprovider
    )
    return result


def scratch_home_environment(env: Mapping[str, str], check_id: str) -> dict[str, str]:
    """A check's env with `HOME` (and the XDG dirs derived from it) redirected
    into a fresh, empty per-check directory under the run's cache directory.

    Correction 14 (C7o) rule 1: a check body must never resolve the caller's
    real `~/.claude` config (or any other ambient home-directory state) --
    bats 1442 did exactly that, reaching a real reviewer CLI from inside a
    test. `MANIFEST_RUN_TMP` (set by `cache_environment` above) is reused as
    the parent so this stays inside the one run-scoped temp directory the
    runner already owns and removes; the per-check id keeps two `scratch_home`
    checks in the same run from ever sharing a HOME.
    """
    home_dir = Path(env["MANIFEST_RUN_TMP"]) / "home" / check_id
    config_dir = home_dir / "xdg-config"
    data_dir = home_dir / "xdg-data"
    state_dir = home_dir / "xdg-state"
    for directory in (home_dir, config_dir, data_dir, state_dir):
        directory.mkdir(parents=True, exist_ok=True)
    result = dict(env)
    result["HOME"] = str(home_dir)
    result["XDG_CONFIG_HOME"] = str(config_dir)
    result["XDG_DATA_HOME"] = str(data_dir)
    result["XDG_STATE_HOME"] = str(state_dir)
    return result
