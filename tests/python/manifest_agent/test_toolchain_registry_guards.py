"""Registry-level guards the 3a design requires: `manifest check` must never
be able to invoke provisioning, and the store-executable allow-list for
plain (non-`store:`) tool names is exactly the always-present interpreter
set plus repository-relative scripts -- never a bare PATH-resolved name."""

from __future__ import annotations

from pathlib import Path

import pytest

from manifest_agent.checks import toolchain
from manifest_agent.checks.registry import load_registry

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "project-checks.json"


def test_no_check_or_preparation_argv_invokes_provision():
    registry = load_registry(REGISTRY_PATH)
    for check in registry["checks"]:
        assert not any("provision" in argument for argument in check.argv), check.id
    for preparation in registry["candidate_preparations"]:
        assert not any("provision" in argument for argument in preparation.argv), (
            preparation.id
        )


def test_no_tool_executable_or_version_argv_invokes_provision():
    registry = load_registry(REGISTRY_PATH)
    for name, tool in registry["tools"].items():
        assert "provision" not in tool["executable"], name
        assert not any("provision" in argument for argument in tool["version_argv"]), (
            name
        )


def test_loading_the_real_registry_populates_the_lock_document():
    """The specific defect a review round caught: `load_registry` folded the
    lock's digest into `config_digest` but never handed the parsed lock
    content itself to the runner, so every `store:` tool would BLOCK as
    unattested even after a successful `manifest provision`. This loads the
    REAL `config/project-checks.json` + `config/toolchain.lock.json` --
    not a hand-built fixture -- and asserts the document actually arrived."""
    registry = load_registry(REGISTRY_PATH)
    assert registry["toolchain_lock"] == "config/toolchain.lock.json"
    assert len(registry["toolchain_lock_digest"]) == 64
    document = registry["toolchain_lock_document"]
    assert document["schema_version"] == 1
    assert "gitleaks" in document["tools"]
    assert document["tools"]["gitleaks"]["kind"] == "binary"


@pytest.mark.parametrize("name", ["python3", "bash"])
def test_always_present_interpreter_allow_list(name):
    """The allow-list a schema/registry migration to `store:` must respect --
    enumerated here so drift in `ALWAYS_PRESENT_EXECUTABLES` is a visible
    test diff, not a silent widening of what may skip the store."""
    assert toolchain.is_legal_plain_executable(name) is True


def test_allow_list_is_exactly_python3_and_bash():
    assert frozenset({"python3", "bash"}) == toolchain.ALWAYS_PRESENT_EXECUTABLES


@pytest.mark.parametrize(
    "name",
    ["ruff", "gitleaks", "shellcheck", "markdownlint-cli2", "yamllint", "pyright"],
)
def test_third_party_bare_names_are_not_on_the_allow_list(name):
    """These are exactly the PATH-trusted tools 3a exists to migrate off of
    (see C2); until migrated they stay plain names in the registry, but the
    allow-list mechanism itself must already refuse to call them legal."""
    assert toolchain.is_legal_plain_executable(name) is False


def test_repo_relative_scripts_remain_legal_without_the_store():
    for value in (
        "tests/lint/check_array_expansion.sh",
        "./node_modules/.bin/bats",
        "configs/claude/.venv/bin/manifest",
    ):
        assert toolchain.is_legal_plain_executable(value) is True
