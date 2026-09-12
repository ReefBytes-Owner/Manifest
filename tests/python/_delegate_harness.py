"""Shared fixtures and helpers for the delegate.py CLI-subprocess tests.

Split out of test_delegate_jobs.py so the job-lifecycle tests and the
cancel/orphan-race tests can share one stub-backend harness without either file
crossing the module-size ceiling. Named `_delegate_harness` (not `conftest`) on
purpose: a sibling `conftest.py` already exists under tests/python/agents/, so a
bare `from conftest import ...` would resolve ambiguously. Test modules import
the helpers and the `env_factory` fixture explicitly from this unique module;
tests/python is on sys.path[0] under pytest's default import mode.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from _delegate_runtime_env import in_tree_trusted_python, manifest_home

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "plugins" / "manifest-delegate" / "scripts" / "delegate.py"
STUB_SRC = (
    REPO_ROOT / "tests" / "python" / "fixtures" / "stub_backends" / "stub_backend.py"
)


def _make_stub_launcher(bin_dir, name):
    """Create an executable named `name` in bin_dir that execs stub_backend.py."""
    launcher = bin_dir / name
    launcher.write_text(f'#!/bin/sh\nexec {sys.executable} {STUB_SRC} "$@"\n')
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


def _registry(entries):
    return {"backends": entries}


def _stub_entry(id_="stub", resume="default", input_transport="stdin", **overrides):
    entry = {
        "id": id_,
        "aliases": [],
        "invoke": [id_],
        "resume": [id_, "--resume", "{session_ref}"] if resume else None,
        "input": {
            "transport": input_transport,
            "max_payload_bytes": 1_000_000,
            "max_context_bytes": 1_000_000,
        },
        "sandbox": {"read_only_args": [], "write_args": []},
        "session_id_capture": {"method": "output_scan", "pattern": r"session: (\S+)"},
        "default_tier": "default",
        "version_probe": [id_, "--version"],
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def env_factory(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub_launcher(bin_dir, "stub")
    _make_stub_launcher(bin_dir, "noresume")

    delegations_dir = tmp_path / "delegations"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "delegation.json").write_text(json.dumps({"default_backend": "stub"}))
    control_path = tmp_path / "control.json"

    def _build(entries=None, control=None):
        registry_path = tmp_path / "backends.json"
        registry_path.write_text(json.dumps(_registry(entries or [_stub_entry()])))
        if control is not None:
            control_path.write_text(json.dumps(control))
        # `PYTHONPATH` dropped: `test.python`'s own honest env (Correction 9)
        # points it at the CANDIDATE's raw manifest_model_policy source so
        # in-process checks import the code under test; delegate.py, run as a
        # subprocess here, resolves that package itself through the trusted,
        # properly-installed venv `_run` already launches it with
        # (`_trusted_python`) -- an inherited raw-source entry ahead of that
        # venv's own site-packages only confuses its trust gate.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        # Own HOME (Correction 15 rule 1): delegate.py's trust gate re-execs
        # `~/.claude/.venv/bin/python` when the launcher interpreter itself
        # has no manifest-model-policy distribution (true for `_trusted_
        # python()` below -- its throwaway venv has no PyYAML, so importing
        # the policy package fails and the gate falls through to that
        # re-exec). Pointing HOME at `manifest_home()` gives that re-exec a
        # real, trusted target built from the toolchain store instead of the
        # developer's own machine.
        env["HOME"] = str(manifest_home())
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["MANIFEST_DELEGATE_REGISTRY_PATH"] = str(registry_path)
        env["MANIFEST_DELEGATIONS_DIR"] = str(delegations_dir)
        env["MANIFEST_CONFIG_DIR"] = str(config_dir)
        env["STUB_CONTROL_FILE"] = str(control_path)
        return env

    _build.delegations_dir = delegations_dir
    _build.control_path = control_path
    return _build


_trusted_python_dir: str | None = None
_trusted_python_path: Path | None = None


def _trusted_python() -> Path:
    """The interpreter delegate.py's own trust gate accepts for THIS checkout.

    `project-env`'s shared `manifest-model-policy` editable install is pinned
    at `manifest provision` time to whichever checkout provisioned it -- never
    to a `test.python` candidate materialization, a different, disposable
    checkout of the same tree. Every subprocess invocation of delegate.py in
    this suite would fail its own trust gate against that shared interpreter
    for that reason alone, unrelated to the job-lifecycle behavior these tests
    actually exercise. Built once per test process (delegate.py's trust gate
    only cares about the checkout, which does not change mid-run) and removed
    at interpreter exit via `atexit`, not a fixture teardown, so every module
    -- not only ones that opt into a fixture -- shares one build.
    """
    global _trusted_python_dir, _trusted_python_path
    if _trusted_python_path is None:
        _trusted_python_dir = tempfile.mkdtemp(prefix="manifest-delegate-trust-")
        _trusted_python_path = in_tree_trusted_python(Path(_trusted_python_dir))
        import atexit

        atexit.register(lambda: shutil.rmtree(_trusted_python_dir, ignore_errors=True))
    return _trusted_python_path


def _run(env, *args, input_text=None):
    return subprocess.run(
        [str(_trusted_python()), str(SCRIPT_PATH), *list(args)],
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _new_job_id(env_factory, known_ids=()):
    """Resolve a job id by diffing job dirs under delegations_dir."""
    delegations_dir = env_factory.delegations_dir
    current = (
        {
            p.name
            for p in delegations_dir.rglob("*")
            if p.is_dir() and (p / "record.json").exists()
        }
        if delegations_dir.exists()
        else set()
    )
    new_ids = current - set(known_ids)
    assert len(new_ids) == 1, (
        f"expected exactly one new job dir, found {new_ids!r} (known={known_ids!r})"
    )
    return next(iter(new_ids))


def _materialize_workspace(env_factory, env):
    """Run a throwaway job so the workspace dir exists, and return it."""
    assert _run(env, "task", "--background", "--json", "warm").returncode == 0
    return next(
        p.parent for p in env_factory.delegations_dir.rglob("record.json")
    ).parent


def _spawn_orphan():
    """A real setsid'd process standing in for a detached backend group."""
    proc = subprocess.Popen(["sleep", "300"], preexec_fn=os.setsid)
    return proc, os.getpgid(proc.pid)


def _kill_orphan(proc):
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def _spawn_orphan_holding_backend_lock(job_dir, publish_pgid_after=None):
    """A real setsid'd process that holds an exclusive flock on
    <job_dir>/backend.lock — standing in for a live backend the way the real
    dispatcher's preexec does. Returns (proc, pgid) once the lock is confirmed
    held. reap/cancel now verify this lock (not just the pgid) before killing,
    so a simulated orphan must hold it to be reapable.

    If `publish_pgid_after` (seconds) is set, the orphan holds the lock but
    delays writing <job_dir>/backend.pgid until then — reproducing the launch
    race where the child holds the inherited lock before it has published its
    pgid in preexec."""
    job_dir.mkdir(parents=True, exist_ok=True)
    lock_path = job_dir / "backend.lock"
    ready = job_dir / ".lock_ready"
    pgid_path = job_dir / "backend.pgid"
    if publish_pgid_after is None:
        code = (
            "import os,sys,fcntl,time\n"
            "fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600)\n"
            "fcntl.flock(fd,fcntl.LOCK_EX)\n"
            "open(sys.argv[2],'w').write('ready')\n"
            "time.sleep(300)\n"
        )
        cmd_args = [str(lock_path), str(ready)]
    else:
        # Hold the lock, signal ready, then publish backend.pgid (own group)
        # only after the delay — the race the reaper must survive.
        code = (
            "import os,sys,fcntl,time\n"
            "fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600)\n"
            "fcntl.flock(fd,fcntl.LOCK_EX)\n"
            "open(sys.argv[2],'w').write('ready')\n"
            "time.sleep(float(sys.argv[4]))\n"
            "open(sys.argv[3],'w').write(str(os.getpgid(0)))\n"
            "time.sleep(300)\n"
        )
        cmd_args = [
            str(lock_path),
            str(ready),
            str(pgid_path),
            str(publish_pgid_after),
        ]
    proc = subprocess.Popen(
        [sys.executable, "-c", code, *cmd_args],
        preexec_fn=os.setsid,
    )
    deadline = time.time() + 8
    while time.time() < deadline and not ready.exists():
        time.sleep(0.05)
    assert ready.exists(), "orphan never acquired backend.lock"
    return proc, os.getpgid(proc.pid)


def _hand_build_job(workspace_dir, job_id, state, backend_pgid):
    """Write a job record + backend.pgid file directly, simulating a job whose
    worker died before persisting its pgid. worker_pid is unreachable (2**31-1)
    so the CLI's os.kill on it is a harmless no-op (never a pid-reuse victim)."""
    job_dir = workspace_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "record.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "state": state,
                "worker_pid": 2**31 - 1,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
        )
    )
    (job_dir / "backend.pgid").write_text(str(backend_pgid))
    return job_dir
