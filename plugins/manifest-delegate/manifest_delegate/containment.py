"""OS-level containment for backend descendants that escape the process group.

`killpg(recorded_pgid)` reaches only processes still in the backend's group. A
descendant that calls `setsid()` gets a new session whose group id was never
recorded, so it survives cancel, timeout and the drain-grace kill -- still
holding the workspace and consuming quota (#740).

cgroup v2 closes that by construction: membership is inherited across `fork`
AND `setsid`, so a process cannot leave its cgroup by detaching. `cgroup.kill`
(kernel >= 5.14) then reaps every member in one write, regardless of session.

DEGRADED, NEVER SILENT. Containment requires a writable cgroup v2 subtree,
which macOS never has and many containers do not delegate. Callers get an
explicit state -- `contained` or `degraded` -- rather than a boolean that reads
as a guarantee. A containment promise that quietly does not apply is the
false-green shape this repository gates against elsewhere.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal

from . import constants

CGROUP_ROOT_ENV = "MANIFEST_CGROUP_ROOT"
_DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"


def cgroup_root() -> str:
    """The delegated subtree to use, or the unified root when none is set.

    A delegated subtree is the normal shape in both production and CI: the
    cgroup root is owned by root, so an unprivileged process is handed a
    chowned child instead. Without this override the probe could only ever
    succeed as root, and would silently report degraded everywhere else --
    which is precisely how the Linux test skipped on Ubuntu CI while the job
    reported green.
    """
    return os.environ.get(CGROUP_ROOT_ENV) or _DEFAULT_CGROUP_ROOT


CGROUP_ROOT = _DEFAULT_CGROUP_ROOT
CGROUP_DIR_FILENAME = "backend.cgroup"

STATE_CONTAINED = "contained"
STATE_DEGRADED = "degraded"


def _controllers_path(base: str) -> str:
    return os.path.join(base, "cgroup.controllers")


def probe(root: str | None = None) -> tuple[bool, str]:
    """Return (available, reason). Reason is always populated, for reporting.

    Three conditions, checked in the order they fail on real systems: the
    unified hierarchy must be mounted (absent on macOS and on cgroup v1 hosts),
    the subtree must be writable (a container without a delegated subtree is
    the common case), and the kernel must expose `cgroup.kill` -- without it a
    reap would have to walk `cgroup.procs`, which races a forking descendant.
    """
    root = root or cgroup_root()
    if not os.path.isfile(_controllers_path(root)):
        return False, "cgroup v2 unified hierarchy not mounted"
    if not os.access(root, os.W_OK):
        return False, f"{root} is not writable (no delegated subtree)"
    return True, "cgroup v2 with cgroup.kill"


def create(job_dir: str, root: str | None = None) -> tuple[str | None, str, str]:
    """Create a per-job cgroup and record its path. Returns (path, state, reason).

    `reason` is populated in every outcome, including success, so a degraded run
    can say WHY -- an operator cannot otherwise distinguish "no cgroups on this
    host" from "kernel too old for cgroup.kill".

    The path is written to <job_dir>/backend.cgroup so cancel can find it even
    if the worker died -- the same crash-safe pattern backend.pgid uses for the
    process group.
    """
    root = root or cgroup_root()
    available, reason = probe(root)
    if not available:
        return None, STATE_DEGRADED, reason
    path = os.path.join(root, f"manifest-delegate-{os.path.basename(job_dir)}")
    try:
        os.makedirs(path, exist_ok=True)
        if not os.path.isfile(os.path.join(path, "cgroup.kill")):
            # Directory created but the kernel is too old to reap atomically.
            _rmdir_quiet(path)
            return None, STATE_DEGRADED, "kernel lacks cgroup.kill"
        with open(os.path.join(job_dir, CGROUP_DIR_FILENAME), "w") as handle:
            handle.write(path)
        return path, STATE_CONTAINED, reason
    except OSError as exc:
        return None, STATE_DEGRADED, f"cgroup setup failed: {exc}"


def join(path: str) -> None:
    """Move the CALLING process into `path`. Used from the child pre-exec.

    Writing to cgroup.procs moves the writer; every later fork and setsid
    inherits the membership, which is the property killpg lacks.
    """
    with open(os.path.join(path, "cgroup.procs"), "w") as handle:
        handle.write(str(os.getpid()))


def read_path(job_dir: str) -> str | None:
    """The cgroup recorded for this job, or None when it ran degraded."""
    try:
        with open(os.path.join(job_dir, CGROUP_DIR_FILENAME)) as handle:
            recorded = handle.read().strip()
    except OSError:
        return None
    return recorded or None


def kill(path: str) -> bool:
    """Reap every process in the cgroup. True when the write succeeded.

    One write, no PID enumeration: a descendant forking while we walked
    cgroup.procs would otherwise be missed, which is the race this exists to
    remove.
    """
    try:
        with open(os.path.join(path, "cgroup.kill"), "w") as handle:
            handle.write("1")
        return True
    except OSError:
        return False


def cleanup(job_dir: str) -> None:
    """Remove the job's cgroup directory once its members are gone."""
    path = read_path(job_dir)
    if path:
        _rmdir_quiet(path)
    marker = os.path.join(job_dir, CGROUP_DIR_FILENAME)
    try:
        os.unlink(marker)
    # constitution: exempt C-ERR -- an absent marker IS the expected state for a
    # degraded run (one is never written) and for a second cleanup pass; there is
    # nothing lost to report.
    except FileNotFoundError:
        pass
    except OSError as exc:
        constants.err(f"job dir {job_dir}: failed to remove {marker}: {exc}")


def _rmdir_quiet(path: str) -> None:
    """rmdir that tolerates a still-populated or already-absent cgroup."""
    try:
        os.rmdir(path)
    except OSError:
        shutil.rmtree(path, ignore_errors=True)


def join_hook(job_dir: str):
    """Return a zero-arg callable for the child's pre-exec, or None.

    Lives here rather than in process.py for two reasons: the cgroup branching
    belongs with the cgroup code, and process.py sits at its CON-002 size
    ceiling, so call sites there must stay one line.

    The returned callable swallows OSError deliberately. It runs post-fork and
    pre-exec with no stdio to report through, and the degraded state was already
    recorded by create() at launch -- so a failed join costs the job its
    descendant guarantee, which the operator has already been told about, and
    nothing else.
    """
    path = read_path(job_dir)
    if not path:
        return None

    def _join():
        # suppress, not a bare except: pre-exec has no reporting channel, and the
        # degraded outcome was recorded by create() before launch, so a failed
        # join costs the descendant guarantee the operator was already told about.
        with contextlib.suppress(OSError):
            join(path)

    return _join


def reap(job_dir: str) -> bool:
    """Kill the job's cgroup if it has one. False when it ran degraded.

    Callers pair this with killpg: the cgroup reaches descendants that setsid()'d
    out of the recorded group, and killpg remains the whole story on a host
    without a delegated subtree.
    """
    path = read_path(job_dir)
    return kill(path) if path else False


def kill_stdout_holder(pgid, proc, job_dir):
    """SIGKILL the descendant still holding stdout after the drain grace.

    Without this, a write-capable orphan outlives its job with NO cancellation
    path — the job becomes terminal `timeout`, which cancel/reap treat as a
    no-op. This is the one place that can still reach it.

    Lives here because its former LIMITATION note described exactly the escape
    this module closes: killpg reaches only descendants still in the backend's
    group, and one that setsid()s into its own escapes it. On a contained host
    the reap below covers that; on a degraded host killpg is still the whole
    story, which is why both run.
    """
    reap(job_dir)  # no-op when degraded
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError as exc:
        constants.err(
            f"job dir {job_dir}: failed to kill stdout-holding descendant "
            f"pgid {pgid}: {exc}"
        )
    else:
        proc.wait()
