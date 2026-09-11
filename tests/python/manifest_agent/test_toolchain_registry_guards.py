"""Registry-level guards the 3a design requires: `manifest check` must never
be able to invoke provisioning, and the store-executable allow-list for
plain (non-`store:`) tool names is exactly the always-present interpreter
set plus repository-relative scripts -- never a bare PATH-resolved name.

C2c (phase-3-5-decisions.md "Corrections 2026-09-10" > "Correction 2")
closes the C2/C2b/C2c PATH-resolution migration with a non-reopenable
guard: `test_every_real_registry_tool_executable_is_store_or_legal_plain`
covers the registry-declared `tools[].executable` field (per the chunk's
own acceptance line), and
`test_no_check_body_resolves_an_engine_via_path_outside_the_allow_list`
covers the stronger, harder-to-game property -- that NO check body
anywhere under `tools/project_checks/` reaches for `shutil.which` (PATH)
to find an engine, with an explicit, justified allow-list for the four
legitimate exceptions. A check added later that imports `shutil` and calls
`.which(...)` on a new engine name fails the second test immediately,
without needing to know anything about the registry."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from manifest_agent.checks import toolchain
from manifest_agent.checks.registry import load_registry

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "project-checks.json"
PROJECT_CHECKS_DIR = Path(__file__).resolve().parents[3] / "tools" / "project_checks"

# (filename, enclosing function) -> why this `shutil.which(...)` call site is
# legitimate and does not need to resolve through the hash-verified store.
# Any call site not in this map fails the test below; any entry here whose
# call site disappears from the source also fails it (`==`, not `<=`) so the
# allow-list cannot silently drift wider than the source actually needs.
SHUTIL_WHICH_ALLOW_LIST = {
    ("generated.py", "_cursor_preflight"): (
        "bash/python3 -- both are in toolchain.ALWAYS_PRESENT_EXECUTABLES, "
        "the interpreter set 3a guarantees present without provisioning."
    ),
    ("structure.py", "_shell_syntax"): (
        "bash -- same always-present interpreter allow-list as above."
    ),
    ("dependency_checks.py", "_which"): (
        "uv/pip-audit/npm, but ONLY as the shared helper behind "
        "dependency.audit.python/dependency.audit.node -- both BLOCKED-by-"
        "decision (chunk C8, an outstanding human call on sending package "
        "metadata to PyPI/OSV/npm) and wired into no profile, so `manifest "
        "check` can never reach this call site today."
    ),
    ("tool_versions.py", "_resolved_executable"): (
        "the shared version-probe adapter every check's version_argv runs "
        "through. It never opens a second, independent PATH: for store: "
        "tools the runner has already restricted the child env's PATH to "
        "the store's own bin dirs (toolchain.resolved_env) before this "
        "adapter runs inside it; for the always-present-interpreter "
        "exemptions it is the same allow-listed lookup as the two rows "
        "above, just centralized."
    ),
}


def _shutil_which_call_sites() -> set[tuple[str, str]]:
    """AST-scan every `tools/project_checks/*.py` source file for
    `shutil.which(...)` call expressions, returning `(filename, enclosing
    function)` pairs. Module-level calls (no enclosing function) would
    appear as `(filename, "<module>")` -- none exist today."""
    sites: set[tuple[str, str]] = set()
    for path in sorted(PROJECT_CHECKS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not (
                isinstance(function, ast.Attribute)
                and function.attr == "which"
                and isinstance(function.value, ast.Name)
                and function.value.id == "shutil"
            ):
                continue
            sites.add((path.name, _enclosing_function(tree, node)))
    return sites


def _enclosing_function(tree: ast.Module, target: ast.Call) -> str:
    enclosing = "<module>"
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if any(child is target for child in ast.walk(node)):
            enclosing = node.name
    return enclosing


def test_no_check_body_resolves_an_engine_via_path_outside_the_allow_list():
    """The durable guard: without this, a future check body could import
    `shutil` and call `.which("some-new-engine")` and nothing would catch
    the reopened trust gap 3a/C2/C2b/C2c closed. `==` (not a subset check)
    both directions: a new, unlisted call site fails, and so does an
    allow-list entry whose call site no longer exists in the source."""
    assert _shutil_which_call_sites() == set(SHUTIL_WHICH_ALLOW_LIST)


# `cargo` (hook.cargo-fmt-check/hook.cargo-clippy) is a pre-existing,
# documented exception from chunk C2, not a C2c gap: both checks pin
# `types_or: ["rust"]` (config/project-checks.json), and this repository has
# zero `.rs` files, so `runner._selection_outcome`'s "project selection with
# path filters and zero matched inputs" branch reports NOT_APPLICABLE before
# `execute_check` ever resolves or runs `cargo` -- the bare name is declared
# but structurally unreachable (phase-3-5-decisions.md 3b: "dormant-language
# controls... NOT_APPLICABLE with zero-input evidence... excluded from the
# lock"). `test_registry_dormant_cargo_checks_never_select_inputs` below
# pins that structural claim directly against `has_path_filters`, so this
# allow-list entry cannot silently stop being true.
_DORMANT_LANGUAGE_TOOL_NAMES = frozenset({"hook.cargo-fmt-check", "hook.cargo-clippy"})


def test_every_real_registry_tool_executable_is_store_or_legal_plain():
    """C2c completion line (phase-3-5-decisions.md): every real-registry
    `tools[].executable` is either a `store:` reference or on the narrow
    always-present/repo-relative allow-list -- never a bare PATH-resolved
    third-party name that could actually be invoked."""
    registry = load_registry(REGISTRY_PATH)
    for name, tool in registry["tools"].items():
        if name in _DORMANT_LANGUAGE_TOOL_NAMES:
            continue
        executable = tool["executable"]
        is_store = toolchain.parse_store_executable(executable) is not None
        is_legal_plain = toolchain.is_legal_plain_executable(executable)
        assert is_store or is_legal_plain, (name, executable)


def test_registry_dormant_cargo_checks_never_select_inputs():
    """Pins the structural claim the allow-list above relies on: both dormant
    cargo checks declare a `rust` type filter, and this repository has no
    `.rs` files, so `has_path_filters` is true and would-be-selected inputs
    are empty -- `runner._selection_outcome` reports NOT_APPLICABLE without
    ever reaching `execute_check`'s argv/PATH resolution."""
    from manifest_agent.checks.path_filters import has_path_filters

    registry = load_registry(REGISTRY_PATH)
    repo_root = REGISTRY_PATH.parent.parent
    rust_files = list(repo_root.rglob("*.rs"))
    assert rust_files == []
    by_id = {check.id: check for check in registry["checks"]}
    for check_id in _DORMANT_LANGUAGE_TOOL_NAMES:
        check = by_id[check_id]
        assert check.selection == "project"
        assert tuple(check.types_or) == ("rust",)
        assert has_path_filters(check) is True


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


def test_no_distribution_version_probe_names_a_python_env_distribution_on_ambient_python():
    """C7c (coordinator round 2): a `distribution-version`/`--distribution`
    probe for a distribution some `python-env`-kind bundle installs must run
    under THAT bundle's own store python -- never ambient `python3`, which
    silently worked only because a dev checkout's own `.venv` happened to
    resolve first on `PATH`. `PyYAML`/`ruff`/`yamllint`/`pre-commit-hooks`
    are exactly the distributions `config/toolchain/pyproject.toml` (the
    `python-env` bundle) pins; `pytest` is what `test.python`/`test.hooks`
    actually run under (C7i, Correction 7: `store:project-env/bin/python -m
    pytest`), so its probe must run under `project-env`'s own python instead
    -- `python-env` still lists `pytest` as a leftover, unused pin (C7b), so
    accepting either bundle for `pytest` specifically does not weaken this
    guard for the other four distributions, which stay `python-env`-only."""
    registry = load_registry(REGISTRY_PATH)
    python_env_only_distributions = {
        "ruff",
        "yamllint",
        "pre-commit-hooks",
        "pyyaml",
        "pyyaml".upper(),
        "PyYAML",
    }
    project_env_distributions = {"pytest"}

    def _named_distribution(argv: tuple[str, ...]) -> str | None:
        if "distribution-version" in argv:
            return argv[argv.index("distribution-version") + 1]
        if "--distribution" in argv:
            return argv[argv.index("--distribution") + 1]
        return None

    for name, tool in registry["tools"].items():
        argv = tool["version_argv"]
        distribution = _named_distribution(argv)
        if distribution in python_env_only_distributions:
            assert argv[0] == "store:python-env/bin/python", (name, argv)
        elif distribution in project_env_distributions:
            assert argv[0] == "store:project-env/bin/python", (name, argv)


def test_every_file_url_lock_source_is_git_tracked():
    """C7g: `config/toolchain/package-lock.json` (a node-env `file://`
    source) was untracked by the repo-wide `package-lock.json` .gitignore
    rule -- every local `manifest provision` "worked" only because the
    untracked file happened to exist on that machine, and a fresh checkout
    crashed. `git ls-files --error-unmatch` is the same check a fresh
    checkout gets: an ignored or otherwise untracked `file://` source can
    never silently recur."""
    lock = toolchain.load_lock_file(REGISTRY_PATH.parent / "toolchain.lock.json")
    sources = set()
    for entry in (lock.get("tools") or {}).values():
        for platform_entry in (entry.get("platforms") or {}).values():
            url = platform_entry.get("url")
            if isinstance(url, str) and url.startswith("file://"):
                sources.add(url.removeprefix("file://"))
    assert sources, "expected at least one file:// lock source to check"
    repo_root = REGISTRY_PATH.parents[1]
    for relative in sorted(sources):
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", relative],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"{relative} is not git-tracked (stderr: {result.stderr.strip()})"
        )
