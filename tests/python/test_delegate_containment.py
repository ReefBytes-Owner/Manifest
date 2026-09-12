"""OS-level containment of backend descendants (#740).

Two layers, deliberately separate:

* **Portable** — the degraded path, the reporting contract, and the wiring.
  These run everywhere, including macOS, where cgroups do not exist.
* **Linux integration** — the only test that proves the escape is closed. It
  spawns a double-`setsid` grandchild and asserts the reap reaches it, which is
  exactly what `killpg(recorded_pgid)` cannot do. Skipped off Linux and without
  a delegated cgroup subtree, and that skip is why the portable tests above
  exist: a suite that only ever skipped would report green having verified
  nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _delegate_inproc import delegate

containment = delegate.containment

_LINUX_CGROUP = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and containment.probe()[0]),
    reason="requires Linux with a writable cgroup v2 subtree",
)


class TestDegradedPathIsExplicit:
    """A containment promise that quietly does not apply is a false green."""

    def test_probe_reports_a_reason_even_when_unavailable(self, tmp_path):
        available, reason = containment.probe(root=str(tmp_path / "absent"))
        assert available is False
        assert reason, "probe must always explain itself, not just say no"

    def test_create_returns_degraded_and_why_without_cgroups(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        path, state, reason = containment.create(
            str(job_dir), root=str(tmp_path / "absent")
        )
        assert path is None
        assert state == containment.STATE_DEGRADED
        assert "not mounted" in reason

    def test_no_marker_is_written_when_degraded(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        containment.create(str(job_dir), root=str(tmp_path / "absent"))
        assert containment.read_path(str(job_dir)) is None

    def test_reap_is_a_no_op_when_the_job_ran_degraded(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        assert containment.reap(str(job_dir)) is False

    def test_join_hook_is_none_when_degraded(self, tmp_path):
        """The pre-exec must not pay for a cgroup that does not exist."""
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        assert containment.join_hook(str(job_dir)) is None


class TestMarkerIsCrashSafe:
    """cancel must find the cgroup even if the worker died, the same reason
    backend.pgid is written from the child rather than the parent."""

    def test_recorded_path_round_trips_through_the_job_dir(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / containment.CGROUP_DIR_FILENAME).write_text("/sys/fs/cgroup/probe")
        assert containment.read_path(str(job_dir)) == "/sys/fs/cgroup/probe"

    def test_blank_marker_reads_as_absent(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / containment.CGROUP_DIR_FILENAME).write_text("   \n")
        assert containment.read_path(str(job_dir)) is None

    def test_cleanup_tolerates_a_job_that_never_had_a_cgroup(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        containment.cleanup(str(job_dir))  # must not raise


@_LINUX_CGROUP
class TestLinuxEscapeIsClosed:
    def test_reap_kills_a_double_setsid_grandchild(self, tmp_path):
        """The whole point of #740.

        The grandchild calls setsid() twice, so its process group id was never
        recorded anywhere and `killpg(recorded_pgid)` provably cannot reach it.
        cgroup membership is inherited across fork AND setsid, so the reap must.
        """
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        path, state, _ = containment.create(str(job_dir))
        assert state == containment.STATE_CONTAINED

        script = (
            "import os, sys, time\n"
            "os.setsid()\n"
            "if os.fork() == 0:\n"
            "    os.setsid()\n"  # second detach: escapes any recorded pgid
            "    sys.stdout.write(str(os.getpid()) + '\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(300)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            text=True,
            preexec_fn=lambda: containment.join(path),
        )
        grandchild = int(proc.stdout.readline().strip())
        proc.wait(timeout=30)

        os.kill(grandchild, 0)  # alive, and outside every recorded group
        assert containment.kill(path) is True

        for _ in range(100):
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(grandchild, 9)
            pytest.fail("cgroup reap did not reach the double-setsid grandchild")

        containment.cleanup(str(job_dir))

    def test_killpg_alone_would_have_missed_it(self, tmp_path):
        """Pins the premise rather than assuming it: a double-setsid grandchild
        is genuinely unreachable from the parent's group, so the test above is
        measuring containment and not merely a lucky kill."""
        script = (
            "import os, sys, time\n"
            "os.setsid()\n"
            "sys.stdout.write(str(os.getpgid(0)) + '\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(300)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
        )
        child_pgid = int(proc.stdout.readline().strip())
        assert child_pgid != os.getpgid(0), (
            "child did not leave the parent's process group; the escape this "
            "issue describes would not be reproducible"
        )
        os.killpg(child_pgid, 9)
        proc.wait(timeout=30)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux is the containment venue"
)
def test_linux_ci_actually_exercises_containment():
    """A skip that renders as a pass is the failure this gate exists to remove.

    #862 first shipped green with BOTH Linux tests skipped on Ubuntu CI: the
    probe requires a writable subtree and `/sys/fs/cgroup` is root-owned, so the
    containment code was executed on no machine anywhere while the check went
    green. That is the exact false green the module's own docstring warns about,
    and a green Test job was not evidence of anything.

    On CI this now FAILS instead of skipping, so an unverifiable venue is loud.
    Locally on Linux it still skips, because a developer without a delegated
    subtree should not be blocked.
    """
    if not os.environ.get("CI"):
        pytest.skip("local Linux run; CI is the venue that must verify")
    available, reason = containment.probe()
    assert available, (
        f"Linux CI cannot exercise containment ({reason}). The workflow must "
        f"delegate a subtree and set {containment.CGROUP_ROOT_ENV}; without it "
        f"the escape tests skip and this job reports green having verified "
        f"nothing."
    )


def test_containment_is_wired_into_the_shared_kill_site():
    """`_kill_pgid` is the single call site cancel, timeout and the reaper share;
    if the reap is not there, contained hosts silently behave like degraded."""
    source = Path(delegate.process.__file__).read_text(encoding="utf-8")
    assert "containment.reap(" in source, "cgroup reap missing from _kill_pgid"
    assert "containment.join_hook(" in source, "pre-exec never joins the cgroup"
