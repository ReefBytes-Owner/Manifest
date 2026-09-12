"""Execution reports over real disposable Git candidates and local checkers."""

from __future__ import annotations

import json
import platform
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace

import pytest

from manifest_agent.checks import run_profile
from manifest_agent.checks.cli import _list_report
from manifest_agent.checks.models import CheckSpec, PreparationSpec
from manifest_agent.checks.process import CAPTURE_LIMIT
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture


def _fixture_check(behavior: str, tools: dict[str, dict]) -> CheckSpec:
    if behavior == "missing-tool":
        executable = "/definitely/missing/manifest-check-tool"
        tool = "missing"
        tools[tool] = {
            "executable": executable,
            "version_argv": (executable, "--version"),
            "expected_version": "1.0.0",
            "required_modules": (),
        }
        argv = (executable,)
        inputs = ("exit-zero.py",)
    else:
        tool = "python"
        argv = (sys.executable, f"{behavior}.py")
        inputs = (f"{behavior}.py",)
    return CheckSpec(
        id=f"check.{behavior}",
        category="test",
        group="test",
        argv=argv,
        cwd=".",
        inputs=inputs,
        dependencies=(),
        timeout_seconds=2.0,
        selection="project",
        tool=tool,
        version=tools[tool]["expected_version"],
    )


@pytest.fixture
def candidate(source, tmp_path):
    scripts = {
        "exit-zero.py": "raise SystemExit(0)\n",
        "exit-one.py": "raise SystemExit(1)\n",
        "mark.py": "import os\nfrom pathlib import Path\nPath(os.environ['CHECK_MARKER']).write_text('ran')\n",
        "counter.py": "import os\nfrom pathlib import Path\np = Path(os.environ['CHECK_COUNTER']); p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')\n",
        "excess.py": "import sys\nsys.stdout.write('password=fixture-secret \\n' + 'x' * 100000)\nsys.stderr.write('y' * 100000)\n",
        "mutate-candidate.py": "from pathlib import Path\nPath('head-only.txt').write_text('mutated by checker')\n",
        "chain-a.py": (
            "import os,sys\nfrom pathlib import Path\n"
            "if os.environ.get('FAIL_CHAIN')=='before': raise SystemExit(4)\n"
            "root=Path('.apm/chain'); (root/'bin').mkdir(parents=True)\n"
            'body=f\'#!{sys.executable}\\nimport os,sys\\nfrom pathlib import Path\\nif sys.argv[1:]==["--version"]: print("chain 1.0.0")\\nelif sys.argv[1:2]==["-c"]: os.execv(sys.executable,(sys.executable,*sys.argv[1:]))\\nelif sys.argv[1:]==["prepare"]: Path("generated").mkdir(); Path("generated/value").write_text("ok"); Path("b-ran").write_text("yes")\\n\'\n'
            "tool=root/'bin/context-python'; tool.write_text(body); tool.chmod(0o700)\n"
            "(root/'localmod.py').write_text('VALUE=1\\n')\n"
            "if os.environ.get('FAIL_CHAIN')=='after': raise SystemExit(4)\n"
        ),
        "prepare.py": "from pathlib import Path\np = Path('.apm/generated'); p.mkdir(parents=True, exist_ok=True)\n(p / 'value').write_text(Path('head-only.txt').read_text())\n",
        "pkg/input.py": "VALUE = 1\n",
        "pkg/runner.py": "import sys\nfrom pathlib import Path\nassert all(Path(arg).is_file() for arg in sys.argv[1:])\nprint(' '.join(sys.argv[1:]))\n",
        "show-args.py": "import sys; print(' '.join(sys.argv[1:]))\n",
        "show-json-args.py": "import json,sys; print(json.dumps(sys.argv[1:]))\n",
        "-leading.py": "VALUE = 1\n",
        "space name.py": "VALUE = 1\n",
        "slow.py": "import time; time.sleep(.35)\n",
    }
    for name, body in scripts.items():
        path = source[0] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return materialize(source, tmp_path)


@pytest.fixture
def check_fixture() -> Callable[..., dict]:
    def registry_for(*behaviors: str) -> dict:
        version = platform.python_version()
        tools: dict[str, dict] = {
            "python": {
                "executable": sys.executable,
                "version_argv": (sys.executable, "--version"),
                "expected_version": version,
                "required_modules": (),
            }
        }
        checks = [_fixture_check(behavior, tools) for behavior in behaviors]
        ids = tuple(check.id for check in checks)
        return {
            "schema_version": 1,
            "tools": tools,
            "checks": tuple(checks),
            "candidate_preparations": (),
            "profiles": dict.fromkeys(("quick", "full", "security", "release"), ids),
            "coverage_pending": dict.fromkeys(
                ("quick", "full", "security", "release"), ()
            ),
        }

    return registry_for


def _with_checks(registry: dict, *checks: CheckSpec) -> dict:
    ids = tuple(check.id for check in checks)
    return {
        **registry,
        "checks": checks,
        "profiles": dict.fromkeys(registry["profiles"], ids),
    }


def _preparation(version: str) -> PreparationSpec:
    return PreparationSpec(
        id="prepare.generated",
        argv=(sys.executable, "prepare.py"),
        cwd=".",
        inputs=("prepare.py", "head-only.txt"),
        outputs=(".apm/generated",),
        groups=("test",),
        timeout_seconds=2.0,
        tool="python",
        version=version,
    )


def _context_tool() -> dict:
    return {
        "executable": "context-python",
        "version_argv": ("context-python", "--version"),
        "expected_version": "1.0.0",
        "required_modules": ("localmod",),
    }


def test_mixed_failure_and_missing_tool_do_not_pass(check_fixture, candidate):
    report = run_profile(
        check_fixture("exit-one", "missing-tool"), "full", None, candidate, {}
    )
    assert report["status"] == "FAIL"
    assert {result["status"] for result in report["results"]} == {"FAIL", "BLOCKED"}


def test_report_binds_candidate_tree_source_and_deterministic_config(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    first = run_profile(registry, "full", "test", candidate, {})
    reordered = dict(reversed(tuple(registry.items())))
    second = run_profile(reordered, "full", "test", candidate, {})
    state = json.loads((candidate.root / ".git/candidate-state.json").read_text())
    assert first["partial"] is True
    assert first["candidate_digest"] == state["digest"]
    assert first["source_digest"] == candidate.source_digest
    assert first["head_sha"] == candidate.head_sha
    assert first["base_sha"] == candidate.base_sha
    assert first["tree_sha"] == candidate.tree_sha
    assert len(first["config_digest"]) == 64
    assert first["config_digest"] == second["config_digest"]
    registry["coverage_pending"]["full"] = ("gap",)
    changed = run_profile(registry, "full", "test", candidate, {})
    assert changed["config_digest"] != first["config_digest"]


def test_version_requires_an_exact_whitespace_delimited_token(check_fixture, candidate):
    registry = check_fixture("exit-zero")
    registry["tools"]["python"]["expected_version"] = platform.python_version()[1:]
    check = replace(
        registry["checks"][0], version=registry["tools"]["python"]["expected_version"]
    )
    registry = _with_checks(registry, check)
    report = run_profile(registry, "full", None, candidate, {})
    assert report["status"] == "BLOCKED"
    assert "version mismatch" in report["results"][0]["diagnostics"]


def test_missing_required_python_module_blocks_before_checker_execution(
    check_fixture, candidate, tmp_path
):
    registry = check_fixture("mark")
    registry["tools"]["python"]["required_modules"] = ("missing_fixture_module",)
    marker = tmp_path / "checker-ran"
    report = run_profile(
        registry, "full", None, candidate, {"CHECK_MARKER": str(marker)}
    )
    assert report["status"] == "BLOCKED"
    assert "required Python module" in report["results"][0]["diagnostics"]
    assert not marker.exists()


def test_tool_preflight_uses_and_keys_by_effective_check_context(
    check_fixture, candidate
):
    tool_body = (
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "if sys.argv[1:] == ['--version']:\n    print('context tool 1.0.0')\n"
        "elif sys.argv[1:2] == ['-c']:\n    os.execv(sys.executable, (sys.executable, *sys.argv[1:]))\n"
        "else:\n    print('check ran')\n"
    )
    for name in ("one", "two"):
        root = candidate.root / ".apm" / name
        (root / "bin").mkdir(parents=True)
        (root / "input").write_text("input")
        executable = root / "bin/context-python"
        executable.write_text(tool_body)
        executable.chmod(0o700)
    (candidate.root / ".apm/one/localmod.py").write_text("VALUE = 'one'\n")
    registry = check_fixture("exit-zero")
    registry["tools"] = {"context": _context_tool()}
    checks = tuple(
        replace(
            registry["checks"][0],
            id=f"check.{name}",
            argv=("context-python", "check"),
            cwd=f".apm/{name}",
            inputs=(f".apm/{name}/input",),
            tool="context",
            version="1.0.0",
        )
        for name in ("one", "two")
    )
    report = run_profile(
        _with_checks(registry, *checks),
        "full",
        None,
        candidate,
        {"PATH": "bin", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert [result["status"] for result in report["results"]] == ["PASS", "BLOCKED"]
    assert "required Python module" in report["results"][1]["diagnostics"]


@pytest.mark.parametrize("failure_mode", [None, "after", "before"])
def test_chained_preparation_preflights_in_order_and_stops_after_failure(
    check_fixture, candidate, failure_mode
):
    registry = check_fixture("exit-zero")
    registry["tools"]["context"] = _context_tool()
    first = replace(
        _preparation(platform.python_version()),
        id="prepare.chain-a",
        argv=(sys.executable, "chain-a.py"),
        inputs=("chain-a.py",),
        outputs=(".apm/chain",),
    )
    second = replace(
        _preparation("1.0.0"),
        id="prepare.chain-b",
        argv=("context-python", "prepare"),
        cwd=".apm/chain",
        inputs=(".apm/chain/localmod.py",),
        outputs=(".apm/chain/generated", ".apm/chain/b-ran"),
        tool="context",
    )
    check = replace(
        registry["checks"][0],
        argv=("context-python", "check"),
        cwd=".apm/chain",
        inputs=(".apm/chain/generated/value",),
        tool="context",
        version="1.0.0",
    )
    registry = _with_checks(registry, check)
    registry["candidate_preparations"] = (first, second)
    environment = {"PATH": "bin", "PYTHONDONTWRITEBYTECODE": "1"}
    if failure_mode:
        environment["FAIL_CHAIN"] = failure_mode
    report = run_profile(registry, "full", None, candidate, environment)
    assert report["status"] == ("PASS" if failure_mode is None else "BLOCKED")
    assert (candidate.root / ".apm/chain/b-ran").exists() is (failure_mode is None)
    if failure_mode == "before":
        assert not (candidate.root / ".apm/chain").exists()


def test_failed_prerequisite_blocks_dependent_but_independent_check_continues(
    check_fixture, candidate, tmp_path
):
    registry = check_fixture("exit-one", "mark", "exit-zero")
    dependent = replace(registry["checks"][1], dependencies=("check.exit-one",))
    registry = _with_checks(
        registry, registry["checks"][0], dependent, registry["checks"][2]
    )
    marker = tmp_path / "dependent-ran"
    report = run_profile(
        registry, "full", None, candidate, {"CHECK_MARKER": str(marker)}
    )
    assert report["status"] == "FAIL"
    assert [result["status"] for result in report["results"]] == [
        "FAIL",
        "BLOCKED",
        "PASS",
    ]
    assert "prerequisite" in report["results"][1]["diagnostics"]
    assert not marker.exists()


def test_preparation_tool_version_is_validated_before_preparation_executes(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    wrong_version = platform.python_version()[1:]
    registry["tools"]["python"]["expected_version"] = wrong_version
    check = replace(
        registry["checks"][0], inputs=(".apm/generated/value",), version=wrong_version
    )
    registry = _with_checks(registry, check)
    registry["candidate_preparations"] = (_preparation(wrong_version),)
    report = run_profile(registry, "full", None, candidate, {})
    assert report["status"] == "BLOCKED"
    assert "version mismatch" in report["results"][0]["diagnostics"]
    assert not (candidate.root / ".apm/generated").exists()


def test_timeout_is_blocked_and_combined_diagnostics_are_bounded(
    check_fixture, candidate
):
    timeout_registry = check_fixture("slow")
    timeout_check = replace(timeout_registry["checks"][0], timeout_seconds=0.1)
    timed_out = run_profile(
        _with_checks(timeout_registry, timeout_check), "full", None, candidate, {}
    )
    excessive = run_profile(check_fixture("excess"), "full", None, candidate, {})
    assert timed_out["status"] == "BLOCKED"
    assert "timeout" in timed_out["results"][0]["diagnostics"]
    diagnostic = excessive["results"][0]["diagnostics"]
    assert "fixture-secret" not in diagnostic
    assert diagnostic.startswith("[head truncated: ")
    assert len(diagnostic.encode()) <= CAPTURE_LIMIT


def test_source_identity_is_checked_before_and_after_execution(
    check_fixture, candidate, source, tmp_path
):
    marker = tmp_path / "checker-ran"
    (source[0] / "staged").write_text("changed before check")
    before = run_profile(
        check_fixture("mark"),
        "full",
        None,
        candidate,
        {"CHECK_MARKER": str(marker)},
    )
    assert before["status"] == "BLOCKED"
    assert "source identity" in before["results"][0]["diagnostics"]
    assert not marker.exists()
    # Restore exactly the materialized source identity, then mutate during a slow check.
    (source[0] / "staged").write_text("staged edit\n")
    registry = check_fixture("slow")

    def mutate_source() -> None:
        time.sleep(0.1)
        (source[0] / "unstaged").write_text("changed during check")

    thread = threading.Thread(target=mutate_source)
    thread.start()
    after = run_profile(registry, "full", None, candidate, {})
    thread.join()
    assert after["status"] == "BLOCKED"
    assert "source identity" in after["results"][0]["diagnostics"]


def test_candidate_mutation_invalidates_an_executed_success(check_fixture, candidate):
    report = run_profile(check_fixture("mutate-candidate"), "full", None, candidate, {})
    assert report["status"] == "BLOCKED"
    assert "candidate identity" in report["results"][0]["diagnostics"]


def test_pending_coverage_blocks_complete_profile_but_not_partial_group(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    registry["coverage_pending"]["full"] = ("whole-project typing",)
    complete = run_profile(registry, "full", None, candidate, {})
    partial = run_profile(registry, "full", "test", candidate, {})
    assert complete["status"] == "BLOCKED"
    assert complete["coverage_pending"] == ["whole-project typing"]
    assert partial["status"] == "PASS"
    assert partial["partial"] is True


def test_group_report_preserves_pending_obligations_for_selected_checks(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    registry["coverage_pending"]["full"] = (
        "check.exit-zero: tool provisioning pending Phase 3",
        "lint.unselected: tool provisioning pending Phase 3",
    )

    report = run_profile(registry, "full", "test", candidate, {})

    assert report["status"] == "BLOCKED"
    assert report["partial"] is True
    assert report["coverage_pending"] == [
        "check.exit-zero: tool provisioning pending Phase 3"
    ]


def test_group_pending_obligations_match_longest_exact_valid_check_id(
    check_fixture, candidate
):
    registry = check_fixture("exit-zero")
    prefix = replace(
        registry["checks"][0], id="check.item", category="lint", group="lint"
    )
    colon = replace(registry["checks"][0], id="check.item:sub")
    registry = _with_checks(registry, prefix, colon)
    registry["coverage_pending"]["full"] = (
        "check.item: lint obligation pending Phase 3",
        "check.item:sub: test obligation pending Phase 3",
    )

    report = run_profile(registry, "full", "test", candidate, {})

    assert report["status"] == "BLOCKED"
    assert report["coverage_pending"] == [
        "check.item:sub: test obligation pending Phase 3"
    ]


def test_group_list_report_uses_the_same_exact_pending_obligation_matcher(
    check_fixture,
):
    registry = check_fixture("exit-zero")
    prefix = replace(
        registry["checks"][0], id="check.item", category="lint", group="lint"
    )
    colon = replace(registry["checks"][0], id="check.item:sub")
    registry = _with_checks(registry, prefix, colon)
    registry["coverage_pending"]["full"] = (
        "check.item: lint obligation pending Phase 3",
        "check.item:sub: test obligation pending Phase 3",
    )

    report = _list_report(registry, "full", "test")

    assert report["status"] == "BLOCKED"
    assert report["coverage_pending"] == [
        "check.item:sub: test obligation pending Phase 3"
    ]


def test_successful_check_is_executed_again_instead_of_reusing_cache(
    check_fixture, candidate, tmp_path
):
    counter = tmp_path / "counter"
    registry = check_fixture("counter")
    environment = {"CHECK_COUNTER": str(counter)}
    for _ in range(2):
        report = run_profile(registry, "full", None, candidate, environment)
        assert report["status"] == "PASS"
    assert counter.read_text() == "2"


def test_invalid_candidate_state_returns_blocked_report_instead_of_raising(
    check_fixture, candidate
):
    (candidate.root / ".git/candidate-state.json").write_text("not JSON")
    report = run_profile(check_fixture("exit-zero"), "full", None, candidate, {})
    assert report["status"] == "BLOCKED"
    assert report["candidate_digest"] == ""
    assert "identity" in report["results"][0]["diagnostics"]
