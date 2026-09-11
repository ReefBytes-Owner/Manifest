# constitution: exempt C-SIZE — the shared six-harness lifecycle fixture keeps
# command ordering and receipt ownership comparable in one executable matrix.
"""Hermetic production-adapter lifecycle coverage for all supported harnesses."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from manifest_agent.adapters.antigravity import AntigravityAdapter
from manifest_agent.adapters.claude import ClaudeAdapter
from manifest_agent.adapters.codex import CodexAdapter, _catalog_owned_entries
from manifest_agent.adapters.cursor import CursorAdapter
from manifest_agent.adapters.devin import DevinAdapter
from manifest_agent.adapters.gemini import GeminiAdapter
from manifest_agent.contracts import (
    DOMAIN_BUNDLES,
    Capabilities,
    Components,
    load_addon_contracts,
    load_domain_contracts,
)
from manifest_agent.models import (
    CapabilityTier,
    DesiredState,
    HarnessReceipt,
    HarnessResult,
    MarketplaceSource,
    MarketplaceSourceKind,
    OwnedEntry,
    ResultState,
)
from manifest_agent.ownership import authenticate_codex_receipt
from manifest_agent.process import CommandRunner

_STUB = Path(__file__).parents[2] / "fixtures" / "harness_bins" / "harness-stub"
_REPOSITORY = Path(__file__).parents[3]
_HARNESS_EXECUTABLES = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "cursor": "cursor-agent",
    "antigravity": "agy",
    "devin": "devin",
}


def _desired(harness: str) -> DesiredState:
    empty_capabilities = Capabilities(
        dict.fromkeys(CapabilityTier, ()), dict.fromkeys(CapabilityTier, ())
    )

    def without_component_requirements(contract):
        return replace(
            contract,
            components=Components(
                contract.components.skills_root,
                contract.components.skills_include,
                (),
                (),
                (),
                (),
            ),
            capabilities=empty_capabilities,
        )

    contracts = tuple(
        without_component_requirements(contract)
        for contract in load_domain_contracts(_REPOSITORY / "plugins")
    )
    loaded_addons = load_addon_contracts(_REPOSITORY / "plugins")
    addons = (
        loaded_addons
        if harness in {"antigravity", "devin"}
        else tuple(
            without_component_requirements(contract) for contract in loaded_addons
        )
    )
    return DesiredState(
        release_version="0.2.0",
        source_commit="a" * 40,
        source=str(_REPOSITORY),
        marketplace_source=MarketplaceSource(
            MarketplaceSourceKind.LOCAL, str(_REPOSITORY), None
        ),
        release_root=_REPOSITORY,
        repository_url="https://example.invalid/Manifest.git",
        source_dirty=False,
        archive_sha256="b" * 64,
        contracts=contracts,
        selected_optional=frozenset(),
        requested_harnesses=(harness,),
        addon_contracts=addons,
    )


def _bundle_names(desired: DesiredState) -> tuple[str, ...]:
    return tuple(contract.name for contract in desired.all_contracts)


def _bundle_version(desired: DesiredState, name: str) -> str:
    return next(
        contract.version for contract in desired.all_contracts if contract.name == name
    )


def _response(
    argv: list[str], stdout: str = "", *, returncode: int = 0
) -> dict[str, object]:
    return {"argv": argv, "stdout": stdout, "returncode": returncode}


def _responses(harness: str, desired: DesiredState, phase: str) -> str:
    if harness == "claude":
        return _claude_responses(desired, phase)
    if harness == "codex":
        return _codex_responses(desired, phase)
    if harness == "gemini":
        return _gemini_responses(desired, phase)
    if harness == "cursor":
        return _cursor_responses(desired, phase)
    if harness == "antigravity":
        return _antigravity_responses(desired, phase)
    if harness == "devin":
        return _devin_responses(desired, phase)
    raise AssertionError(f"unsupported fixture harness: {harness}")


def _configured_environment(
    tmp_path: Path, harness: str, desired: DesiredState
) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / _HARNESS_EXECUTABLES[harness]
    executable.symlink_to(_STUB)
    log = tmp_path / "argv.jsonl"
    environment = {
        "HOME": str(tmp_path / "home"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HARNESS_STUB_LOG": str(log),
        "HARNESS_STUB_RESPONSES": _responses(harness, desired, "detect"),
        "HARNESS_STUB_STATE": str(tmp_path / "stub-state.json"),
    }
    if harness == "codex":
        codex_home = tmp_path / "home/.codex"
        environment["CODEX_HOME"] = str(codex_home)
        for contract in desired.all_contracts:
            destination = (
                codex_home / "plugins/cache/manifest" / contract.name / contract.version
            )
            shutil.copytree(desired.bundle_path(contract.name), destination)
    return environment


def _adapter(harness: str, environment: dict[str, str]):
    def which(name: str) -> str | None:
        return shutil.which(name, path=environment["PATH"])

    common = {"runner": CommandRunner(), "which": which, "env": environment}
    if harness == "claude":
        return ClaudeAdapter(**common)
    if harness == "codex":
        return CodexAdapter(**common)
    if harness == "gemini":
        return GeminiAdapter(**common)
    if harness == "cursor":
        return CursorAdapter(
            repository_url="https://example.invalid/Manifest.git", **common
        )
    if harness == "antigravity":
        return AntigravityAdapter(**common)
    if harness == "devin":
        return DevinAdapter(**common)
    raise AssertionError(f"unsupported fixture harness: {harness}")


def _local_adapter(harness: str):
    adapters = {
        "claude": ClaudeAdapter,
        "codex": CodexAdapter,
        "gemini": GeminiAdapter,
        "cursor": CursorAdapter,
        "antigravity": AntigravityAdapter,
        "devin": DevinAdapter,
    }
    return adapters[harness]()


def _receipt(
    harness: str,
    desired: DesiredState,
    plugin_ids: tuple[str, ...],
    environment: dict[str, str],
    installed_result: HarnessResult | None = None,
) -> HarnessReceipt:
    if harness in {"claude", "codex"}:
        entries = (OwnedEntry("marketplace", "manifest", "receipt"),)
        if harness == "codex":
            entries = _catalog_owned_entries(desired, environment)
    elif harness == "cursor":
        entries = (OwnedEntry("marketplace", desired.repository_url, "manifest"),)
    elif harness == "devin" and installed_result is not None:
        entries = installed_result.owned_entries
    else:
        entries = ()
    receipt = HarnessReceipt(
        harness=harness,
        adapter_version="1",
        native_version="fixture-1",
        plugin_ids=plugin_ids,
        owned_entries=entries,
        capabilities={},
        verified=True,
    )
    return (
        authenticate_codex_receipt(receipt, env=environment)
        if harness == "codex"
        else receipt
    )


def _prepare_devin_guidance(
    harness: str, desired: DesiredState, environment: dict[str, str]
) -> None:
    if harness != "devin":
        return
    rule = Path(environment["HOME"]) / ".codeium/windsurf/memories/global_rules.md"
    rule.parent.mkdir(parents=True, exist_ok=True)
    rule.write_bytes(
        (
            desired.bundle_path("manifest-i-have-adhd") / "devin/global-rule.md"
        ).read_bytes()
    )


def _expected_plugin_ids(harness: str, desired: DesiredState) -> tuple[str, ...]:
    bundle_names = _bundle_names(desired)
    if harness in {"claude", "codex"}:
        return tuple(f"{name}@manifest" for name in bundle_names)
    return bundle_names


def _assert_installed_state(
    harness: str,
    installed: HarnessResult,
    inspected: HarnessResult,
    plugin_ids: tuple[str, ...],
) -> None:
    if harness == "cursor":
        assert installed.state is ResultState.DEGRADED
        assert inspected.state is ResultState.DEGRADED
        return
    assert installed.state is ResultState.READY
    assert inspected.state is ResultState.READY
    if harness in {"antigravity", "devin"}:
        assert (
            installed.capabilities["manifest-i-have-adhd:skill:i-have-adhd"]
            == "verified"
        )
        assert (
            installed.capabilities["manifest-i-have-adhd:executable:python3"]
            == "verified"
        )
        assert (
            installed.capabilities["manifest-i-have-adhd:hook:adhd-session-start"]
            == "verified"
        )
        return
    assert installed.installed_plugin_ids == plugin_ids
    assert inspected.installed_plugin_ids == plugin_ids


@pytest.mark.parametrize("harness", tuple(_HARNESS_EXECUTABLES))
def test_response_driven_production_adapters_complete_receipt_lifecycles(
    tmp_path: Path, harness: str
) -> None:
    desired = _desired(harness)
    environment = _configured_environment(tmp_path, harness, desired)
    adapter = _adapter(harness, environment)

    detection = adapter.detect()
    log = tmp_path / "argv.jsonl"
    pre_inspection_start = len(_logged_argv(log))
    environment["HARNESS_STUB_RESPONSES"] = _responses(harness, desired, "pre")
    before = adapter.inspect(desired)
    _assert_pre_install_inspection(
        harness, desired, before, _logged_argv(log)[pre_inspection_start:]
    )
    environment["HARNESS_STUB_RESPONSES"] = _responses(harness, desired, "installed")
    _prepare_devin_guidance(harness, desired, environment)
    installed = adapter.install(desired)
    inspected = adapter.inspect(desired)
    environment["HARNESS_STUB_RESPONSES"] = _responses(harness, desired, "removed")
    plugin_ids = _expected_plugin_ids(harness, desired)
    removed = adapter.uninstall(
        _receipt(harness, desired, plugin_ids, environment, installed)
    )

    assert detection.present is True
    assert len(desired.contracts) == len(DOMAIN_BUNDLES)
    assert {contract.name for contract in desired.addon_contracts} == {
        "manifest-i-have-adhd"
    }
    _assert_installed_state(harness, installed, inspected, plugin_ids)
    assert removed.state is ResultState.READY
    argv = _logged_argv(log)
    assert [row[1:] for row in argv if row[1:] == ["--version"]]
    _assert_lifecycle_commands(harness, desired, argv)


@pytest.mark.native
@pytest.mark.parametrize("harness", tuple(_HARNESS_EXECUTABLES))
def test_local_native_cli_probe_reports_blocked_absence(harness: str) -> None:
    """`detect()` reports the true state of this host's optional native CLI.

    An absent optional CLI is a legitimate, correctly-reported environment
    state, not a test failure: the assertion below IS the behaviour under
    test (Detection(present=False, reason="<exe> CLI not present")) -- a
    prior version unconditionally `pytest.fail`ed on absence, turning any
    host missing one native CLI into a false red for the whole workstream.
    The live branch below only runs when the CLI is actually installed.
    """
    adapter = _local_adapter(harness)
    detection = adapter.detect()

    if detection.present:
        assert detection.executable is not None
        return

    assert detection.executable is None
    assert detection.version is None
    assert detection.reason == f"{_HARNESS_EXECUTABLES[harness]} CLI not present"


def _assert_lifecycle_commands(
    harness: str, desired: DesiredState, argv: list[list[str]]
) -> None:
    commands = [tuple(row[1:]) for row in argv]
    bundle_names = _bundle_names(desired)
    bundle_paths = tuple(str(desired.bundle_path(name)) for name in bundle_names)
    expected_installs = {
        "claude": [
            ("plugin", "install", f"{name}@manifest", "--scope", "user")
            for name in bundle_names
        ],
        "codex": [
            ("plugin", "add", f"{name}@manifest", "--json") for name in bundle_names
        ],
        "gemini": [
            ("extensions", "install", path, "--consent", "--skip-settings")
            for path in bundle_paths
        ],
        "cursor": [
            (
                "plugin",
                "marketplace",
                "add",
                desired.repository_url,
                "--git-ref",
                desired.source_commit,
            )
        ],
        "antigravity": [("plugin", "validate", path) for path in bundle_paths]
        + [("plugin", "link", "manifest", str(desired.release_root))]
        + [("plugin", "install", f"{name}@manifest") for name in bundle_names],
        "devin": [("plugins", "install", path, "--yes") for path in bundle_paths],
    }
    expected_removals = {
        "claude": [("plugin", "uninstall", f"{name}@manifest") for name in bundle_names]
        + [("plugin", "marketplace", "remove", "manifest")],
        "codex": [
            ("plugin", "remove", f"{name}@manifest", "--json") for name in bundle_names
        ]
        + [("plugin", "marketplace", "remove", "manifest", "--json")],
        "gemini": [("extensions", "uninstall", name) for name in bundle_names],
        "cursor": [("plugin", "marketplace", "remove", desired.repository_url)],
        "antigravity": [("plugin", "uninstall", name) for name in bundle_names],
        "devin": [("plugins", "remove", name) for name in bundle_names]
        + [("plugins", "prune")],
    }
    assert _commands_in_order(commands, expected_installs[harness])
    assert _commands_in_order(commands, expected_removals[harness])


def _assert_pre_install_inspection(
    harness: str,
    desired: DesiredState,
    result: HarnessResult,
    argv: list[list[str]],
) -> None:
    bundle_names = _bundle_names(desired)
    expected_commands = {
        "claude": [
            ("plugin", "marketplace", "list", "--json"),
            ("plugin", "list", "--json"),
        ],
        "codex": [
            ("plugin", "marketplace", "list", "--json"),
            ("plugin", "list", "--json"),
            # Observing served MCP servers is part of inspect; see
            # test_codex_mcp_inventory.py.
            ("mcp", "list", "--json"),
        ],
        "gemini": [
            ("extensions", "list", "--output-format", "json"),
            ("skills", "list", "--all"),
        ],
        "cursor": [("plugin", "marketplace", "list", "--format", "json")],
        "antigravity": [("plugin", "list")],
        "devin": [("plugins", "list")]
        + [("plugins", "info", name) for name in bundle_names],
    }
    expected_errors = {
        "claude": [f"missing required plugin: {name}@manifest" for name in bundle_names]
        + _missing_adapter_evidence(desired),
        "codex": [f"missing required plugin: {name}@manifest" for name in bundle_names]
        + _missing_adapter_evidence(desired),
        "gemini": [f"missing required extension: {name}" for name in bundle_names]
        + _missing_adapter_evidence(desired),
        "cursor": ["Cursor marketplace list must contain exactly one manifest source"],
        "antigravity": [f"missing required plugin: {name}" for name in bundle_names]
        + _missing_adapter_evidence(desired, "antigravity"),
        "devin": [f"missing required plugin: {name}" for name in bundle_names]
        + [
            "missing adapter evidence: manifest-i-have-adhd:hook:adhd-session-start",
            "missing adapter evidence: manifest-i-have-adhd:runtime:adhd-hook-runtime",
            "missing adapter evidence: manifest-i-have-adhd:guidance:adhd-always-on-guidance",
        ],
    }
    assert result.state is ResultState.BLOCKED
    assert [tuple(command[1:]) for command in argv] == expected_commands[harness]
    assert result.errors == tuple(expected_errors[harness])


def _missing_adapter_evidence(
    desired: DesiredState, harness: str | None = None
) -> list[str]:
    errors: list[str] = []
    for contract in desired.all_contracts:
        root = desired.bundle_path(contract.name) / contract.components.skills_root
        skill_paths = {
            path
            for pattern in contract.components.skills_include
            for path in root.glob(pattern)
            if path.is_file()
        }
        errors.extend(
            f"missing adapter evidence: {contract.name}:skill:{path.parent.name}"
            for path in sorted(skill_paths)
        )
        for component_type in ("agents", "hooks", "runtime", "guidance"):
            label = component_type.removesuffix("s")
            for component in getattr(contract.components, component_type):
                status = (
                    component.compatibility.get(harness)
                    if harness is not None and component.compatibility is not None
                    else None
                )
                if status is not None and status.mode in {"degraded", "unsupported"}:
                    if status.reason and status.reason not in errors:
                        errors.append(status.reason)
                    continue
                errors.append(
                    f"missing adapter evidence: {contract.name}:{label}:{component.id}"
                )
    return errors


def _unsupported_component_reasons(desired: DesiredState, harness: str) -> list[str]:
    reasons: list[str] = []
    for contract in desired.all_contracts:
        for component_type in ("agents", "hooks", "runtime", "guidance"):
            for component in getattr(contract.components, component_type):
                status = (
                    component.compatibility.get(harness)
                    if component.compatibility is not None
                    else None
                )
                if (
                    status is not None
                    and status.mode in {"degraded", "unsupported"}
                    and status.reason
                    and status.reason not in reasons
                ):
                    reasons.append(status.reason)
    return reasons


def _logged_argv(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def _commands_in_order(
    commands: list[tuple[str, ...]], expected: list[tuple[str, ...]]
) -> bool:
    """Match each expected exact argv once, while preserving adapter command order."""
    matched = [command for command in commands if command in expected]
    return matched == expected


def _fixture(responses: list[dict[str, object]]) -> str:
    return json.dumps(
        {
            "responses": responses,
            "default": {"stderr": "unconfigured harness argv", "returncode": 97},
        }
    )


def _claude_responses(desired: DesiredState, phase: str) -> str:
    bundle_names = _bundle_names(desired)
    marketplace = json.dumps(
        [{"name": "manifest", "source": "directory", "path": str(_REPOSITORY)}]
    )
    rows = [
        {
            "id": f"{name}@manifest",
            "version": _bundle_version(desired, name),
            "scope": "user",
            "enabled": True,
            "installPath": str(desired.bundle_path(name)),
        }
        for name in bundle_names
    ]
    inspect_responses = [
        _response(["plugin", "marketplace", "list", "--json"], marketplace),
        _response(["plugin", "list", "--json"], json.dumps(rows)),
    ]
    if phase == "detect":
        return _fixture([_response(["--version"], "0.2.0\n")])
    if phase == "pre":
        return _fixture(
            [inspect_responses[0], _response(["plugin", "list", "--json"], "[]")]
        )
    if phase == "installed":
        responses = [
            _response(
                ["plugin", "marketplace", "add", str(_REPOSITORY), "--scope", "user"]
            ),
            *inspect_responses,
        ]
        responses.extend(
            _response(["plugin", "install", f"{name}@manifest", "--scope", "user"])
            for name in bundle_names
        )
        return _fixture(responses)
    if phase == "removed":
        responses = [
            _response(["plugin", "uninstall", f"{name}@manifest"])
            for name in bundle_names
        ]
        responses.extend(
            [
                _response(["plugin", "list", "--json"], "[]"),
                _response(["plugin", "marketplace", "remove", "manifest"]),
            ]
        )
        return _fixture(responses)
    raise AssertionError(f"unsupported fixture phase: {phase}")


# constitution: exempt C-SIZE — one ordered native JSON transcript prevents
# fixture phases from silently diverging from Codex's marketplace lifecycle.
def _codex_responses(desired: DesiredState, phase: str) -> str:
    bundle_names = _bundle_names(desired)
    marketplace = json.dumps(
        {
            "marketplaces": [
                {
                    "name": "manifest",
                    "root": str(_REPOSITORY),
                    "marketplaceSource": {
                        "sourceType": "local",
                        "source": str(_REPOSITORY),
                    },
                }
            ]
        }
    )
    rows = [
        {
            "pluginId": f"{name}@manifest",
            "name": name,
            "marketplaceName": "manifest",
            "version": _bundle_version(desired, name),
            "installed": True,
            "enabled": True,
            "source": {"source": "local", "path": str(desired.bundle_path(name))},
        }
        for name in bundle_names
    ]
    inspect_responses = [
        _response(["plugin", "marketplace", "list", "--json"], marketplace),
        _response(["plugin", "list", "--json"], json.dumps({"installed": rows})),
    ]
    if phase == "detect":
        return _fixture([_response(["--version"], "0.2.0\n")])
    if phase == "pre":
        return _fixture(
            [
                inspect_responses[0],
                _response(["plugin", "list", "--json"], json.dumps({"installed": []})),
            ]
        )
    if phase == "installed":
        responses = [
            _response(
                ["plugin", "marketplace", "add", str(_REPOSITORY), "--json"],
                json.dumps(
                    {
                        "marketplaceName": "manifest",
                        "installedRoot": str(_REPOSITORY),
                        "alreadyAdded": False,
                    }
                ),
            ),
            inspect_responses[0],
            {
                "argv": ["plugin", "list", "--json"],
                "sequence": [
                    {"stdout": json.dumps({"installed": []})},
                    {"stdout": json.dumps({"installed": rows})},
                    {"stdout": json.dumps({"installed": rows})},
                ],
            },
        ]
        for name in bundle_names:
            responses.append(
                _response(
                    ["plugin", "add", f"{name}@manifest", "--json"],
                    json.dumps(
                        {
                            "pluginId": f"{name}@manifest",
                            "name": name,
                            "marketplaceName": "manifest",
                            "version": _bundle_version(desired, name),
                            "installedPath": str(desired.bundle_path(name)),
                            "installed": True,
                            "enabled": True,
                        }
                    ),
                )
            )
        return _fixture(responses)
    if phase == "removed":
        responses = [
            _response(
                ["plugin", "remove", f"{name}@manifest", "--json"],
                json.dumps(
                    {
                        "pluginId": f"{name}@manifest",
                        "name": name,
                        "marketplaceName": "manifest",
                    }
                ),
            )
            for name in bundle_names
        ]
        responses.extend(
            [
                _response(["plugin", "list", "--json"], json.dumps({"installed": []})),
                {
                    "argv": ["plugin", "marketplace", "list", "--json"],
                    "sequence": [
                        {"stdout": marketplace},
                        {"stdout": json.dumps({"marketplaces": []})},
                    ],
                },
                _response(
                    ["plugin", "marketplace", "remove", "manifest", "--json"],
                    json.dumps({"marketplaceName": "manifest", "installedRoot": None}),
                ),
            ]
        )
        return _fixture(responses)
    raise AssertionError(f"unsupported fixture phase: {phase}")


def _gemini_responses(desired: DesiredState, phase: str) -> str:
    bundle_names = _bundle_names(desired)
    rows = [
        {
            "name": name,
            "version": _bundle_version(desired, name),
            "path": str(desired.bundle_path(name)),
            "isActive": True,
        }
        for name in bundle_names
    ]
    skills: list[str] = ["Discovered Agent Skills:", ""]
    for contract in desired.all_contracts:
        root = desired.bundle_path(contract.name) / contract.components.skills_root
        for pattern in contract.components.skills_include:
            for path in sorted(root.glob(pattern)):
                skills.extend(
                    [
                        f"{path.parent.name} [Enabled]",
                        f"  Location: {path}",
                        "",
                    ]
                )
    inspect_responses = [
        _response(["extensions", "list", "--output-format", "json"], json.dumps(rows)),
        _response(["skills", "list", "--all"], "\n".join(skills)),
    ]
    if phase == "detect":
        return _fixture([_response(["--version"], "0.2.0\n")])
    if phase == "pre":
        return _fixture(
            [
                _response(["extensions", "list", "--output-format", "json"], "[]"),
                inspect_responses[1],
            ]
        )
    if phase == "installed":
        return _fixture(
            [
                *[
                    _response(
                        [
                            "extensions",
                            "install",
                            str(desired.bundle_path(name)),
                            "--consent",
                            "--skip-settings",
                        ]
                    )
                    for name in bundle_names
                ],
                *inspect_responses,
            ]
        )
    if phase == "removed":
        return _fixture(
            [_response(["extensions", "uninstall", name]) for name in bundle_names]
        )
    raise AssertionError(f"unsupported fixture phase: {phase}")


def _cursor_responses(desired: DesiredState, phase: str) -> str:
    marketplace = json.dumps(
        [
            {
                "name": "manifest",
                "gitUrl": desired.repository_url,
                "gitRef": desired.source_commit,
                "scope": "user",
            }
        ]
    )
    if phase == "detect":
        return _fixture([_response(["--version"], "0.2.0\n")])
    if phase == "pre":
        return _fixture(
            [_response(["plugin", "marketplace", "list", "--format", "json"], "[]")]
        )
    if phase == "installed":
        return _fixture(
            [
                _response(
                    [
                        "plugin",
                        "marketplace",
                        "add",
                        desired.repository_url,
                        "--git-ref",
                        desired.source_commit,
                    ]
                ),
                _response(
                    ["plugin", "marketplace", "list", "--format", "json"], marketplace
                ),
                _response(
                    ["plugin", "--help"],
                    "Commands:\n  marketplace  Manage plugin marketplaces\n",
                ),
            ]
        )
    if phase == "removed":
        return _fixture(
            [_response(["plugin", "marketplace", "remove", desired.repository_url])]
        )
    raise AssertionError(f"unsupported fixture phase: {phase}")


def _antigravity_responses(desired: DesiredState, phase: str) -> str:
    bundle_names = _bundle_names(desired)
    inventory = json.dumps(
        {
            "imports": [
                {
                    "name": name,
                    "source": "manifest",
                    "components": [
                        "skills",
                        *([]),
                    ],
                }
                for name in bundle_names
            ]
        }
    )
    if phase == "detect":
        return _fixture([_response(["--version"], "0.2.0\n")])
    if phase == "pre":
        return _fixture([_response(["plugin", "list"], '{"imports":[]}')])
    if phase == "installed":
        responses = [
            _response(["plugin", "validate", str(desired.bundle_path(name))])
            for name in bundle_names
        ]
        responses.append(
            _response(["plugin", "link", "manifest", str(desired.release_root)])
        )
        responses.extend(
            _response(["plugin", "install", f"{name}@manifest"])
            for name in bundle_names
        )
        responses.append(_response(["plugin", "list"], inventory))
        return _fixture(responses)
    if phase == "removed":
        return _fixture(
            [_response(["plugin", "uninstall", name]) for name in bundle_names]
        )
    raise AssertionError(f"unsupported fixture phase: {phase}")


def _devin_responses(desired: DesiredState, phase: str) -> str:
    bundle_names = _bundle_names(desired)
    installed_listing = "Installed plugins\n" + "\n".join(
        f"{name} {_bundle_version(desired, name)}" for name in bundle_names
    )
    info_responses: list[dict[str, object]] = []
    for contract in desired.all_contracts:
        root = desired.bundle_path(contract.name) / contract.components.skills_root
        skills = "\n".join(
            f"  - {path.parent.name}"
            for pattern in contract.components.skills_include
            for path in sorted(root.glob(pattern))
        )
        info_responses.append(
            _response(
                ["plugins", "info", contract.name],
                f"Plugin: {contract.name}\nversion: {contract.version}\nsource: {desired.bundle_path(contract.name)}\nSkills\n{skills}\n"
                + "Required plugins\n",
            )
        )
    if phase == "detect":
        return _fixture([_response(["--version"], "0.2.0\n")])
    if phase == "pre":
        return _fixture(
            [_response(["plugins", "list"], "No plugins installed.\n"), *info_responses]
        )
    if phase == "installed":
        responses = [
            _response(["plugins", "install", str(desired.bundle_path(name)), "--yes"])
            for name in bundle_names
        ]
        responses.extend(
            [_response(["plugins", "list"], installed_listing), *info_responses]
        )
        return _fixture(responses)
    if phase == "removed":
        return _fixture(
            [
                {
                    "argv": ["plugins", "list"],
                    "sequence": [
                        {"stdout": installed_listing},
                        {"stdout": "No plugins installed.\n"},
                        {"stdout": "No plugins installed.\n"},
                    ],
                },
                *[_response(["plugins", "remove", name]) for name in bundle_names],
                _response(["plugins", "prune"]),
            ]
        )
    raise AssertionError(f"unsupported fixture phase: {phase}")
