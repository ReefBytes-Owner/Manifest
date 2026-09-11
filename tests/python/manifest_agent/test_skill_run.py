# constitution: exempt C-SIZE -- packaging and deployed-runtime contracts share costly wheel fixtures.
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest
from manifest_model_policy.skill_run import CommandRunner as InstalledCommandRunner

from manifest_agent.models import CommandResult
from manifest_agent.process import CommandRunner
from manifest_agent.skill_run import (
    TASK_LIMIT,
    SkillRecoveryStore,
    SkillRunReport,
    execute_skill_command,
    load_policy_config,
    read_task,
    run_skill,
)


class Runner(CommandRunner):
    def __init__(self, results):
        self.results = list(results)
        self.argv = []
        self.stdin = []

    def run(self, argv, *, env=None, stdin_bytes=None):
        del env
        self.argv.append(tuple(argv))
        self.stdin.append(stdin_bytes)
        return self.results.pop(0)


def _config():
    return {
        "model_tiers": {"codex": {"advanced": "gpt-a", "flash": "gpt-f"}},
        "model_fallback": {"mode": "auto", "chains": {"codex": ["advanced", "flash"]}},
        "cli_agents": {
            "codex": {
                "binary": "codex",
                "base_args": ["exec"],
                "model_args": ["--model", "{model}"],
                "prompt_args": ["{prompt}"],
            }
        },
    }


def test_piped_skill_command_never_prompts_consumed_task_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n", encoding="utf-8")
    config = tmp_path / "parallel_agent.yml"
    config.write_text("{}\n", encoding="utf-8")
    confirmations: list[str] = []
    observed: dict[str, object] = {}

    def fake_run_skill(*_args, **kwargs):
        observed["interactive"] = kwargs["interactive"]
        if kwargs["interactive"]:
            kwargs["confirm_callback"]("approve fallback")
        return SkillRunReport(
            "codex",
            ({"tier": "advanced", "model": "gpt-a"},),
            None,
            "",
            "rate_limit",
            (),
            "manifest skill-run demo --fallback-decision approve",
            {"recovery_id": "fixture", "version": 1, "next_tier": "flash"},
        )

    module = sys.modules[execute_skill_command.__module__]
    monkeypatch.setattr(module, "run_skill", fake_run_skill)

    outcome = execute_skill_command(
        skill=skill,
        harness="codex",
        task_stream=io.BytesIO(b"private piped task"),
        config_path=config,
        model_chain="advanced,flash",
        model_fallback="confirm",
        confirm_callback=lambda message: confirmations.append(message) or True,
    )

    assert observed == {"interactive": False}
    assert confirmations == []
    assert outcome.payload["recovery"]["recovery_id"] == "fixture"


def test_skill_run_auto_fallback_preserves_task_in_memory(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    runner = Runner(
        [
            CommandResult(("codex",), 1, "", "HTTP 429 rate limit"),
            CommandResult(("codex",), 0, "done", ""),
        ]
    )
    result = run_skill(
        skill,
        "private task",
        "codex",
        config=_config(),
        model_chain=("advanced", "flash"),
        runner=runner,
    )
    assert result.output == "done"
    assert len(result.attempts) == 2
    assert "private task" in runner.argv[0][-1]


def test_skill_run_clears_output_file_between_fallback_attempts(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    config = _config()
    config["cli_agents"]["codex"]["base_args"] = [
        "exec",
        "--output-last-message",
        "{output_file}",
    ]

    class OutputFileRunner:
        calls = 0

        def run(self, argv, **_kwargs):
            self.calls += 1
            output_path = Path(argv[argv.index("--output-last-message") + 1])
            if self.calls == 1:
                output_path.write_text("stale partial", encoding="utf-8")
                return CommandResult(tuple(argv), 1, "", "HTTP 429 rate limit")
            return CommandResult(tuple(argv), 0, "fresh final", "")

    result = run_skill(
        skill,
        "private task",
        "codex",
        config=config,
        model_chain=("advanced", "flash"),
        runner=OutputFileRunner(),
    )

    assert result.output == "fresh final"
    assert result.final_model == "gpt-f"


def test_skill_run_stdin_transport_keeps_large_prompt_out_of_argv(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    runner = Runner([CommandResult(("codex",), 0, "done", "")])
    config = _config()
    config["cli_agents"]["codex"].update(
        {
            "skill_prompt_transport": "stdin",
            "skill_prompt_args": ["-"],
        }
    )
    task = "x" * 200_000

    result = run_skill(
        skill,
        task,
        "codex",
        config=config,
        model_chain=("advanced",),
        runner=runner,
    )

    assert result.output == "done"
    assert task not in "\0".join(runner.argv[0])
    assert runner.argv[0][-1] == "-"
    assert runner.stdin[0] is not None
    assert task.encode() in runner.stdin[0]


def test_installed_runner_delivers_one_mib_task_over_stdin(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    config = _config()
    config["cli_agents"]["codex"] = {
        "binary": sys.executable,
        "base_args": [
            "-c",
            "import sys; print(len(sys.stdin.buffer.read()))",
        ],
        "model_args": [],
        "skill_prompt_transport": "stdin",
        "skill_prompt_args": [],
    }

    result = run_skill(
        skill,
        "x" * TASK_LIMIT,
        "codex",
        config=config,
        model_chain=("advanced",),
    )

    assert result.failure is None
    assert int(result.output.strip()) > TASK_LIMIT


def test_repo_skill_run_transports_keep_prompts_out_of_argv() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    config = load_policy_config(repo_root / "configs/claude/config/parallel_agent.yml")

    # antigravity is the deliberate exception: agy discards piped stdin in print
    # mode and exposes no prompt-file flag, so inline argv is the only transport
    # that actually delivers the prompt.
    assert {
        name: entry.get("skill_prompt_transport")
        for name, entry in config["cli_agents"].items()
    } == {
        "claude": "stdin",
        "gemini": "stdin",
        "cursor": "stdin",
        "codex": "stdin",
        "antigravity": "argv",
        "devin": "file",
    }


def test_antigravity_skill_transport_matches_working_prompt_args() -> None:
    """agy ignores stdin under --print; skill-run must pass the prompt inline."""
    repo_root = Path(__file__).resolve().parents[3]
    entry = load_policy_config(repo_root / "configs/claude/config/parallel_agent.yml")[
        "cli_agents"
    ]["antigravity"]

    assert entry["skill_prompt_transport"] == "argv"
    assert "{prompt}" in entry["skill_prompt_args"]
    assert list(entry["skill_prompt_args"]) == list(entry["prompt_args"])


def test_skill_run_confirm_noninteractive_returns_recovery(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    runner = Runner([CommandResult(("codex",), 1, "", "HTTP 429 rate limit")])
    store = SkillRecoveryStore(tmp_path / "state")
    task = "private-sentinel-7fbd1d"
    result = run_skill(
        skill,
        task,
        "codex",
        config=_config(),
        model_chain=("advanced", "flash"),
        fallback_mode="confirm",
        runner=runner,
        recovery_store=store,
    )
    assert result.failure == "rate_limit"
    assert "--fallback-decision approve" in result.recovery_command
    assert result.recovery is not None
    record_path = store.root / f"{result.recovery['recovery_id']}.json"
    assert record_path.stat().st_mode & 0o777 == 0o600
    assert task not in record_path.read_text(encoding="utf-8")

    record = store.read(result.recovery["recovery_id"])
    resumed = run_skill(
        skill,
        "resubmitted task",
        "codex",
        config=_config(),
        model_chain=tuple(record["remaining_tiers"]),
        fallback_mode="auto",
        runner=Runner([CommandResult(("codex",), 0, "done", "")]),
        recovery_store=store,
        recovery_id=result.recovery["recovery_id"],
        expected_version=result.recovery["version"],
        previous_attempts=tuple(record["attempts"]),
    )

    assert resumed.output == "done"
    assert len(resumed.attempts) == 2
    assert not record_path.exists()


def test_metadata_absent_execution_is_one_shot(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    runner = Runner([CommandResult(("codex",), 1, "", "HTTP 429 rate limit")])

    result = run_skill(skill, "task", "codex", config=_config(), runner=runner)

    assert len(result.attempts) == 1
    assert result.recovery_command is None


def test_skill_recovery_store_rejects_task_bearing_attempt_fields(
    tmp_path: Path,
) -> None:
    store = SkillRecoveryStore(tmp_path / "state")

    with pytest.raises(ValueError, match="attempt is invalid"):
        store.create(
            {
                "skill_path": str(tmp_path / "SKILL.md"),
                "harness": "codex",
                "remaining_tiers": ["flash"],
                "fallback_mode": "confirm",
                "attempts": [{"tier": "advanced", "model": "gpt-a", "task": "private"}],
                "requires_task_resubmission": True,
            }
        )


def test_skill_recovery_store_stale_cas_preserves_current_record(
    tmp_path: Path,
) -> None:
    store = SkillRecoveryStore(tmp_path / "state")
    document = {
        "skill_path": str(tmp_path / "SKILL.md"),
        "harness": "codex",
        "remaining_tiers": ["flash"],
        "fallback_mode": "confirm",
        "attempts": [{"tier": "advanced", "model": "gpt-a"}],
        "requires_task_resubmission": True,
    }
    record = store.create(document)

    with pytest.raises(ValueError, match="stale recovery version"):
        store.replace(record["recovery_id"], record["version"] + 1, document)
    assert store.read(record["recovery_id"]) == record
    with pytest.raises(ValueError, match="stale recovery version"):
        store.delete(record["recovery_id"], record["version"] + 1)
    assert store.read(record["recovery_id"]) == record


def test_skill_recovery_claim_rejects_race_and_recovers_abandoned(
    tmp_path: Path,
) -> None:
    store = SkillRecoveryStore(tmp_path / "state")
    record = store.create(
        {
            "skill_path": str(tmp_path / "SKILL.md"),
            "harness": "codex",
            "remaining_tiers": ["flash"],
            "fallback_mode": "confirm",
            "attempts": [{"tier": "advanced", "model": "gpt-a"}],
            "requires_task_resubmission": True,
        }
    )

    claimed, descriptor = store.claim(record["recovery_id"], record["version"])
    with pytest.raises(ValueError, match="active claim"):
        store.claim(record["recovery_id"], record["version"])
    SkillRecoveryStore.release_claim(descriptor)

    recovered = store.recover_abandoned(record["recovery_id"])
    assert claimed["state"] == "claimed"
    assert recovered["state"] == "pending"
    assert recovered["version"] == claimed["version"] + 1


def test_skill_run_spawn_failure_restores_claimed_recovery(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    store = SkillRecoveryStore(tmp_path / "state")
    record = store.create(
        {
            "skill_path": str(skill.resolve()),
            "harness": "codex",
            "remaining_tiers": ["flash"],
            "fallback_mode": "confirm",
            "attempts": [{"tier": "advanced", "model": "gpt-a"}],
            "requires_task_resubmission": True,
        }
    )

    class SpawnFailure:
        def run(self, argv, *, env=None):
            del argv, env
            raise OSError("spawn failed with private-sentinel")

    with pytest.raises(RuntimeError, match="recovery version"):
        run_skill(
            skill,
            "resubmitted private task",
            "codex",
            config=_config(),
            model_chain=("flash",),
            runner=SpawnFailure(),
            recovery_store=store,
            recovery_id=record["recovery_id"],
            expected_version=record["version"],
            previous_attempts=tuple(record["attempts"]),
        )

    restored = store.read(record["recovery_id"])
    assert restored["state"] == "pending"
    assert restored["version"] == record["version"] + 2
    serialized = json.dumps(restored)
    assert "resubmitted private task" not in serialized
    assert "private-sentinel" not in serialized


def test_skill_run_enforces_four_cumulative_attempts(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    previous = tuple(
        {"tier": f"prior-{index}", "model": f"model-{index}"} for index in range(3)
    )

    with pytest.raises(ValueError, match="at most four unique attempts"):
        run_skill(
            skill,
            "resubmitted task",
            "codex",
            config=_config(),
            model_chain=("advanced", "flash"),
            runner=Runner([]),
            previous_attempts=previous,
        )


def test_read_task_rejects_symlink_without_whole_file_read(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("task", encoding="utf-8")
    link = tmp_path / "task.txt"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="safe regular file"):
        read_task(task_file=link)


def test_installed_command_runner_bounds_provider_streams() -> None:
    result = InstalledCommandRunner().run(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 70000)"]
    )

    assert len(result.stdout.encode()) == 64 * 1024
    assert result.truncated is True


def test_skill_run_timeout_terminates_provider_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "escaped-child.txt"
    child = (
        "import time; from pathlib import Path; time.sleep(0.4); "
        f"Path({str(marker)!r}).write_text('alive')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(10)"
    )
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    config = _config()
    config["timeouts"] = {"default": 0.05}
    config["cli_agents"]["codex"] = {
        "binary": sys.executable,
        "base_args": ["-c", parent],
        "model_args": [],
        "skill_prompt_transport": "stdin",
        "skill_prompt_args": [],
    }

    started = time.monotonic()
    result = run_skill(
        skill,
        "private task",
        "codex",
        config=config,
        model_chain=("advanced",),
    )

    assert time.monotonic() - started < 2
    assert result.failure == "transient"
    time.sleep(0.6)
    assert not marker.exists()


# constitution: exempt C-SIZE -- one subprocess scenario pins deployment, fallback, and resume state.
def test_deployed_manifest_skill_run_uses_runtime_config_from_unrelated_cwd(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for project in (
        repo_root / "configs/claude/scripts/manifest_model_policy",
        repo_root / "configs/claude",
    ):
        built = subprocess.run(
            [
                "uv",
                "build",
                "--project",
                str(project),
                "--wheel",
                "--out-dir",
                str(wheels),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert built.returncode == 0, built.stderr
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    installed = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(site_packages),
            "--offline",
            "--reinstall",
            "--no-deps",
            *map(str, sorted(wheels.glob("*.whl"))),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr

    runtime = tmp_path / "deployed-runtime"
    (runtime / "config").mkdir(parents=True)
    (runtime / "config/parallel_agent.yml").write_text(
        (repo_root / "configs/claude/config/parallel_agent.yml").read_text(),
        encoding="utf-8",
    )
    skills_root = tmp_path / "deployed-skills"
    skill = skills_root / "demo/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\n---\nDo the work.\n", encoding="utf-8")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake_codex = binaries / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *gpt-5.6-sol*) printf 'HTTP 429 rate limit' >&2; exit 1 ;;\n"
        "  *) printf deployed-ok ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(tmp_path / "empty-home"),
            "MANIFEST_HOME": str(runtime),
            "PYTHONPATH": str(site_packages),
            "MANIFEST_SKILLS_ROOT": str(skills_root),
            "MANIFEST_SKILL_RUN_STATE_ROOT": str(tmp_path / "recovery-state"),
            "PATH": f"{binaries}:{environment['PATH']}",
        }
    )
    runtime_entry = [sys.executable, "-c", "from manifest_cli import cli; cli()"]

    result = subprocess.run(
        [
            *runtime_entry,
            "skill-run",
            "demo",
            "--harness",
            "codex",
            "--model",
            "flash",
            "--model-fallback",
            "auto",
            "--non-interactive",
            "--json",
        ],
        cwd=unrelated,
        env=environment,
        input="private task",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["output"] == "deployed-ok"
    assert report["attempts"][0]["tier"] == "flash"

    pending = subprocess.run(
        [
            *runtime_entry,
            "skill-run",
            "demo",
            "--harness",
            "codex",
            "--model",
            "advanced",
            "--model-chain",
            "flash",
            "--model-fallback",
            "confirm",
            "--non-interactive",
            "--json",
        ],
        cwd=unrelated,
        env=environment,
        input="private task must be resubmitted",
        capture_output=True,
        text=True,
        check=False,
    )
    assert pending.returncode == 1, pending.stderr
    pending_report = json.loads(pending.stdout)
    recovery = pending_report["recovery"]
    assert recovery["requires_task_resubmission"] is True
    assert "private task must be resubmitted" not in (
        tmp_path / "recovery-state" / f"{recovery['recovery_id']}.json"
    ).read_text(encoding="utf-8")

    pending_text = subprocess.run(
        [
            *runtime_entry,
            "skill-run",
            "demo",
            "--harness",
            "codex",
            "--model",
            "advanced",
            "--model-chain",
            "flash",
            "--model-fallback",
            "confirm",
            "--non-interactive",
        ],
        cwd=unrelated,
        env=environment,
        input="another private task",
        capture_output=True,
        text=True,
        check=False,
    )
    assert pending_text.returncode == 1, pending_text.stderr
    assert "failure: rate_limit" in pending_text.stdout
    assert "recovery_id:" in pending_text.stdout
    assert "resume: manifest skill-run" in pending_text.stdout

    resumed = subprocess.run(
        [
            *runtime_entry,
            "skill-run",
            "demo",
            "--harness",
            "codex",
            "--recovery-id",
            recovery["recovery_id"],
            "--expected-version",
            str(recovery["version"]),
            "--fallback-decision",
            "approve",
            "--non-interactive",
            "--json",
        ],
        cwd=unrelated,
        env=environment,
        input="resubmitted private task",
        capture_output=True,
        text=True,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    resumed_report = json.loads(resumed.stdout)
    assert resumed_report["output"] == "deployed-ok"
    assert [attempt["tier"] for attempt in resumed_report["attempts"]] == [
        "advanced",
        "flash",
    ]
    assert not (
        tmp_path / "recovery-state" / f"{recovery['recovery_id']}.json"
    ).exists()


# constitution: exempt C-SIZE -- wheel build and isolated execution are one packaging boundary test.
def test_built_root_wheel_skill_run_resolves_packaged_policy_config(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    wheels = tmp_path / "root-wheels"
    wheels.mkdir()
    built = subprocess.run(
        [
            "uv",
            "build",
            "--project",
            str(repo_root),
            "--wheel",
            "--out-dir",
            str(wheels),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    wheel = next(wheels.glob("manifest_agent-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode("utf-8")
    assert "manifest_agent/_model_policy/skill_run.py" in names
    assert "Requires-Dist: manifest-model-policy" not in metadata
    site_packages = tmp_path / "root-site-packages"
    site_packages.mkdir()
    installed = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(site_packages),
            "--offline",
            "--reinstall",
            "--no-deps",
            str(wheel),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr
    probe_environment = dict(os.environ, PYTHONPATH=str(site_packages))
    policy_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import manifest_agent.skill_run as s; print(s.run_skill.__module__)",
        ],
        cwd=tmp_path,
        env=probe_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert policy_probe.returncode == 0, policy_probe.stderr
    assert policy_probe.stdout.strip() == "manifest_agent._model_policy.skill_execution"
    skills = tmp_path / "root-skills"
    skill = skills / "demo/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\n---\nDo the work.\n", encoding="utf-8")
    binaries = tmp_path / "root-bin"
    binaries.mkdir()
    codex = binaries / "codex"
    codex.write_text("#!/bin/sh\nprintf packaged-root-ok\n", encoding="utf-8")
    codex.chmod(0o755)
    unrelated = tmp_path / "outside-checkout"
    unrelated.mkdir()
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(tmp_path / "root-home"),
            "MANIFEST_SKILLS_ROOT": str(skills),
            "PATH": f"{binaries}:{environment['PATH']}",
            "PYTHONPATH": str(site_packages),
        }
    )
    root_entry = [sys.executable, "-c", "from manifest_agent.cli import cli; cli()"]

    result = subprocess.run(
        [
            *root_entry,
            "skill-run",
            "demo",
            "--harness",
            "codex",
            "--model",
            "flash",
            "--model-fallback",
            "auto",
            "--non-interactive",
            "--json",
        ],
        cwd=unrelated,
        env=environment,
        input="private packaged task",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["output"] == "packaged-root-ok"


# constitution: exempt C-SIZE -- one end-to-end recovery transcript compares source and deployed CLIs.
def test_root_manifest_skill_run_matches_deployed_recovery_contract(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\n---\nDo the work.\n", encoding="utf-8")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake_codex = binaries / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *gpt-5.6-sol*) printf 'HTTP 429 rate limit' >&2; exit 1 ;;\n"
        "  *) printf root-ok ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    state_root = tmp_path / "recovery"
    environment = dict(os.environ)
    environment.update(
        {
            "MANIFEST_SKILLS_ROOT": str(skills_root),
            "MANIFEST_SKILL_RUN_STATE_ROOT": str(state_root),
            "PATH": f"{binaries}:{environment['PATH']}",
        }
    )
    # Invoke the running interpreter's module entry point directly rather than
    # `uv run manifest ...` against the repo: under the runner guard
    # (UV_NO_SYNC=1, empty UV_PROJECT_ENVIRONMENT) `uv run` cannot spawn a
    # project console script (Correction 12 rule 1).
    base = [
        sys.executable,
        "-m",
        "manifest_agent",
        "skill-run",
        "manifest-workspace:demo",
    ]
    pending = subprocess.run(
        [
            *base,
            "--harness",
            "codex",
            "--model",
            "advanced",
            "--model-chain",
            "flash",
            "--model-fallback",
            "confirm",
            "--non-interactive",
            "--json",
        ],
        cwd=repo_root,
        env=environment,
        input="private root task",
        capture_output=True,
        text=True,
        check=False,
    )
    assert pending.returncode == 1, pending.stderr
    recovery = json.loads(pending.stdout)["recovery"]
    record = state_root / f"{recovery['recovery_id']}.json"
    assert "private root task" not in record.read_text(encoding="utf-8")

    stale = subprocess.run(
        [
            *base,
            "--harness",
            "codex",
            "--recovery-id",
            recovery["recovery_id"],
            "--expected-version",
            str(recovery["version"] + 1),
            "--fallback-decision",
            "approve",
            "--non-interactive",
            "--json",
        ],
        cwd=repo_root,
        env=environment,
        input="resubmitted root task",
        capture_output=True,
        text=True,
        check=False,
    )
    assert stale.returncode == 2
    assert "stale recovery version" in stale.stderr
    assert record.exists()

    resumed = subprocess.run(
        [
            *base,
            "--harness",
            "codex",
            "--recovery-id",
            recovery["recovery_id"],
            "--expected-version",
            str(recovery["version"]),
            "--fallback-decision",
            "approve",
            "--non-interactive",
            "--json",
        ],
        cwd=repo_root,
        env=environment,
        input="resubmitted root task",
        capture_output=True,
        text=True,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    report = json.loads(resumed.stdout)
    assert report["output"] == "root-ok"
    assert [attempt["tier"] for attempt in report["attempts"]] == [
        "advanced",
        "flash",
    ]
    assert not record.exists()


def test_crash_abandoned_recovery_resumes_with_the_printed_version(tmp_path: Path):
    """The resume command printed before a crash must still work after it.

    recover_abandoned resets a crash-abandoned claim through a versioned CAS,
    so it bumps the version. Validating the caller's --expected-version against
    that post-reset number made the only command the tool ever prints fail
    permanently -- the exact case the reset exists to handle.
    """
    from manifest_model_policy import skill_command
    from manifest_model_policy.skill_recovery import SkillRecoveryStore

    store = SkillRecoveryStore(tmp_path / "state")
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    created = store.create(
        {
            "recovery_id": "a" * 32,
            "version": 1,
            "skill_path": str(skill),
            "harness": "codex",
            "remaining_tiers": ["flash"],
            "fallback_mode": "confirm",
            "attempts": [],
            "requires_task_resubmission": True,
            "state": "pending",
        }
    )
    recovery_id = created["recovery_id"]
    printed_version = created["version"]

    # The resume claims it, then the process is SIGKILLed: the OS drops the
    # lifetime flock, leaving the record "claimed" at a bumped version.
    _claimed, descriptor = store.claim(recovery_id, printed_version)
    os.close(descriptor)

    prepared = skill_command._prepare_resume(
        store,
        skill,
        "codex",
        recovery_id,
        printed_version,
        "approve",
        None,
        None,
        None,
        None,
        None,
    )

    assert prepared.effective_version is not None
    assert prepared.effective_version > printed_version
    assert store.read(recovery_id)["state"] == "pending"


def test_timeout_does_not_hang_when_a_child_outlives_termination(tmp_path: Path):
    """A survivor holding the pipes must not wedge the run forever.

    killpg can return EPERM for a LIVE process this user may not signal. The
    drain threads block until every pipe write end closes, so an unbounded
    join would hang the whole skill run -- worse than the crash it replaced.
    """
    import manifest_model_policy.skill_process as sp

    # Simulate "termination did not kill it": make the group kill a no-op.
    original = sp._terminate_process_group
    try:
        sp._terminate_process_group = lambda process: None

        skill = tmp_path / "SKILL.md"
        skill.write_text("---\nname: demo\n---\nDo the work.\n")
        config = _config()
        config["timeouts"] = {"default": 0.05}
        config["cli_agents"]["codex"] = {
            "binary": sys.executable,
            # Holds stdout/stderr open well past the timeout.
            "base_args": ["-c", "import time; time.sleep(8)"],
            "model_args": [],
            "skill_prompt_transport": "stdin",
            "skill_prompt_args": [],
        }

        started = time.monotonic()
        result = run_skill(
            skill,
            "private task",
            "codex",
            config=config,
            model_chain=("advanced",),
        )
        elapsed = time.monotonic() - started
    finally:
        sp._terminate_process_group = original

    assert elapsed < 6, f"run wedged on the surviving child ({elapsed:.1f}s)"
    assert result.failure == "transient"


def test_reject_refuses_to_delete_a_live_claim(tmp_path: Path):
    """store.delete is a version-only CAS -- it must not orphan a live run.

    recover_abandoned leaves a genuinely-claimed record untouched because its
    flock is held, so without a state guard a reject could delete the record
    out from under an in-progress run.
    """
    from manifest_model_policy import skill_command
    from manifest_model_policy.skill_recovery import SkillRecoveryStore

    store = SkillRecoveryStore(tmp_path / "state")
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    created = store.create(
        {
            "recovery_id": "b" * 32,
            "version": 1,
            "skill_path": str(skill),
            "harness": "codex",
            "remaining_tiers": ["flash"],
            "fallback_mode": "confirm",
            "attempts": [],
            "requires_task_resubmission": True,
            "state": "pending",
        }
    )
    recovery_id = created["recovery_id"]

    # A live run holds the claim (descriptor stays open -> flock held).
    claimed, descriptor = store.claim(recovery_id, created["version"])
    try:
        with pytest.raises(ValueError, match="active claim"):
            skill_command._prepare_resume(
                store,
                skill,
                "codex",
                recovery_id,
                claimed["version"],
                "reject",
                None,
                None,
                None,
                None,
                None,
            )
        # The live run's record must still be there.
        assert store.read(recovery_id)["state"] == "claimed"
    finally:
        os.close(descriptor)


def test_reject_refuses_while_a_claim_flock_is_held_even_if_state_reads_pending(
    tmp_path: Path,
):
    """State alone is not proof that no run is live.

    claim() takes the .claim flock BEFORE writing state="claimed" through a
    separate lock, so a record can read "pending" while a claimant is
    mid-flight. Deleting then would orphan that run.
    """
    import fcntl

    from manifest_model_policy import skill_command
    from manifest_model_policy.skill_recovery import SkillRecoveryStore

    store = SkillRecoveryStore(tmp_path / "state")
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\nDo the work.\n")
    created = store.create(
        {
            "recovery_id": "c" * 32,
            "version": 1,
            "skill_path": str(skill),
            "harness": "codex",
            "remaining_tiers": ["flash"],
            "fallback_mode": "confirm",
            "attempts": [],
            "requires_task_resubmission": True,
            "state": "pending",
        }
    )
    recovery_id = created["recovery_id"]

    # Exactly the mid-flight window: flock held, on-disk state still "pending".
    claim_path = (tmp_path / "state" / f"{recovery_id}.json").with_suffix(".claim")
    held = os.open(claim_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert store.read(recovery_id)["state"] == "pending"
        with pytest.raises(ValueError, match="active claim"):
            skill_command._prepare_resume(
                store,
                skill,
                "codex",
                recovery_id,
                created["version"],
                "reject",
                None,
                None,
                None,
                None,
                None,
            )
        assert store.read(recovery_id)["state"] == "pending"  # not deleted
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)
