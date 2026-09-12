from dataclasses import FrozenInstanceError

import pytest

from manifest_agent.models import CommandResult
from manifest_agent.process import CommandRunner


def test_runner_never_uses_a_shell():
    result = CommandRunner().run(("python3", "-c", "print('ok')"))

    assert isinstance(result, CommandResult)
    assert result.stdout.strip() == "ok"
    assert result.argv[0] == "python3"


def test_runner_rejects_string_commands():
    with pytest.raises(TypeError, match="argv must be a sequence"):
        CommandRunner().run("printf unsafe")  # type: ignore[arg-type]


def test_runner_merges_environment_without_mutating_the_parent(monkeypatch):
    monkeypatch.setenv("MANIFEST_PARENT_VALUE", "parent")

    # subprocess-env: exempt -- CommandRunner.run() merges this on top of
    # os.environ.copy() internally (src/manifest_agent/process.py), so the
    # child always inherits the parent's PYTHONDONTWRITEBYTECODE.
    result = CommandRunner().run(
        (
            "python3",
            "-c",
            "import os; print(os.environ['MANIFEST_PARENT_VALUE'], "
            "os.environ['MANIFEST_CHILD_VALUE'])",
        ),
        env={"MANIFEST_CHILD_VALUE": "child"},
    )

    assert result.stdout.strip() == "parent child"


def test_runner_redacts_credentials_from_native_stderr():
    result = CommandRunner().run(
        (
            "python3",
            "-c",
            "import sys; sys.stderr.write('Authorization: Bearer native-secret')",
        )
    )

    assert "native-secret" not in result.stderr
    assert "[REDACTED]" in result.stderr


@pytest.mark.parametrize(
    "flag",
    [
        "--token super-secret",
        "--api-key API-KEY-VALUE",
        "--API_KEY UNDERSCORE-VALUE",
        "--password=PASSWORD-VALUE",
        "--credential CREDENTIAL-VALUE",
        "--credential=CREDENTIAL-EQUALS-VALUE",
        "--authorization AUTHORIZATION-VALUE",
        "--authorization=AUTHORIZATION-EQUALS-VALUE",
    ],
)
def test_runner_redacts_cli_flag_credentials_from_native_stderr(flag):
    result = CommandRunner().run(
        (
            "python3",
            "-c",
            "import sys; sys.stderr.write(sys.argv[1])",
            flag,
        )
    )

    assert flag.split()[-1].split("=")[-1] not in result.stderr
    assert "[REDACTED]" in result.stderr


def test_command_result_is_frozen():
    result = CommandResult(("true",), 0, "", "")

    with pytest.raises(FrozenInstanceError):
        result.returncode = 1  # type: ignore[misc]
