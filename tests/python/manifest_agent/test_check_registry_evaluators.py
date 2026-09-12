"""Direct-argv evaluator rejection contracts for project checks."""

from __future__ import annotations

import pytest

from manifest_agent.checks.registry import load_registry
from tests.python.manifest_agent.check_registry_fixtures import (
    _argv_entry_kwargs,
    _check,
    _preparation,
    _python_and_tool,
)
from tests.python.manifest_agent.check_registry_fixtures import (
    _registry_file as _registry_file,
)


@pytest.mark.parametrize(
    "target,executable,argv",
    [
        ("version", "bash", ["bash", "-ec", "printf ok"]),
        ("check", "sh", ["sh", "-lc", "printf ok"]),
        ("preparation", "zsh", ["zsh", "-fc", "printf ok"]),
    ],
)
def test_posix_shell_short_option_clusters_are_rejected(
    registry_file, target, executable, argv
):
    with pytest.raises(ValueError, match="string evaluation"):
        load_registry(registry_file(**_argv_entry_kwargs(target, executable, argv)))


@pytest.mark.parametrize(
    "argv",
    [
        ["bash", "-o", "pipefail", "-c", "printf ok"],
        ["bash", "-O", "extglob", "-c", "printf ok"],
    ],
)
def test_posix_value_options_do_not_hide_later_eval_flags(registry_file, argv):
    with pytest.raises(ValueError, match="string evaluation"):
        load_registry(registry_file(**_argv_entry_kwargs("check", "bash", argv)))


@pytest.mark.parametrize(
    "argv",
    [
        ["bash", "-o"],
        ["bash", "+O"],
        ["bash", "--rcfile"],
    ],
)
def test_posix_value_options_require_a_non_option_value(registry_file, argv):
    with pytest.raises(ValueError, match="requires a value"):
        load_registry(registry_file(**_argv_entry_kwargs("check", "bash", argv)))


@pytest.mark.parametrize("option", ["-eo", "-Oextglob"])
def test_ambiguous_posix_value_option_clusters_are_rejected(registry_file, option):
    argv = ["bash", option, "pipefail", "scripts/check.sh"]

    with pytest.raises(ValueError, match="ambiguous"):
        load_registry(registry_file(**_argv_entry_kwargs("check", "bash", argv)))


def test_posix_value_option_then_script_remains_allowed(registry_file):
    argv = ["bash", "-o", "pipefail", "scripts/check.sh", "-c", "value"]

    registry = load_registry(registry_file(**_argv_entry_kwargs("check", "bash", argv)))

    assert registry["checks"][0].argv == tuple(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["bash", "scripts/check.sh", "-c", "value"],
        ["bash", "--", "scripts/check.sh", "-c", "value"],
    ],
)
def test_posix_shell_stops_option_inspection_at_script_boundary(registry_file, argv):
    registry = load_registry(registry_file(**_argv_entry_kwargs("check", "bash", argv)))

    assert registry["checks"][0].argv == tuple(argv)


@pytest.mark.parametrize("flag", ["/c", "/K"])
def test_cmd_command_forms_are_rejected(registry_file, flag):
    kwargs = _argv_entry_kwargs("check", "cmd.exe", ["cmd.exe", flag, "echo ok"])

    with pytest.raises(ValueError, match="string evaluation"):
        load_registry(registry_file(**kwargs))


@pytest.mark.parametrize("switch", ["/cecho ok", "/KECHO ok"])
def test_concatenated_cmd_command_switches_are_rejected(registry_file, switch):
    kwargs = _argv_entry_kwargs("check", "cmd.exe", ["cmd.exe", switch])

    with pytest.raises(ValueError, match="string evaluation"):
        load_registry(registry_file(**kwargs))


@pytest.mark.parametrize(
    "executable,flag",
    [
        ("pwsh", "-c"),
        ("pwsh", "-Command"),
        ("powershell.exe", "-e"),
        ("powershell.exe", "-Enc"),
        ("pwsh", "-EncodedCommand:value"),
    ],
)
def test_powershell_command_and_encoded_command_forms_are_rejected(
    registry_file, executable, flag
):
    kwargs = _argv_entry_kwargs(
        "preparation", executable, [executable, flag, "ZQBjAGgAbwAgAG8AawA="]
    )

    with pytest.raises(ValueError, match="string evaluation"):
        load_registry(registry_file(**kwargs))


@pytest.mark.parametrize(
    "argv",
    [
        ["pwsh", "-ExecutionPolicy", "Bypass", "-Command", "Get-ChildItem"],
        [
            "pwsh",
            "-WorkingDirectory",
            "scripts",
            "-EncodedCommand",
            "ZQBjAGgAbwA=",
        ],
    ],
)
def test_powershell_value_options_do_not_hide_later_eval_flags(registry_file, argv):
    with pytest.raises(ValueError, match="string evaluation"):
        load_registry(registry_file(**_argv_entry_kwargs("check", "pwsh", argv)))


@pytest.mark.parametrize(
    "argv",
    [
        ["pwsh", "-ExecutionPolicy"],
    ],
)
def test_powershell_value_options_require_a_non_option_value(registry_file, argv):
    with pytest.raises(ValueError, match="requires a value"):
        load_registry(registry_file(**_argv_entry_kwargs("check", "pwsh", argv)))


@pytest.mark.parametrize("option", ["-NoP", "-UnknownOption"])
def test_unknown_or_ambiguous_powershell_options_are_rejected(registry_file, option):
    argv = ["pwsh", option, "scripts/check.ps1"]

    with pytest.raises(ValueError, match="unsupported PowerShell option"):
        load_registry(registry_file(**_argv_entry_kwargs("check", "pwsh", argv)))


def test_powershell_value_and_boolean_options_then_script_remain_allowed(registry_file):
    argv = [
        "pwsh",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "scripts/check.ps1",
        "-Command",
        "value",
    ]

    registry = load_registry(registry_file(**_argv_entry_kwargs("check", "pwsh", argv)))

    assert registry["checks"][0].argv == tuple(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["pwsh", "-File", "scripts/check.ps1", "-Command", "value"],
        ["pwsh", "-f", "scripts/check.ps1", "-EncodedCommand", "value"],
        ["pwsh", "scripts/check.ps1", "-Command", "value"],
    ],
)
def test_powershell_stops_option_inspection_at_script_boundary(registry_file, argv):
    registry = load_registry(registry_file(**_argv_entry_kwargs("check", "pwsh", argv)))

    assert registry["checks"][0].argv == tuple(argv)


@pytest.mark.parametrize("target", ["version", "check", "preparation"])
def test_direct_env_wrappers_are_rejected(registry_file, target):
    kwargs = _argv_entry_kwargs(target, "/usr/bin/env", ["/usr/bin/env", "python"])

    with pytest.raises(ValueError, match="env wrapper"):
        load_registry(registry_file(**kwargs))


@pytest.mark.parametrize("executable", ["bash", "pwsh"])
def test_direct_script_and_version_argv_remain_allowed(registry_file, executable):
    tools = _python_and_tool(executable)
    check = _check("lint.a", tool="reviewed", version="1.0.0")
    suffix = "ps1" if executable == "pwsh" else "sh"
    check["argv"] = [executable, f"scripts/check.{suffix}"]
    preparation = _preparation(
        argv=[executable, f"tools/generate.{suffix}"],
        tool="reviewed",
        version="1.0.0",
    )

    registry = load_registry(
        registry_file(
            tools=tools,
            checks=[check, _check("lint.b", dependencies=["lint.a"])],
            candidate_preparations=[preparation],
        )
    )

    assert registry["checks"][0].argv == (executable, f"scripts/check.{suffix}")
