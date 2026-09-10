"""C7d: `python3` means the runner's OWN interpreter, never a `PATH` search
(Correction 4, phase-3-5-decisions.md).

Evidence this closes (phases-3-5-ledger.md, C7d findings): with C7c's honest
child PATH (store bin dirs + `os.defpath`), a bare `python3` resolved to
macOS system Python 3.9 while `manifest check` itself runs under the
project's 3.11+ venv -- bodies importing `manifest_agent` died on a missing
stdlib symbol. Proven here with a real PATH impostor and a real disposable
candidate, not just asserted about the resolver.
"""

from __future__ import annotations

import sys

import pytest

from manifest_agent.checks import run_profile, toolchain
from manifest_agent.checks.models import CheckSpec
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture


@pytest.fixture
def candidate(source, tmp_path):
    (source[0] / "noop.py").write_text("VALUE = 1\n")
    return materialize(source, tmp_path)


def _impostor_python3_dir(tmp_path) -> str:
    """A `PATH` entry whose ONLY `python3` prints an unmistakable marker and
    exits 1 -- the attack this chunk closes: a check that runs under this
    interpreter can never PASS."""
    impostor_dir = tmp_path / "impostor-bin"
    impostor_dir.mkdir()
    script = impostor_dir / "python3"
    script.write_text("#!/bin/sh\necho IMPOSTOR\nexit 1\n")
    script.chmod(0o700)
    return str(impostor_dir)


def _demo_tool(*, version_argv=None, expected_version: str = "1.0.0") -> dict:
    return {
        "executable": "python3",
        "version_argv": version_argv or ("python3", "-c", "print('demo 1.0.0')"),
        "expected_version": expected_version,
        "required_modules": (),
    }


def _demo_check(
    argv: tuple[str, ...],
    *,
    check_id: str = "check.demo",
    inputs: tuple[str, ...] = ("noop.py",),
) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        category="test",
        group="test",
        argv=argv,
        cwd=".",
        inputs=inputs,
        dependencies=(),
        timeout_seconds=10.0,
        selection="project",
        tool="demo",
        version="1.0.0",
    )


def _registry(check: CheckSpec, tool: dict) -> dict:
    return {
        "schema_version": 1,
        "toolchain_lock_document": {},
        "tools": {"demo": tool},
        "checks": (check,),
        "candidate_preparations": (),
        "profiles": dict.fromkeys(
            ("quick", "full", "security", "release"), (check.id,)
        ),
        "coverage_pending": dict.fromkeys(("quick", "full", "security", "release"), ()),
    }


class TestPython3MeansTheRunnersInterpreter:
    """Deliverable 1: a fake `python3` on `PATH` must never be reached."""

    def test_a_registry_shaped_body_runs_under_sys_executable_not_a_path_impostor(
        self, candidate, tmp_path
    ):
        impostor_dir = _impostor_python3_dir(tmp_path)
        tool = _demo_tool()
        check = _demo_check(
            ("python3", "-c", "import sys; print(sys.executable)"),
        )
        registry = _registry(check, tool)
        env = {"PATH": impostor_dir, "HOME": str(tmp_path)}
        report = run_profile(registry, "full", None, candidate, env)
        assert report["status"] == "PASS", report["results"]
        diagnostics = report["results"][0]["diagnostics"]
        assert "IMPOSTOR" not in diagnostics
        assert sys.executable in diagnostics

    def test_the_version_probe_runs_under_sys_executable_not_a_path_impostor(
        self, candidate, tmp_path
    ):
        impostor_dir = _impostor_python3_dir(tmp_path)
        tool = _demo_tool(
            version_argv=("python3", "-c", "import sys; print(sys.executable)"),
            expected_version=sys.executable,
        )
        check = _demo_check(("python3", "-c", "pass"))
        registry = _registry(check, tool)
        env = {"PATH": impostor_dir, "HOME": str(tmp_path)}
        report = run_profile(registry, "full", None, candidate, env)
        # A probe that ran under the impostor would print "IMPOSTOR" (not a
        # token matching `expected_version`) and exit 1 -- BLOCKED, not PASS.
        assert report["status"] == "PASS", report["results"]

    def test_bash_is_never_rewritten(self):
        argv = ("bash", "-c", "true")
        assert toolchain.resolve_interpreter_argv(argv) == argv
