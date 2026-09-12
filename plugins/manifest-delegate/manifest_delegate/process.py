"""manifest-delegate: process."""

import contextlib
import fcntl
import os
import signal
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass

from . import backend, constants, containment
from .process_capture import (
    DRAIN_GRACE_SECONDS,
    MAX_CAPTURED_OUTPUT_BYTES,
    MAX_FAILURE_STREAM_BYTES,
    SESSION_CAPTURE_HEAD_BYTES,
    STDOUT_CAPTURE_TAIL_BYTES,
    _BoundedHead,
    _BoundedTail,
    _drain_into,
    _feed_stdin,
)

BACKEND_PGID_FILENAME = "backend.pgid"
BACKEND_LOCK_FILENAME = "backend.lock"
WORKER_LOCK_FILENAME = "worker.lock"
WORKER_IDENTITY_FILENAME = "worker.identity"

# Worker-held lock fds, kept open for the worker process's lifetime so the
# kernel releases them only on exit. Never closed explicitly.
_WORKER_LOCK_FDS = []
_WORKER_LOCK_PATHS = set()


def _acquire_worker_lifetime_lock(job_dir):
    """Called once at worker startup: hold an exclusive flock on worker.lock for
    the whole process lifetime. Because the kernel releases an flock only when
    the holding process exits, the lock's held-ness is a pid-reuse-proof liveness
    signal — unlike os.kill(pid, 0), which a recycled pid would answer for."""
    lock_path = os.path.join(job_dir, WORKER_LOCK_FILENAME)
    if lock_path in _WORKER_LOCK_PATHS:
        return
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    _WORKER_LOCK_FDS.append(fd)
    _WORKER_LOCK_PATHS.add(lock_path)


def _publish_worker_identity(job_dir, identity):
    """Publish the launch nonce only after the worker lifetime lock is held."""
    path = os.path.join(job_dir, WORKER_IDENTITY_FILENAME)
    descriptor, temporary = tempfile.mkstemp(prefix=".worker-identity.", dir=job_dir)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            descriptor = -1
            stream.write(identity)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(job_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


def _spawned_worker_gone(store, job_id, record):
    """Prove an identified spawned worker acquired ownership and has exited."""
    dispatch = record.get("dispatch")
    if not isinstance(dispatch, dict):
        return False
    pid = dispatch.get("pid")
    pgid = dispatch.get("pgid")
    identity = dispatch.get("process_start_identity")
    if (
        not isinstance(pid, int)
        or pid <= 0
        or not isinstance(pgid, int)
        or pgid <= 0
        or not isinstance(identity, str)
        or not identity
    ):
        return False
    try:
        with open(
            os.path.join(store.job_dir(job_id), WORKER_IDENTITY_FILENAME),
            encoding="ascii",
        ) as stream:
            published = stream.read()
    except OSError:
        return False
    return published == identity and not _worker_alive(store, job_id, record)


def _terminate_and_reap_worker(proc):
    """Terminate a detached worker group and prove the launched process exited."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        return _wait_for_worker_exit(proc)
    except OSError:
        return False
    return _wait_for_worker_exit(proc)


def _wait_for_worker_exit(proc):
    try:
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.poll() is not None


def _worker_alive(store, job_id, record):
    """True iff THIS job's worker process is still running, proven by the
    worker.lock flock rather than os.kill(worker_pid, 0) — so a recycled pid can
    never be mistaken for a live worker. A missing lock file means the worker was
    recorded but has not yet acquired its lock (a sub-millisecond startup window);
    treated as not-confirmably-alive, which is safe because the atomic
    queued->running claim independently stops a cancelled job's backend."""
    if not record.get("worker_pid"):
        return False
    lock_path = os.path.join(store.job_dir(job_id), WORKER_LOCK_FILENAME)
    if not os.path.exists(lock_path):
        return False
    fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)  # acquired ⇒ no live worker holds it ⇒ dead
        return False
    except OSError:
        return True  # EWOULDBLOCK/EAGAIN ⇒ our worker still holds it
    finally:
        os.close(fd)


def _backend_preexec(job_dir):
    """Return a child preexec_fn that starts a new session (so the backend gets
    its own process group for clean timeout kills) AND writes that group id to
    <job_dir>/backend.pgid before exec. The write happens in the forked child,
    so the pgid is recoverable even if the parent worker is SIGKILLed in the
    window between Popen() returning and the parent's on_pgid persist — closing
    the pre-persist orphan race. Runs post-fork/pre-exec: uses only raw syscalls
    (async-signal-safe-ish), reports nothing (no stdio) and never raises out."""
    pgid_path = os.path.join(job_dir, BACKEND_PGID_FILENAME)

    join_cgroup = containment.join_hook(job_dir)

    def _preexec():
        if join_cgroup:  # before setsid: membership survives fork AND setsid
            join_cgroup()
        os.setsid()
        fd = os.open(pgid_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        os.write(fd, str(os.getpgid(0)).encode("ascii"))
        os.close(fd)

    return _preexec


def _backend_alive(store, job_id):
    """True iff this job's backend process group is still running, proven by the
    backend.lock flock (held for the backend's lifetime) rather than
    os.killpg(pgid, 0) — so a recycled pgid is never mistaken for a live backend.
    A missing lock file means no backend is currently holding it (never started,
    or already exited): not confirmably alive."""
    lock_path = os.path.join(store.job_dir(job_id), BACKEND_LOCK_FILENAME)
    if not os.path.exists(lock_path):
        return False
    fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)  # acquired ⇒ no live backend holds it ⇒ dead
        return False
    except OSError:
        return True  # EWOULDBLOCK/EAGAIN ⇒ the backend still holds it
    finally:
        os.close(fd)


def _read_pgid_file(job_dir):
    """Crash-safe fallback: the backend pgid the child wrote in preexec, or None.
    Used by cancel/reap when the worker died before persisting pgid to the record."""
    try:
        with open(os.path.join(job_dir, BACKEND_PGID_FILENAME), encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


WORKER_STARTUP_GRACE_SECONDS = 15  # a job younger than this is "starting", not dead


def _safe_output_path(path, job_dir, *, allow_missing=False):
    root = os.path.realpath(job_dir)
    if os.path.dirname(os.path.realpath(path)) != root:
        raise ValueError("backend output path escaped the job directory")
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return root
        raise
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("backend output path is not a safe regular file")
    return root


def _replace_owned_output(path, job_dir, content=b""):
    root = _safe_output_path(path, job_dir, allow_missing=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".output.", dir=root)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _read_bounded_file(path, cap, job_dir):
    """Acquire at most ``cap + 1`` bytes and report whether the file overflowed."""
    try:
        _safe_output_path(path, job_dir)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError("backend output path is not a safe regular file")
        with os.fdopen(descriptor, "rb") as fh:
            data = fh.read(cap + 1)
        return data[:cap].decode("utf-8", errors="replace"), len(data) > cap
    except (OSError, ValueError) as exc:
        constants.err(f"job dir: failed to read bounded output {path}: {exc}")
        return "", True


def _launch_backend(argv, transport, job_dir):
    """Popen the backend with its own session (setsid) holding a lifetime flock.
    The lock fd is opened in the parent and passed via pass_fds (so close_fds
    does not close it); preexec flocks it in the child. Returns (proc, pgid)."""
    stdin_arg = subprocess.PIPE if transport == "stdin" else subprocess.DEVNULL
    # Flock in the PARENT before Popen so there is NO fork->flock window: the
    # child inherits the already-locked open-file description via pass_fds (an
    # flock lives on the description, shared across fork), so backend.lock is held
    # continuously from the instant Popen returns until the backend exits. The
    # parent then closes its copy; the child keeps the description (and the lock).
    lock_fd = os.open(
        os.path.join(job_dir, BACKEND_LOCK_FILENAME), os.O_CREAT | os.O_RDWR, 0o600
    )
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        proc = subprocess.Popen(
            argv,
            stdin=stdin_arg,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=_backend_preexec(job_dir),
            pass_fds=(lock_fd,),
        )
    finally:
        os.close(lock_fd)  # child holds the locked description; parent's copy is done
    try:
        return proc, os.getpgid(proc.pid)
    except OSError:
        return proc, proc.pid


@dataclass
class _Capture:
    head: _BoundedHead
    tail: _BoundedTail
    stderr_tail: _BoundedTail
    reader: threading.Thread
    stderr_reader: threading.Thread


def _log_backend_invocation(entry, argv, prompt_bytes, job_dir):
    logged_argv = list(argv)
    if (entry.get("input") or {}).get("transport") == "argv":
        try:
            prompt = prompt_bytes.decode("utf-8")
        except UnicodeDecodeError:
            prompt = None
        if prompt:
            logged_argv = ["<prompt>" if token == prompt else token for token in argv]
    with open(os.path.join(job_dir, "job.log"), "a", encoding="utf-8") as log_fh:
        log_fh.write("invoke: {}\n".format(" ".join(logged_argv)))


def _start_capture(proc, transport, prompt_bytes):
    tail = _BoundedTail(STDOUT_CAPTURE_TAIL_BYTES)
    stderr_tail = _BoundedTail(MAX_FAILURE_STREAM_BYTES)
    head = _BoundedHead(SESSION_CAPTURE_HEAD_BYTES)
    reader = threading.Thread(
        target=_drain_into, args=(proc.stdout, (head, tail)), daemon=True
    )
    stderr_reader = threading.Thread(
        target=_drain_into, args=(proc.stderr, (stderr_tail,)), daemon=True
    )
    reader.start()
    stderr_reader.start()
    if transport == "stdin":
        threading.Thread(
            target=_feed_stdin, args=(proc.stdin, prompt_bytes), daemon=True
        ).start()
    return _Capture(head, tail, stderr_tail, reader, stderr_reader)


def _wait_backend(proc, pgid, budget, job_dir):
    timed_out = False
    try:
        proc.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError as exc:
            constants.err(
                f"job dir {job_dir}: failed to kill timed-out pgid {pgid}: {exc}"
            )
        proc.wait()
    return timed_out


def _collect_capture(entry, capture, proc, pgid, job_dir, stdout_path, timed_out):
    capture.reader.join(DRAIN_GRACE_SECONDS)
    capture.stderr_reader.join(DRAIN_GRACE_SECONDS)
    if capture.reader.is_alive() or capture.stderr_reader.is_alive():
        containment.kill_stdout_holder(pgid, proc, job_dir)
        timed_out = True
    head_bytes = capture.head.value()
    tail_bytes = capture.tail.value()
    stdout_total = capture.head.total_bytes
    if stdout_total <= MAX_CAPTURED_OUTPUT_BYTES:
        overlap = max(0, len(head_bytes) + len(tail_bytes) - stdout_total)
        stdout_bytes = head_bytes + tail_bytes[overlap:]
    else:
        stdout_bytes = head_bytes + tail_bytes
    raw = stdout_bytes.decode("utf-8", errors="replace")
    stderr = capture.stderr_tail.value().decode("utf-8", errors="replace")
    output_content, output_truncated = _read_bounded_file(
        stdout_path, MAX_CAPTURED_OUTPUT_BYTES, job_dir
    )
    combined = (output_content + "\n" + raw) if output_content else raw
    session_ref = backend.extract_session_ref(
        entry, head_bytes.decode("utf-8", errors="replace")
    ) or backend.extract_session_ref(entry, combined)
    truncated = (
        stdout_total > MAX_CAPTURED_OUTPUT_BYTES
        or capture.stderr_tail.truncated
        or output_truncated
    )
    return proc.returncode, combined, stderr, pgid, timed_out, session_ref, truncated


def _spawn_backend(entry, argv, prompt_bytes, job_dir, budget, on_pgid=None):
    stdout_path = os.path.join(job_dir, "output.txt")
    _replace_owned_output(stdout_path, job_dir)
    _log_backend_invocation(entry, argv, prompt_bytes, job_dir)
    transport = (entry.get("input") or {}).get("transport", "stdin")
    proc, pgid = _launch_backend(argv, transport, job_dir)
    if on_pgid:
        on_pgid(pgid)
    capture = _start_capture(proc, transport, prompt_bytes)
    return _collect_capture(
        entry,
        capture,
        proc,
        pgid,
        job_dir,
        stdout_path,
        _wait_backend(proc, pgid, budget, job_dir),
    )


def _kill_pgid(store, job_id, pgid):
    """Best-effort SIGKILL of a backend process group. Shared by the cancel
    path, the reaper, and the worker's cancel-during-fork guard so there is one
    killpg call site with one error-reporting convention."""
    containment.reap(store.job_dir(job_id))  # reaches setsid'd descendants
    try:
        os.killpg(pgid, signal.SIGKILL)
        return True
    except OSError as exc:
        # pgid may have exited between a liveness check and this call.
        constants.err(f"job {job_id}: failed to kill pgid {pgid}: {exc}")
        return False


def _has_pgid_tracking(store, job_id, record):
    """True if a job still has ANY backend-pgid tracking — recorded in the
    record OR present as the crash-safe backend.pgid file — regardless of
    whether that pgid is currently alive. Gates the cancelled-orphan reap on
    *presence* (not liveness) so a dead-but-uncleared stale pgid is still
    cleaned up once, closing the recycled-pgid wrong-kill window."""
    if record.get("pgid") is not None:
        return True
    return os.path.exists(os.path.join(store.job_dir(job_id), BACKEND_PGID_FILENAME))


def _clear_pgid_tracking(store, job_id):
    """Erase all backend-pgid tracking for a job: null the recorded pgid (a
    terminal-safe mutate) and delete the crash-safe backend.pgid file. Called as
    the FINAL act of every site that kills a backend because the job went
    cancelled, so no later reap/status/list call can re-derive a stale pgid and
    SIGKILL a process group the kernel has since recycled."""

    def _clear(rec):
        return dict(rec, pgid=None)

    _clear.allow_terminal_reentry = True
    store.mutate(job_id, _clear)
    # constitution: exempt C-ERR — absent file is the expected steady state; nothing to recover
    with contextlib.suppress(OSError):
        os.remove(os.path.join(store.job_dir(job_id), BACKEND_PGID_FILENAME))


def _reap_cancelled_orphan(store, job_id, record):
    """Kill the orphaned backend group of a cancelled job (if still alive), then
    clear all pgid tracking so this runs at most once and no later pass can
    re-probe a (possibly recycled) pgid."""
    pgid = record.get("pgid") or _read_pgid_file(store.job_dir(job_id))
    if pgid and _backend_alive(store, job_id):
        _kill_pgid(store, job_id, pgid)
    _clear_pgid_tracking(store, job_id)


def _make_pgid_persister(store, job_id):
    """Return an on_pgid callback that records the backend's process group even
    after the job goes terminal (recording a pgid is bookkeeping, not a
    lifecycle transition), then kills the group if cancel already won the race."""

    def _record(rec):
        return dict(rec, pgid=_record.pgid_val)

    _record.allow_terminal_reentry = True

    def _persist(pgid_val):
        _record.pgid_val = pgid_val
        updated = store.mutate(job_id, _record)
        if isinstance(updated.get("recovery"), dict):

            def _dispatch_owned(rec):
                rec.pop("recovery", None)
                rec.pop("failure_summary", None)
                return rec

            # store.mutate silently refuses terminal records without this, and
            # the very race the next branch handles (a cancel landing around
            # spawn) makes the job terminal here -- so the clear would no-op and
            # leave stale recovery/failure_summary on the finished record.
            _dispatch_owned.allow_terminal_reentry = True
            updated = store.mutate(job_id, _dispatch_owned)
            store.clear_recovery(job_id)
        if updated.get("state") == "cancelled":
            # Detached (setsid) backend outlived the cancel; the worker is the
            # only party that reliably knows the pgid at this instant. Kill it,
            # then clear tracking so the pgid we just re-recorded cannot linger
            # stale for a later reap to re-probe.
            _kill_pgid(store, job_id, pgid_val)
            _clear_pgid_tracking(store, job_id)

    return _persist
