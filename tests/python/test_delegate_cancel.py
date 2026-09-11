"""Cancel and orphaned-backend-reaping race coverage for delegate.py.

Split out of test_delegate_jobs.py: these tests drive real setsid'd process
groups to prove that cancelling a job leaves no write-capable backend running,
across every race window (pgid recorded, pgid persisted only in the crash-safe
backend.pgid file after a pre-persist worker death, and the once-only reap of a
cancelled job's orphan). Shared harness lives in _delegate_harness.py.
"""

import json
import os
import subprocess
import time

from _delegate_harness import (
    SCRIPT_PATH,
    _hand_build_job,
    _kill_orphan,
    _materialize_workspace,
    _run,
    _spawn_orphan_holding_backend_lock,
    _trusted_python,
)


class TestBackendDrainBound:
    def test_detached_descendant_holding_stdout_does_not_hang_past_budget(
        self, env_factory
    ):
        """K1: a backend that exits immediately but leaves a detached descendant
        holding the stdout pipe open must not hang the dispatcher's drain past
        its budget. The descendant outlives the harness's 30s subprocess timeout,
        so the pre-fix unbounded reader.join would exceed it (TimeoutExpired);
        the bounded grace-join + pipe-close returns in ~5s with a valid result."""
        env = env_factory(
            control={
                "detached_holder_secs": 45,
                "envelope": {
                    "backend": "stub",
                    "model": "default",
                    "outcome": "success",
                    "attempted": "x",
                    "changes": [],
                    "succeeded": [],
                    "failed": [],
                    "follow_ups": [],
                },
            }
        )
        start = time.time()
        result = _run(env, "task", "--json", "hi")  # _run has timeout=30
        assert time.time() - start < 25, (
            "backend drain hung near/over the harness timeout"
        )
        assert result.returncode in (0, 1), result.stderr

    def test_drain_grace_kills_in_group_descendant_holding_stdout(self, env_factory):
        """Codex HIGH: when the backend exits but an IN-GROUP descendant holds
        stdout past the drain grace, the dispatcher must SIGKILL the recorded
        process group. Otherwise that write-capable child runs on with NO cancel
        path — the job goes terminal `timeout`, which cancel/reap treat as a
        no-op. A holder in the backend's own group stands in for a runaway child.
        Mutation check: revert the killpg on the drain-grace branch and the pgid
        below stays alive, failing this test."""
        env = env_factory(
            control={
                "detached_holder_secs": 45,
                "holder_in_group": True,
                "envelope": {
                    "backend": "stub",
                    "model": "default",
                    "outcome": "success",
                    "attempted": "x",
                    "changes": [],
                    "succeeded": [],
                    "failed": [],
                    "follow_ups": [],
                },
            }
        )
        result = _run(env, "task", "--json", "hi")
        assert result.returncode in (0, 1), result.stderr
        record = next(env_factory.delegations_dir.rglob("record.json"))
        pgid = json.loads(record.read_text()).get("pgid")
        assert pgid, "backend pgid was never recorded"
        dead, deadline = False, time.time() + 5
        while time.time() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                dead = True
                break
            time.sleep(0.2)
        assert dead, (
            "in-group stdout-holding descendant survived the drain grace "
            f"(pgid {pgid} still alive) — no cancellation path for a terminal timeout"
        )


class TestForegroundOwnership:
    def test_foreground_job_survives_concurrent_reap_past_grace(self, env_factory):
        """M1 (codex round 3): a long foreground delegation must hold the worker
        lifetime lock so a concurrent status/SessionEnd reap (from another
        session sharing the workspace), firing after the startup grace, does NOT
        see it as dead and kill it. The backend sleeps past the grace; midway a
        separate `status` call triggers reap_if_dead; the task must still finish
        successfully. Pre-fix (no foreground lock) the reap killed it → failed."""
        # grace is 15s; sleep 20s so the reap at ~16s lands after the grace.
        env = env_factory(
            control={
                "sleep": 20,
                "envelope": {
                    "backend": "stub",
                    "model": "default",
                    "outcome": "success",
                    "attempted": "x",
                    "changes": [],
                    "succeeded": [],
                    "failed": [],
                    "follow_ups": [],
                },
            }
        )
        proc = subprocess.Popen(
            [str(_trusted_python()), str(SCRIPT_PATH), "task", "--json", "hi"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # Recover the job id from the record dir (created at task start).
            job_id, deadline = None, time.time() + 8
            while time.time() < deadline and job_id is None:
                recs = list(env_factory.delegations_dir.rglob("record.json"))
                if recs:
                    job_id = recs[0].parent.name
                time.sleep(0.3)
            assert job_id, "foreground job record never appeared"

            time.sleep(16)  # let the job age past WORKER_STARTUP_GRACE_SECONDS (15s)
            reap = _run(env, "status", job_id, "--json")  # triggers reap_if_dead
            assert reap.returncode == 0
            assert json.loads(reap.stdout)["state"] == "running", (
                "reap failed a live foreground job"
            )

            out, err = proc.communicate(timeout=20)
            assert proc.returncode == 0, err
            assert json.loads(out)["outcome"] == "success"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


class TestForegroundCancelSafety:
    def test_cancel_of_foreground_job_does_not_kill_the_cli_process(self, env_factory):
        """M1-followup (codex/QA): cancelling a foreground job from another
        session must kill only its backend group, NOT SIGKILL the interactive
        CLI running it (whose pid is now recorded as worker_pid). The CLI must
        exit cleanly (not returncode -SIGKILL) with the job cancelled."""
        env = env_factory(
            control={
                "sleep": 20,
                "envelope": {
                    "backend": "stub",
                    "model": "default",
                    "outcome": "success",
                    "attempted": "x",
                    "changes": [],
                    "succeeded": [],
                    "failed": [],
                    "follow_ups": [],
                },
            }
        )
        proc = subprocess.Popen(
            [str(_trusted_python()), str(SCRIPT_PATH), "task", "--json", "hi"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            job_id, deadline = None, time.time() + 8
            while time.time() < deadline and job_id is None:
                recs = list(env_factory.delegations_dir.rglob("record.json"))
                if recs:
                    job_id = recs[0].parent.name
                time.sleep(0.3)
            assert job_id, "foreground job record never appeared"
            time.sleep(1)  # ensure the backend is running under the CLI

            assert _run(env, "cancel", job_id, "--json").returncode == 0
            rc = proc.wait(timeout=20)
            # The CLI must NOT have been SIGKILLed (-9); it exits on its own.
            assert rc != -9, "cancel SIGKILLed the foreground CLI process"
            assert rc is not None and rc >= 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


class TestCancelOrphanReaping:
    def test_cancel_leaves_no_orphaned_backend_process_group(self, env_factory):
        """G1 residual: cancelling a running job must actually kill the
        detached (setsid) backend process group, not just flip the record to
        'cancelled'. Proves no write-capable backend survives the cancel."""
        env = env_factory(
            control={
                "sleep": 30,
                "envelope": {
                    "backend": "stub",
                    "model": "default",
                    "outcome": "success",
                    "attempted": "x",
                    "changes": [],
                    "succeeded": [],
                    "failed": [],
                    "follow_ups": [],
                },
            }
        )
        result = _run(env, "task", "--background", "--json", "hi")
        job_id = json.loads(result.stdout)["job_id"]

        # Wait until the backend has spawned and its pgid is recorded.
        pgid = None
        deadline = time.time() + 10
        while time.time() < deadline:
            status = json.loads(_run(env, "status", job_id, "--json").stdout)
            if status.get("state") == "running" and status.get("pgid"):
                pgid = status["pgid"]
                break
            time.sleep(0.2)
        assert pgid, "backend pgid never recorded"
        os.killpg(pgid, 0)  # alive now (raises if not)

        cancel = _run(env, "cancel", job_id, "--json")
        assert cancel.returncode == 0, cancel.stderr

        # The backend process group must die — no orphan.
        died = False
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                died = True
                break
            time.sleep(0.2)
        assert died, f"backend pgid {pgid} survived cancel (orphaned)"

    def test_cancel_does_not_kill_a_recycled_worker_pid(self, env_factory):
        """J1: cancel must not SIGKILL record['worker_pid'] unless the lifetime
        worker.lock proves the pid is still OUR worker. Simulated: an innocent
        live process stands in for a pid the OS recycled after our worker died;
        the job record carries that pid but NO worker.lock, so _worker_alive is
        False and cancel must leave the innocent process untouched. On iter-8
        code (unconditional os.kill(worker_pid)) this killed the innocent."""
        env = env_factory()
        workspace_dir = _materialize_workspace(env_factory, env)
        innocent = subprocess.Popen(["sleep", "300"])  # the recycled pid's new owner
        try:
            job_id = "feedface" * 4
            job_dir = workspace_dir / job_id
            job_dir.mkdir(parents=True)
            (job_dir / "record.json").write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "state": "running",
                        "worker_pid": innocent.pid,
                        "created_at": time.time(),
                        "updated_at": time.time(),
                    }
                )
            )
            # Deliberately NO worker.lock file → worker is not confirmably ours.
            assert _run(env, "cancel", job_id, "--json").returncode == 0

            # The innocent process must still be alive — cancel did not signal it.
            assert innocent.poll() is None, "cancel SIGKILLed a recycled worker pid"
        finally:
            innocent.kill()
            innocent.wait()

    def test_cancel_clears_pgid_tracking_so_no_stale_pgid_remains(self, env_factory):
        """I8-A: the ordinary cancel path must erase pgid tracking after killing
        the backend, so record.json keeps NO stale non-null pgid. Otherwise a
        later status/reap could SIGKILL a process group the OS recycled onto the
        dead pgid number. Fails on iter-7 code (cmd_cancel left the pgid set)."""
        env = env_factory(
            control={
                "sleep": 30,
                "envelope": {
                    "backend": "stub",
                    "model": "default",
                    "outcome": "success",
                    "attempted": "x",
                    "changes": [],
                    "succeeded": [],
                    "failed": [],
                    "follow_ups": [],
                },
            }
        )
        job_id = json.loads(_run(env, "task", "--background", "--json", "hi").stdout)[
            "job_id"
        ]

        # Wait until the pgid is recorded (backend running).
        deadline = time.time() + 10
        while time.time() < deadline:
            if json.loads(_run(env, "status", job_id, "--json").stdout).get("pgid"):
                break
            time.sleep(0.2)

        assert _run(env, "cancel", job_id, "--json").returncode == 0
        job_dir = next(
            p.parent
            for p in env_factory.delegations_dir.rglob("record.json")
            if p.parent.name == job_id
        )
        assert json.loads((job_dir / "record.json").read_text()).get("pgid") is None, (
            "cancel left a stale pgid in the record (recycled-pgid wrong-kill risk)"
        )
        assert not (job_dir / "backend.pgid").exists(), (
            "cancel left the crash-safe pgid file"
        )

    def test_cancel_kills_orphan_via_pgid_file_when_worker_died_pre_persist(
        self, env_factory
    ):
        """I6-A: the worst race — the worker is SIGKILLed between Popen() (backend
        forked+setsid, its group already live) and the on_pgid persist, so the
        pgid never reaches record.json. The forked child wrote its group id to
        <job_dir>/backend.pgid in preexec, so cancel must still find and kill the
        orphan. Simulated deterministically: a real setsid'd 'sleep' stands in for
        the orphaned backend, a hand-built running record has NO pgid and a dead
        worker_pid, and only backend.pgid carries the group id."""
        env = env_factory()
        workspace_dir = _materialize_workspace(env_factory, env)
        job_dir = workspace_dir / ("deadf00d" * 4)
        orphan, orphan_pgid = _spawn_orphan_holding_backend_lock(job_dir)
        try:
            _hand_build_job(workspace_dir, job_dir.name, "running", orphan_pgid)

            cancel = _run(env, "cancel", job_dir.name, "--json")
            assert cancel.returncode == 0, cancel.stderr
            assert json.loads(cancel.stdout)["state"] == "cancelled"

            # Observe the orphan's own handle (no pgid-reuse ambiguity): if cancel
            # killed the group, our sleep child is reaped.
            try:
                orphan.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise AssertionError(
                    f"orphaned backend (pgid {orphan_pgid}) survived cancel — pgid-file fallback failed"
                ) from exc
        finally:
            _kill_orphan(orphan)

    def test_recycled_backend_pgid_survives_reap_and_cancel(self, env_factory):
        """O1 (codex round 4): the backend pgid now has flock identity. A job
        recording a pgid whose backend already died (no backend.lock held) must
        NOT be killed by reap or cancel — the recorded number may have been
        recycled to an unrelated group. An innocent live group stands in for the
        recycled pgid; it must survive. Pre-fix (killpg on _pgid_alive) killed it."""
        innocent = subprocess.Popen(["sleep", "300"], preexec_fn=os.setsid)
        innocent_pgid = os.getpgid(innocent.pid)
        try:
            env = env_factory()
            workspace_dir = _materialize_workspace(env_factory, env)
            job_id = "b16b00b5" * 4
            job_dir = workspace_dir / job_id
            job_dir.mkdir(parents=True)
            # running, aged past grace, dead worker, pgid = the innocent group,
            # and NO backend.lock (the real backend is "gone", pgid recycled).
            (job_dir / "record.json").write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "state": "running",
                        "worker_pid": 2**31 - 1,
                        "pgid": innocent_pgid,
                        "created_at": time.time() - 100,
                        "updated_at": time.time(),
                    }
                )
            )
            _run(env, "status", job_id, "--json")  # triggers reap_if_dead
            _run(env, "cancel", job_id, "--json")  # triggers _terminate_job_processes
            time.sleep(0.5)
            assert innocent.poll() is None, (
                "reap/cancel SIGKILLed a recycled (unrelated) pgid"
            )
        finally:
            innocent.kill()
            innocent.wait()

    def test_cancelled_orphan_reap_is_once_only_then_stops_probing(self, env_factory):
        """I7-A: reap of a cancelled job's orphan backend must fire AT MOST ONCE.
        After the first reap (triggered by `status`), the pgid is cleared from
        the record and backend.pgid is deleted, so no later status/reap call can
        re-probe or SIGKILL a (possibly recycled) pgid. On iteration-6 code this
        branch re-probed indefinitely."""
        env = env_factory()
        workspace_dir = _materialize_workspace(env_factory, env)
        job_dir = workspace_dir / ("cafebabe" * 4)
        orphan, orphan_pgid = _spawn_orphan_holding_backend_lock(job_dir)
        try:
            _hand_build_job(workspace_dir, job_dir.name, "cancelled", orphan_pgid)
            pgid_file = job_dir / "backend.pgid"

            # First status triggers the one-shot reap: orphan dies.
            assert _run(env, "status", job_dir.name, "--json").returncode == 0
            try:
                orphan.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise AssertionError("orphan survived the first reap") from exc

            # One-shot cleanup: pgid cleared from record AND the file removed, so
            # subsequent reaps have nothing to probe (no recycled-pgid wrong-kill).
            assert not pgid_file.exists(), "backend.pgid not removed after reap"
            assert json.loads((job_dir / "record.json").read_text()).get("pgid") is None

            # A second status must be a clean no-op (record already has no pgid).
            second = _run(env, "status", job_dir.name, "--json")
            assert second.returncode == 0
            assert json.loads(second.stdout)["state"] == "cancelled"
        finally:
            _kill_orphan(orphan)
