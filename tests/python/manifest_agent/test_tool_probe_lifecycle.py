"""Late cancellation and leader-observation contracts for tool probes."""

from __future__ import annotations

import signal
import sys
from pathlib import Path

import pytest

from tools.project_checks import tool_versions


def test_finalize_restores_and_closes_when_cleanup_raises(monkeypatch):
    events = []

    class Stream:
        def close(self):
            events.append("pipe-close")

    class Process:
        stdout = Stream()
        stderr = Stream()

    class Selector:
        def close(self):
            events.append("selector-close")

    monkeypatch.setattr(
        tool_versions._PROCESS,
        "_cleanup_family",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("waitid failed")),
    )
    monkeypatch.setattr(
        tool_versions._PROCESS.signal,
        "signal",
        lambda *_args: events.append("handler-restore"),
    )

    failure = tool_versions._PROCESS._finalize(
        Process(), Selector(), {signal.SIGTERM: signal.SIG_DFL}, None
    )

    assert isinstance(failure, tool_versions._PROCESS.ProbeProcessError)
    assert "waitid failed" in str(failure)
    assert events == ["pipe-close", "pipe-close", "selector-close", "handler-restore"]


def test_ignored_sigchld_blocks_before_launch(tmp_path: Path, monkeypatch):
    marker = tmp_path / "launched"
    real_getsignal = tool_versions._PROCESS.signal.getsignal

    def disposition(signum):
        return signal.SIG_IGN if signum == signal.SIGCHLD else real_getsignal(signum)

    monkeypatch.setattr(tool_versions._PROCESS.signal, "getsignal", disposition)
    monkeypatch.setattr(
        tool_versions._PROCESS.subprocess,
        "Popen",
        lambda *_args, **_kwargs: marker.write_text("launched"),
    )

    with pytest.raises(tool_versions.ProbeError, match="supervision is unsupported"):
        tool_versions._run_probe(["ignored"])
    assert not marker.exists()


def test_custom_sigchld_handler_blocks_before_launch(tmp_path: Path, monkeypatch):
    marker = tmp_path / "launched"
    real_getsignal = tool_versions._PROCESS.signal.getsignal

    def custom_handler(_signum, _frame):
        return None

    def disposition(signum):
        return custom_handler if signum == signal.SIGCHLD else real_getsignal(signum)

    monkeypatch.setattr(tool_versions._PROCESS.signal, "getsignal", disposition)
    monkeypatch.setattr(
        tool_versions._PROCESS.subprocess,
        "Popen",
        lambda *_args, **_kwargs: marker.write_text("launched"),
    )

    with pytest.raises(tool_versions.ProbeError, match="supervision is unsupported"):
        tool_versions._run_probe(["custom"])
    assert not marker.exists()


def test_unusable_wnowait_blocks_before_launch(tmp_path: Path, monkeypatch):
    marker = tmp_path / "launched"
    monkeypatch.setattr(
        tool_versions._PROCESS.os,
        "waitid",
        lambda *_args: (_ for _ in ()).throw(OSError("WNOWAIT unavailable")),
    )
    monkeypatch.setattr(
        tool_versions._PROCESS.subprocess,
        "Popen",
        lambda *_args, **_kwargs: marker.write_text("launched"),
    )
    with pytest.raises(tool_versions.ProbeError, match="supervision is unsupported"):
        tool_versions._run_probe(["unavailable"])
    assert not marker.exists()


def test_anchor_observation_failure_avoids_group_signal(tmp_path: Path, monkeypatch):
    script = tmp_path / "anchor.py"
    script.write_text(
        "import os, sys, time\n"
        "os.close(sys.stdout.fileno())\n"
        "os.close(sys.stderr.fileno())\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    calls = 0
    group_signals = []

    def fail_after_capability_probe(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ChildProcessError
        raise OSError("anchor lost")

    monkeypatch.setattr(
        tool_versions._PROCESS.os, "waitid", fail_after_capability_probe
    )
    monkeypatch.setattr(
        tool_versions._PROCESS,
        "_signal_group",
        lambda *_args: group_signals.append("unsafe"),
    )
    with pytest.raises(tool_versions.ProbeError, match="ownership observation failed"):
        tool_versions._run_probe([sys.executable, str(script)], timeout_seconds=1)
    assert group_signals == []


@pytest.mark.parametrize("phase", ("after-capture", "during-cleanup"))
def test_late_cancellation_cannot_return_success(
    tmp_path: Path, monkeypatch, phase: str
):
    script = tmp_path / "success.py"
    script.write_text("print('GNU bash, version 5.2.0')\n", encoding="utf-8")
    real_capture = tool_versions._PROCESS._capture
    real_cleanup = tool_versions._PROCESS._cleanup_family
    state = {}

    def capture(*args):
        cancellation = args[-1]
        state["cancellation"] = cancellation
        output = real_capture(*args)
        if phase == "after-capture":
            cancellation.handler(signal.SIGTERM, None)
        return output

    def cleanup(*args, **kwargs):
        if phase == "during-cleanup":
            state["cancellation"].handler(signal.SIGINT, None)
        return real_cleanup(*args, **kwargs)

    monkeypatch.setattr(tool_versions._PROCESS, "_capture", capture)
    monkeypatch.setattr(tool_versions._PROCESS, "_cleanup_family", cleanup)

    with pytest.raises(tool_versions.ProbeError, match="interrupted by signal"):
        tool_versions._run_probe([sys.executable, str(script)], timeout_seconds=1)


@pytest.mark.parametrize("delay, outcome", ((0.2, "success"), (10, "timed out")))
def test_pipe_eof_waits_for_leader_state(tmp_path: Path, delay: float, outcome: str):
    script = tmp_path / "eof.py"
    script.write_text(
        "import os, sys, time\n"
        "print('GNU bash, version 5.2.0', flush=True)\n"
        "os.close(sys.stdout.fileno())\n"
        "os.close(sys.stderr.fileno())\n"
        f"time.sleep({delay})\n",
        encoding="utf-8",
    )
    if outcome == "success":
        assert "GNU bash" in tool_versions._run_probe(
            [sys.executable, str(script)], timeout_seconds=1
        )
    else:
        with pytest.raises(tool_versions.ProbeError, match="timed out"):
            tool_versions._run_probe([sys.executable, str(script)], timeout_seconds=0.2)
