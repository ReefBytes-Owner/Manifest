# constitution: exempt C-SIZE — Task 5 requires one shared real-Git CLI contract suite.
"""CLI contracts for explicit shared project-check execution."""

from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from click.testing import CliRunner

from manifest_agent.cli import cli
from tests.python.manifest_agent._subprocess_env import isolated_env


def test_lifecycle_startup_does_not_import_project_check_subsystem():
    program = (
        "import sys\n"
        "from manifest_agent.cli import cli\n"
        "assert not any(name.startswith('manifest_agent.checks') for name in sys.modules)\n"
        "cli.main(args=['--help'], standalone_mode=False)\n"
        "assert not any(name.startswith('manifest_agent.checks') for name in sys.modules)\n"
        "cli.main(args=['install', '--help'], standalone_mode=False)\n"
        "assert not any(name.startswith('manifest_agent.checks') for name in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        check=False,
        env=isolated_env(
            PATH=os.environ["PATH"],
            PYTHONPATH=os.pathsep.join(
                (
                    str(Path(__file__).parents[3] / "src"),
                    str(Path(__file__).parents[3]),
                )
            ),
        ),
        text=True,
    )

    assert result.returncode == 0, result.stderr


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(root),
            *args,
        ],
        capture_output=True,
        check=True,
        env={
            "PATH": os.defpath,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        text=True,
    )
    return result.stdout.strip()


def _check(check_id: str, script: str, group: str) -> dict[str, object]:
    return {
        "id": check_id,
        "category": "lint" if group == "lint" else "test",
        "group": group,
        "argv": [sys.executable, script],
        "cwd": ".",
        "inputs": [script],
        "dependencies": [],
        "timeout_seconds": 5,
        "selection": "project",
        "pass_filenames": False,
        "tool": "python",
        "version": platform.python_version(),
    }


@dataclass(frozen=True)
class ConfiguredProject:
    root: Path
    config: Path
    base: str
    execution_marker: Path

    def write_registry(self, checks: list[dict[str, object]]) -> None:
        check_ids = [str(check["id"]) for check in checks]
        document = {
            "schema_version": 1,
            "tools": {
                "python": {
                    "executable": sys.executable,
                    "version_argv": [sys.executable, "--version"],
                    "expected_version": platform.python_version(),
                    "required_modules": [],
                }
            },
            "checks": checks,
            "candidate_preparations": [],
            "profiles": {
                "quick": check_ids,
                "full": check_ids,
                "security": check_ids,
                "release": check_ids,
            },
            "coverage_pending": {
                "quick": [],
                "full": [],
                "security": [],
                "release": [],
            },
        }
        self.config.write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def configured_project(tmp_path: Path) -> ConfiguredProject:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "--quiet")
    marker = tmp_path / "execution-marker"
    scripts = {
        "pass.py": "raise SystemExit(0)\n",
        "fail.py": "raise SystemExit(7)\n",
        "mark.py": (
            "import os\nfrom pathlib import Path\n"
            "Path(os.environ['CHECK_MARKER']).write_text('executed')\n"
        ),
        "args.py": "import json, sys\nprint(json.dumps(sys.argv[1:]))\n",
        "slow.py": (
            "import sys, time\nfrom pathlib import Path\n"
            "Path(sys.argv[1]).write_text('ready')\ntime.sleep(0.5)\n"
        ),
    }
    for name, body in scripts.items():
        (root / name).write_text(body, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    project = ConfiguredProject(root, tmp_path / "project-checks.json", base, marker)
    project.write_registry([_check("check.pass", "pass.py", "test")])
    return project


def _invoke(runner: CliRunner, configured_project: ConfiguredProject, *arguments: str):
    with runner.isolated_filesystem(temp_dir=configured_project.root.parent):
        os.chdir(configured_project.root)
        return runner.invoke(
            cli,
            [
                "check",
                "quick",
                "--project-config",
                str(configured_project.config),
                *arguments,
            ],
        )


def test_project_config_is_explicit_and_execution_base_is_conditionally_required(
    runner: CliRunner,
):
    missing_config = runner.invoke(cli, ["check", "quick", "--list"])
    missing_base = runner.invoke(
        cli,
        ["check", "quick", "--project-config", "/does/not/matter.json"],
    )

    assert missing_config.exit_code == 2
    assert "--project-config" in missing_config.output
    assert missing_base.exit_code == 2
    assert "--base" in missing_base.output


def test_malformed_and_unreadable_config_are_structured_infrastructure_failures(
    runner: CliRunner, configured_project: ConfiguredProject
):
    configured_project.config.write_text("{malformed", encoding="utf-8")
    malformed = _invoke(runner, configured_project, "--list", "--json")
    configured_project.config.unlink()
    unreadable = _invoke(runner, configured_project, "--list", "--json")

    for result in (malformed, unreadable):
        assert result.exit_code == 3
        report = json.loads(result.output)
        assert report["status"] == "BLOCKED"
        assert report["diagnostics"]


def test_listing_never_executes_commands(
    runner: CliRunner,
    configured_project: ConfiguredProject,
    monkeypatch: pytest.MonkeyPatch,
):
    configured_project.write_registry([_check("check.mark", "mark.py", "test")])
    monkeypatch.setenv("CHECK_MARKER", str(configured_project.execution_marker))

    result = _invoke(runner, configured_project, "--list", "--json")

    assert result.exit_code == 0
    assert json.loads(result.output)["required_ids"] == ["check.mark"]
    assert not configured_project.execution_marker.exists()


def _configure_listing_sentinels(
    project: ConfiguredProject, source: Path, marker_root: Path
) -> dict[str, Path]:
    sentinel = source / "sentinel.py"
    sentinel.write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path(sys.argv[2]).write_text(sys.argv[1])\n"
        f"print({platform.python_version()!r})\n",
        encoding="utf-8",
    )
    markers = {
        phase: marker_root / f"{phase}-marker"
        for phase in ("probe", "prepare", "check")
    }
    document = json.loads(project.config.read_text(encoding="utf-8"))
    document["tools"]["python"]["version_argv"] = [
        sys.executable,
        "sentinel.py",
        "probe",
        str(markers["probe"]),
    ]
    document["checks"][0].update(
        argv=[sys.executable, "sentinel.py", "check", str(markers["check"])],
        inputs=["sentinel.py"],
    )
    document["candidate_preparations"] = [
        {
            "id": "prepare.sentinel",
            "argv": [
                sys.executable,
                "sentinel.py",
                "prepare",
                str(markers["prepare"]),
            ],
            "cwd": ".",
            "inputs": ["sentinel.py"],
            "outputs": ["generated.txt"],
            "groups": ["test"],
            "timeout_seconds": 5,
            "tool": "python",
            "version": platform.python_version(),
            "network": False,
            "installs_dependencies": False,
        }
    ]
    project.config.write_text(json.dumps(document), encoding="utf-8")
    return markers


def test_listing_does_not_materialize_probe_prepare_or_execute(
    runner: CliRunner, configured_project: ConfiguredProject, tmp_path: Path
):
    plain_source = tmp_path / "plain-source-without-git"
    plain_source.mkdir()
    markers = _configure_listing_sentinels(configured_project, plain_source, tmp_path)

    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.chdir(plain_source)
        result = runner.invoke(
            cli,
            [
                "check",
                "quick",
                "--project-config",
                str(configured_project.config),
                "--list",
                "--json",
            ],
        )

    assert result.exit_code == 0
    assert json.loads(result.output)["required_ids"] == ["check.pass"]
    assert not any(marker.exists() for marker in markers.values())


def test_plain_listing_names_required_checks_and_pending_coverage(
    runner: CliRunner, configured_project: ConfiguredProject
):
    document = json.loads(configured_project.config.read_text(encoding="utf-8"))
    document["coverage_pending"]["quick"] = ["whole-project type coverage"]
    configured_project.config.write_text(json.dumps(document), encoding="utf-8")

    result = _invoke(runner, configured_project, "--list")

    assert result.exit_code == 3
    assert "required check: check.pass" in result.output
    assert "pending coverage: whole-project type coverage" in result.output


@pytest.mark.parametrize(
    ("script", "expected_status", "expected_exit"),
    [("pass.py", "PASS", 0), ("fail.py", "FAIL", 2)],
)
def test_execution_maps_checker_status_to_stable_exit_codes(
    runner: CliRunner,
    configured_project: ConfiguredProject,
    script: str,
    expected_status: str,
    expected_exit: int,
):
    configured_project.write_registry([_check(f"check.{script}", script, "test")])

    result = _invoke(
        runner, configured_project, "--base", configured_project.base, "--json"
    )

    assert result.exit_code == expected_exit
    assert json.loads(result.output)["status"] == expected_status


def test_runtime_infrastructure_failure_exits_three_with_report(
    runner: CliRunner, configured_project: ConfiguredProject
):
    check = _check("check.missing", "pass.py", "test")
    missing = "/definitely/missing/manifest-check-tool"
    check.update(argv=[missing], tool="missing", version="1.0.0")
    document = json.loads(configured_project.config.read_text(encoding="utf-8"))
    document["tools"]["missing"] = {
        "executable": missing,
        "version_argv": [missing, "--version"],
        "expected_version": "1.0.0",
        "required_modules": [],
    }
    check_ids = ["check.missing"]
    document["checks"] = [check]
    document["profiles"] = dict.fromkeys(document["profiles"], check_ids)
    configured_project.config.write_text(json.dumps(document), encoding="utf-8")

    result = _invoke(
        runner, configured_project, "--base", configured_project.base, "--json"
    )

    assert result.exit_code == 3
    report = json.loads(result.output)
    assert report["status"] == "BLOCKED"
    assert report["results"][0]["status"] == "BLOCKED"


def test_candidate_materialization_failure_exits_three_with_report(
    runner: CliRunner, configured_project: ConfiguredProject
):
    result = _invoke(runner, configured_project, "--base", "missing-revision", "--json")

    assert result.exit_code == 3
    report = json.loads(result.output)
    assert report["status"] == "BLOCKED"
    assert "Git operation blocked" in report["diagnostics"][0]


def test_unknown_profile_and_group_are_click_usage_errors(
    runner: CliRunner, configured_project: ConfiguredProject
):
    profile = runner.invoke(
        cli,
        [
            "check",
            "unknown",
            "--project-config",
            str(configured_project.config),
            "--list",
        ],
    )
    group = _invoke(runner, configured_project, "--list", "--group", "unknown")

    assert profile.exit_code == 2
    assert "Invalid value" in profile.output and "unknown" in profile.output
    assert group.exit_code == 2
    assert "Invalid value for '--group'" in group.output


def test_group_execution_reports_partial_scope(
    runner: CliRunner, configured_project: ConfiguredProject
):
    configured_project.write_registry(
        [
            _check("lint.pass", "pass.py", "lint"),
            _check("test.pass", "pass.py", "test"),
        ]
    )

    result = _invoke(
        runner,
        configured_project,
        "--base",
        configured_project.base,
        "--group",
        "lint",
        "--json",
    )

    report = json.loads(result.output)
    assert result.exit_code == 0
    assert report["partial"] is True
    assert report["group"] == "lint"
    assert report["required_ids"] == ["lint.pass"]


def test_ambient_temp_directory_cannot_place_candidate_inside_source(
    runner: CliRunner,
    configured_project: ConfiguredProject,
    monkeypatch: pytest.MonkeyPatch,
):
    import manifest_agent.checks.cli as check_cli

    monkeypatch.setattr(check_cli.tempfile, "tempdir", str(configured_project.root))

    result = _invoke(
        runner, configured_project, "--base", configured_project.base, "--json"
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "PASS"
    assert not any(
        path.name.startswith("manifest-check-")
        for path in configured_project.root.iterdir()
    )


@pytest.mark.parametrize("execution", [False, True])
def test_output_must_be_strictly_outside_checkout_and_reject_symlinks(
    runner: CliRunner,
    configured_project: ConfiguredProject,
    tmp_path: Path,
    execution: bool,
):
    mode = ["--base", configured_project.base] if execution else ["--list"]
    inside = configured_project.root / "report.json"
    inside_result = _invoke(
        runner, configured_project, *mode, "--json", "--output", str(inside)
    )
    target = tmp_path / "target.json"
    target.write_text("keep", encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)
    link_result = _invoke(
        runner, configured_project, *mode, "--json", "--output", str(linked)
    )
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    traversal_result = _invoke(
        runner,
        configured_project,
        *mode,
        "--json",
        "--output",
        str(linked_parent / "report.json"),
    )

    assert inside_result.exit_code == 2
    assert "strictly outside" in inside_result.output
    assert link_result.exit_code == 2
    assert "symlink" in link_result.output
    assert traversal_result.exit_code == 2
    assert "symlink" in traversal_result.output
    assert not inside.exists()
    assert target.read_text(encoding="utf-8") == "keep"
    assert not (real_parent / "report.json").exists()


def test_output_is_written_outside_checkout_without_duplicate_stdout(
    runner: CliRunner, configured_project: ConfiguredProject, tmp_path: Path
):
    output = tmp_path / "report.json"

    result = _invoke(
        runner, configured_project, "--list", "--json", "--output", str(output)
    )

    assert result.exit_code == 0
    assert result.output == ""
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "PASS"


def _configure_slow_check(configured_project: ConfiguredProject, ready: Path) -> None:
    check = _check("check.slow", "slow.py", "test")
    check["argv"] = [sys.executable, "slow.py", str(ready)]
    configured_project.write_registry([check])


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    snapshot = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.fsencode(os.readlink(path)))
        elif path.is_dir():
            snapshot[relative] = ("directory", b"")
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def _unexpected_tree_changes(
    before: dict[str, tuple[str, bytes]],
    after: dict[str, tuple[str, bytes]],
    allowed: set[str],
) -> dict[str, tuple[str, bytes]]:
    return {
        path: value
        for path, value in after.items()
        if path not in allowed and before.get(path) != value
    }


@dataclass
class OutputRelocationProbe:
    root: Path
    ready: Path
    output_parent: Path
    relocated_parent: Path
    real_fsync: Callable[[int], None]
    relocated: threading.Event = field(default_factory=threading.Event)
    write_paused: threading.Event = field(default_factory=threading.Event)
    inspected: threading.Event = field(default_factory=threading.Event)
    completed: threading.Event = field(default_factory=threading.Event)
    during: dict[str, tuple[str, bytes]] = field(default_factory=dict)

    def observe_first_output_fsync(self, descriptor: int) -> None:
        self.real_fsync(descriptor)
        if self.relocated.is_set() and stat.S_ISREG(os.fstat(descriptor).st_mode):
            self.write_paused.set()
            self.inspected.wait(timeout=5)

    def relocate_and_inspect(self) -> None:
        deadline = time.monotonic() + 5
        while not self.ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not self.ready.exists():
            return
        self.output_parent.rename(self.relocated_parent)
        self.relocated.set()
        while not self.write_paused.is_set() and not self.completed.is_set():
            time.sleep(0.001)
        if self.write_paused.is_set():
            self.during.update(_tree_snapshot(self.root))
            self.inspected.set()


def test_relocated_output_parent_is_revalidated_before_temp_file_write(
    runner: CliRunner,
    configured_project: ConfiguredProject,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import manifest_agent.checks.cli as check_cli

    ready = tmp_path / "checker-ready"
    _configure_slow_check(configured_project, ready)
    output_parent = tmp_path / "output-parent"
    output_parent.mkdir()
    relocated_parent = configured_project.root / "relocated-output-parent"
    before = _tree_snapshot(configured_project.root)
    probe = OutputRelocationProbe(
        configured_project.root,
        ready,
        output_parent,
        relocated_parent,
        check_cli.os.fsync,
    )
    monkeypatch.setattr(check_cli.os, "fsync", probe.observe_first_output_fsync)
    worker = threading.Thread(target=probe.relocate_and_inspect)
    worker.start()
    result = _invoke(
        runner,
        configured_project,
        "--base",
        configured_project.base,
        "--json",
        "--output",
        str(output_parent / "report.json"),
    )
    probe.completed.set()
    worker.join(timeout=5)

    allowed = {"relocated-output-parent"}
    assert not worker.is_alive()
    assert probe.relocated.is_set()
    assert result.exit_code == 3
    assert _unexpected_tree_changes(before, probe.during or before, allowed) == {}
    after = _tree_snapshot(configured_project.root)
    assert _unexpected_tree_changes(before, after, allowed) == {}


def test_concurrent_output_parent_substitution_cannot_redirect_into_source(
    runner: CliRunner, configured_project: ConfiguredProject, tmp_path: Path
):
    ready = tmp_path / "checker-ready"
    _configure_slow_check(configured_project, ready)
    output_parent = tmp_path / "output-parent"
    output_parent.mkdir()
    moved_parent = tmp_path / "original-output-parent"

    def substitute_parent() -> None:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if ready.exists():
            output_parent.rename(moved_parent)
            output_parent.symlink_to(configured_project.root, target_is_directory=True)

    worker = threading.Thread(target=substitute_parent)
    worker.start()
    result = _invoke(
        runner,
        configured_project,
        "--base",
        configured_project.base,
        "--json",
        "--output",
        str(output_parent / "report.json"),
    )
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert ready.exists()
    assert result.exit_code == 3
    report = json.loads(result.output)
    assert report["status"] == "BLOCKED"
    assert "output" in report["diagnostics"][0]
    assert not (configured_project.root / "report.json").exists()
    assert not (moved_parent / "report.json").exists()


def test_concurrent_final_symlink_substitution_is_blocked_without_following_it(
    runner: CliRunner, configured_project: ConfiguredProject, tmp_path: Path
):
    ready = tmp_path / "checker-ready"
    _configure_slow_check(configured_project, ready)
    output = tmp_path / "report.json"
    source_target = configured_project.root / "protected.json"
    source_target.write_text("keep", encoding="utf-8")

    def substitute_target() -> None:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if ready.exists():
            output.symlink_to(source_target)

    worker = threading.Thread(target=substitute_target)
    worker.start()
    result = _invoke(
        runner,
        configured_project,
        "--base",
        configured_project.base,
        "--json",
        "--output",
        str(output),
    )
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert result.exit_code == 3
    assert json.loads(result.output)["status"] == "BLOCKED"
    assert output.is_symlink()
    assert source_target.read_text(encoding="utf-8") == "keep"


def test_unsupported_secure_output_primitives_fail_blocked(
    runner: CliRunner,
    configured_project: ConfiguredProject,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import manifest_agent.checks.cli as check_cli

    output = tmp_path / "report.json"
    monkeypatch.setattr(check_cli, "_OUTPUT_PRIMITIVES_SUPPORTED", False)

    result = _invoke(
        runner, configured_project, "--list", "--json", "--output", str(output)
    )

    assert result.exit_code == 3
    report = json.loads(result.output)
    assert report["status"] == "BLOCKED"
    assert "unsupported" in report["diagnostics"][0]
    assert not output.exists()


def test_missing_output_parent_is_a_structured_infrastructure_failure(
    runner: CliRunner, configured_project: ConfiguredProject, tmp_path: Path
):
    output = tmp_path / "missing" / "report.json"

    result = _invoke(
        runner, configured_project, "--list", "--json", "--output", str(output)
    )

    assert result.exit_code == 3
    report = json.loads(result.output)
    assert report["status"] == "BLOCKED"
    assert "output" in report["diagnostics"][0]
    assert not output.exists()


@pytest.mark.parametrize("operation", ["fsync", "rename"])
def test_output_write_failure_falls_back_to_structured_stdout(
    runner: CliRunner,
    configured_project: ConfiguredProject,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
):
    import manifest_agent.checks.cli as check_cli

    output = tmp_path / "report.json"

    def fail(*args, **kwargs):
        raise OSError(f"simulated {operation} failure")

    monkeypatch.setattr(check_cli.os, operation, fail)
    result = _invoke(
        runner, configured_project, "--list", "--json", "--output", str(output)
    )

    assert result.exit_code == 3
    report = json.loads(result.output)
    assert report["status"] == "BLOCKED"
    assert f"simulated {operation} failure" in report["diagnostics"][0]


def test_output_permission_failure_falls_back_to_structured_stdout(
    runner: CliRunner, configured_project: ConfiguredProject, tmp_path: Path
):
    parent = tmp_path / "read-only"
    parent.mkdir(mode=0o500)
    try:
        result = _invoke(
            runner,
            configured_project,
            "--list",
            "--json",
            "--output",
            str(parent / "report.json"),
        )
    finally:
        parent.chmod(0o700)

    assert result.exit_code == 3
    report = json.loads(result.output)
    assert report["status"] == "BLOCKED"
    assert "output" in report["diagnostics"][0]


def test_newline_changed_filename_is_preserved_end_to_end(
    runner: CliRunner, configured_project: ConfiguredProject
):
    changed_name = "line\nbreak.py"
    (configured_project.root / changed_name).write_text("VALUE = 1\n", encoding="utf-8")
    check = _check("lint.args", "args.py", "lint")
    check.update(inputs=["."], selection="changed", pass_filenames=True)
    configured_project.write_registry([check])

    result = _invoke(
        runner, configured_project, "--base", configured_project.base, "--json"
    )

    report = json.loads(result.output)
    assert result.exit_code == 0
    assert report["results"][0]["selected_inputs"] == [changed_name]
    assert json.loads(report["results"][0]["diagnostics"]) == [changed_name]
