"""End-to-end `store:` tool resolution through `run_profile`.

Complements `test_toolchain.py` (unit-level failure semantics) by exercising
the full preflight -> execute_check path with a real disposable candidate,
proving the store's PATH override and hash re-verification actually reach a
running check, not just the resolver in isolation.
"""

from __future__ import annotations

import hashlib
import json
import sys

import pytest

from manifest_agent.checks import run_profile
from manifest_agent.checks.models import CheckSpec
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture


@pytest.fixture
def candidate(source, tmp_path):
    (source[0] / "noop.py").write_text("VALUE = 1\n")
    return materialize(source, tmp_path)


def _tool_script() -> str:
    return (
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('demo 1.0.0')\n"
        "else:\n"
        "    print('PATH=' + os.environ.get('PATH', ''))\n"
    )


def _provision_store(store, *, script: str | None = None) -> tuple[dict, str]:
    """Provision a fixture `demo` store tool; returns (lock, actual_exe_sha256)."""
    script = script if script is not None else _tool_script()
    exe_path = store / "tools/demo/1.0.0/bin/demo"
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    exe_path.write_text(script)
    exe_path.chmod(0o700)
    exe_sha = hashlib.sha256(exe_path.read_bytes()).hexdigest()
    lock = {
        "schema_version": 1,
        "tools": {
            "demo": {
                "kind": "binary",
                "version": "1.0.0",
                "platforms": {
                    "the-platform": {
                        "url": "https://example.invalid/demo.tar.gz",
                        "sha256": exe_sha,
                        "path_in_archive": "demo",
                    }
                },
            }
        },
    }
    manifest = {
        "schema_version": 1,
        "lock_digest": hashlib.sha256(
            json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "tools": {
            "demo": {
                "source_sha256": exe_sha,
                "executables": {
                    "bin/demo": {"path": "tools/demo/1.0.0/bin/demo", "sha256": exe_sha}
                },
            }
        },
    }
    (store / "manifest.json").write_text(json.dumps(manifest))
    return lock, exe_sha


def _registry(lock: dict) -> dict:
    tool = {
        "executable": "store:demo/bin/demo",
        "version_argv": ("store:demo/bin/demo", "--version"),
        "expected_version": "1.0.0",
        "required_modules": (),
    }
    check = CheckSpec(
        id="check.demo",
        category="test",
        group="test",
        argv=("store:demo/bin/demo", "--run"),
        cwd=".",
        inputs=("noop.py",),
        dependencies=(),
        timeout_seconds=5.0,
        selection="project",
        tool="demo",
        version="1.0.0",
    )
    return {
        "schema_version": 1,
        "toolchain_lock_document": lock,
        "tools": {"demo": tool},
        "checks": (check,),
        "candidate_preparations": (),
        "profiles": dict.fromkeys(
            ("quick", "full", "security", "release"), ("check.demo",)
        ),
        "coverage_pending": dict.fromkeys(("quick", "full", "security", "release"), ()),
    }


def _env(store, extra: dict | None = None) -> dict:
    env = {
        "PATH": "/should/not/appear/in/child",
        "HOME": "/tmp",
        "MANIFEST_TOOLCHAIN_STORE": str(store),
    }
    if extra:
        env.update(extra)
    return env


@pytest.fixture(autouse=True)
def _pin_platform(monkeypatch):
    from manifest_agent.checks import toolchain

    monkeypatch.setattr(toolchain, "current_platform", lambda: "the-platform")


def test_store_tool_blocked_when_not_provisioned(candidate, tmp_path):
    store = tmp_path / "empty-store"
    lock, _ = _provision_store(tmp_path / "throwaway-for-lock-only")
    registry = _registry(lock)
    report = run_profile(registry, "full", None, candidate, _env(store))
    assert report["status"] == "BLOCKED"
    assert (
        "toolchain: demo not provisioned (run manifest provision)"
        in report["results"][0]["diagnostics"]
    )


def test_store_tool_passes_and_hides_user_path(candidate, tmp_path):
    store = tmp_path / "store"
    lock, _ = _provision_store(store)
    registry = _registry(lock)
    report = run_profile(registry, "full", None, candidate, _env(store))
    assert report["status"] == "PASS", report["results"]
    diagnostics = report["results"][0]["diagnostics"]
    assert "/should/not/appear/in/child" not in diagnostics
    assert "tools/demo/1.0.0/bin" in diagnostics


def test_store_tool_blocks_on_swapped_launcher(candidate, tmp_path):
    store = tmp_path / "store"
    lock, _ = _provision_store(store)
    registry = _registry(lock)
    # Swap the launcher after provisioning -- same version string, different bytes.
    exe_path = store / "tools/demo/1.0.0/bin/demo"
    exe_path.write_text(_tool_script() + "\n# swapped\n")
    exe_path.chmod(0o700)
    report = run_profile(registry, "full", None, candidate, _env(store))
    assert report["status"] == "BLOCKED"
    assert "toolchain: demo digest mismatch" in report["results"][0]["diagnostics"]


def test_store_changed_during_run_blocks(candidate, tmp_path):
    """A store mutation that happens as a side effect of the check's own
    subprocess call must BLOCK with the distinct "changed during run" reason,
    not silently report whatever status the stale run produced. A
    self-mutating launcher (imagine an autoupdate) is the concrete attack
    this closes: the version probe and hash preflight both matched before
    the run, but the binary that actually executed rewrote itself."""
    from manifest_agent.checks import execute_check, toolchain

    store = tmp_path / "store"
    self_mutating_script = (
        f"#!{sys.executable}\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('demo 1.0.0')\n"
        "else:\n"
        "    with open(__file__, 'a') as handle:\n"
        "        handle.write('\\n# self-mutated\\n')\n"
    )
    lock, _ = _provision_store(store, script=self_mutating_script)
    registry = _registry(lock)
    resolved = toolchain.resolve(
        "store:demo/bin/demo",
        lock=lock,
        store=store,
        platform="the-platform",
    )
    assert isinstance(resolved, toolchain.ResolvedTool)
    check = registry["checks"][0]
    result = execute_check(check, candidate, _env(store), resolved)
    assert result.status == "BLOCKED"
    assert "toolchain: store changed during run" in result.diagnostics
