"""`scratch_home` checks never see the caller's real HOME (Correction 14 / C7o).

bats 1442 resolved `~/.claude/config/parallel_agent.yml` from inside a test
because the check body ran with whatever HOME the runner process happened to
have -- and then invoked a real reviewer CLI. These prove the fix at the
runner level: a `scratch_home` check gets a fresh, empty per-check HOME (and
XDG dirs) carved out of the run's own cache directory; a check without the
flag is unaffected; and a scratch-home body that writes into `$HOME` cannot
touch the caller's real one.
"""

from __future__ import annotations

import platform
import sys

import pytest

from manifest_agent.checks import run_profile
from manifest_agent.checks.models import CheckSpec
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture


@pytest.fixture
def candidate(source, tmp_path):
    scripts = {
        "print-home.py": "import os; print('HOME=' + os.environ.get('HOME', ''))\n",
        "write-home-marker.py": (
            "import os\nfrom pathlib import Path\n"
            "Path(os.environ['HOME'], 'marker.txt').write_text('wrote from check')\n"
        ),
    }
    for name, body in scripts.items():
        (source[0] / name).write_text(body)
    return materialize(source, tmp_path)


def _check(check_id: str, script: str, tool_version: str) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        category="test",
        group="test",
        argv=(sys.executable, script),
        cwd=".",
        inputs=(script,),
        dependencies=(),
        timeout_seconds=5.0,
        selection="project",
        tool="python",
        version=tool_version,
        scratch_home=True,
    )


def _registry(check: CheckSpec) -> dict:
    version = platform.python_version()
    tools = {
        "python": {
            "executable": sys.executable,
            "version_argv": (sys.executable, "--version"),
            "expected_version": version,
            "required_modules": (),
        }
    }
    return {
        "schema_version": 1,
        "tools": tools,
        "checks": (check,),
        "candidate_preparations": (),
        "profiles": dict.fromkeys(
            ("quick", "full", "security", "release"), (check.id,)
        ),
        "coverage_pending": dict.fromkeys(("quick", "full", "security", "release"), ()),
    }


def test_scratch_home_check_never_sees_caller_home(candidate, tmp_path):
    caller_home = tmp_path / "caller-home"
    caller_home.mkdir()
    version = platform.python_version()
    check = _check("check.print-home", "print-home.py", version)
    report = run_profile(
        _registry(check), "full", None, candidate, {"HOME": str(caller_home)}
    )
    assert report["status"] == "PASS", report["results"]
    diagnostics = report["results"][0]["diagnostics"]
    assert str(caller_home) not in diagnostics
    assert "home/check.print-home" in diagnostics


def test_check_without_scratch_home_keeps_callers_home(candidate, tmp_path):
    caller_home = tmp_path / "caller-home"
    caller_home.mkdir()
    version = platform.python_version()
    check = CheckSpec(
        id="check.print-home-plain",
        category="test",
        group="test",
        argv=(sys.executable, "print-home.py"),
        cwd=".",
        inputs=("print-home.py",),
        dependencies=(),
        timeout_seconds=5.0,
        selection="project",
        tool="python",
        version=version,
    )
    report = run_profile(
        _registry(check), "full", None, candidate, {"HOME": str(caller_home)}
    )
    assert report["status"] == "PASS", report["results"]
    assert f"HOME={caller_home}" in report["results"][0]["diagnostics"]


def test_scratch_home_write_leaves_real_home_untouched(candidate, tmp_path):
    caller_home = tmp_path / "caller-home"
    caller_home.mkdir()
    version = platform.python_version()
    check = _check("check.write-home", "write-home-marker.py", version)
    report = run_profile(
        _registry(check), "full", None, candidate, {"HOME": str(caller_home)}
    )
    assert report["status"] == "PASS", report["results"]
    assert not (caller_home / "marker.txt").exists()
