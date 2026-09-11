"""Guard: no test invokes `uv run`/`uv sync` against this repository's own
root (Correction 12, C7k step 5b, phase-3-5-decisions.md rule 1).

Why this exists: once `store:uv/bin` sits on `test.bats`'s PATH (C7k step 5),
a bats test that runs bare `uv run ...`/`uv sync ...` with its working
directory inside the candidate under test succeeds -- there IS a `uv` and
there IS a `pyproject.toml` at the candidate root -- and materializes a real
`.venv` there (measured at 6b1b0026: `plugin_migration.bats` and
`plugin_native_parity.bats` each did exactly this). The runner's strict
candidate-identity walk then correctly BLOCKs every later check on "unsafe
symlink: .venv/bin/python". `toolchain_cache.py::cache_environment`'s
`UV_PROJECT_ENVIRONMENT`/`UV_NO_SYNC` (rule 2) is a belt-and-braces backstop
for exactly this, not the fix -- the fix is that no test does this at all.
Tests that need the project's own interpreter resolve it through the store
(`tests/test_helper/store_python.bash::store_project_env_python`, or the
equivalent Python-side store lookup) instead.

Scope: only *executed* `uv run`/`uv sync` invocations are in scope --
docstrings, comments, and string-literal assertions about CI workflow YAML
(which runs on a GitHub Actions worker, never on this machine against this
checkout) are not real invocations and are not flagged.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BATS_ROOT = REPO_ROOT / "tests" / "bats"
PYTHON_ROOT = REPO_ROOT / "tests" / "python"

# This guard's own file: its fixtures below deliberately contain the exact
# shapes it flags, as strings rather than live invocations.
_SELF_EXEMPT_NAME = "test_uv_escape_guard.py"

# A real `uv run`/`uv sync` invocation whose working directory is NOT this
# repository's root (a stub `uv`, a different --project, or a tmp_path
# fixture project) is safe. Files below were audited by hand: each either
# stubs `uv` itself, targets a project other than the repo root, or is a
# docstring/comment/assertion-about-a-string rather than a live call.
ALLOWLIST: dict[str, str] = {
    "tests/bats/agent_roster_integration.bats": (
        "comment/skip message only ('uv sync --project configs/claude'); "
        "no invocation, and the target is configs/claude, not the repo root"
    ),
    "tests/bats/token_benchmark_skill.bats": (
        "uv is stubbed to fail loudly in setup(); the test asserts it is "
        "never actually invoked on the --cli-only/--report-only paths"
    ),
    "tests/bats/uv_sync_home_runtime.bats": (
        "uv is fully stubbed (MOCK_BIN/uv logs argv and exits 0); "
        "--project targets a mktemp sandbox HOME, never the repo root"
    ),
}


def _bats_violations(path: Path) -> list[tuple[int, str]]:
    violations = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"\brun\s+uv\s+(run|sync)\b", line):
            violations.append((lineno, line.strip()))
    return violations


def _is_uv_argv(node: ast.expr) -> bool:
    if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) >= 2:
        first, second = node.elts[0], node.elts[1]
        return (
            isinstance(first, ast.Constant)
            and first.value == "uv"
            and isinstance(second, ast.Constant)
            and second.value in ("run", "sync")
        )
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(re.match(r"^\s*uv\s+(run|sync)\b", node.value))
    return False


_SUBPROCESS_FUNCS = {"run", "Popen", "check_call", "check_output", "call"}


def _python_violations(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_subprocess_call = (
            isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_FUNCS
        ) or (isinstance(func, ast.Name) and func.id == "system")
        if not is_subprocess_call or not node.args:
            continue
        if _is_uv_argv(node.args[0]):
            violations.append((node.lineno, "executes uv run/sync directly"))
    return violations


def test_no_test_invokes_uv_against_this_repository_root():
    findings: list[str] = []
    for path in sorted(BATS_ROOT.glob("*.bats")):
        rel = str(path.relative_to(REPO_ROOT))
        if rel in ALLOWLIST:
            continue
        for lineno, snippet in _bats_violations(path):
            findings.append(f"{rel}:{lineno}: {snippet}")
    for path in sorted(PYTHON_ROOT.rglob("*.py")):
        if path.name == _SELF_EXEMPT_NAME:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if rel in ALLOWLIST:
            continue
        for lineno, reason in _python_violations(path):
            findings.append(f"{rel}:{lineno}: {reason}")
    assert not findings, (
        "test(s) invoke `uv run`/`uv sync` against the repository root -- "
        "use tests/test_helper/store_python.bash::store_project_env_python "
        "(or the store's project-env python directly) instead:\n" + "\n".join(findings)
    )


def test_allowlist_entries_still_exist_and_still_mention_uv():
    # An allow-list entry for a file that no longer exists, or no longer
    # mentions uv at all, is dead and should be removed rather than carried
    # forward as unexamined trust.
    for rel in ALLOWLIST:
        path = REPO_ROOT / rel
        assert path.exists(), f"stale allowlist entry: {rel} no longer exists"
        assert re.search(r"\buv\b", path.read_text()), (
            f"stale allowlist entry: {rel} no longer mentions uv"
        )


# --- Synthetic accept/reject pins, independent of the live tree -----------


def test_bats_scanner_flags_a_bare_uv_run_invocation():
    fixture = textwrap.dedent(
        """\
        #!/usr/bin/env bats
        @test "example" {
          run uv run python script.py
        }
        """
    )
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".bats", delete=False) as handle:
        handle.write(fixture)
        temp_path = Path(handle.name)
    try:
        assert _bats_violations(temp_path)
    finally:
        temp_path.unlink()


def test_bats_scanner_ignores_a_commented_out_invocation():
    fixture = textwrap.dedent(
        """\
        #!/usr/bin/env bats
        # run uv run python script.py
        @test "example" {
          true
        }
        """
    )
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".bats", delete=False) as handle:
        handle.write(fixture)
        temp_path = Path(handle.name)
    try:
        assert not _bats_violations(temp_path)
    finally:
        temp_path.unlink()


def test_python_scanner_flags_a_list_argv_invocation():
    source = textwrap.dedent(
        """
        import subprocess
        subprocess.run(["uv", "run", "python", "script.py"])
        """
    )
    tree = ast.parse(source)
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    )
    assert _is_uv_argv(call.args[0])


def test_python_scanner_ignores_a_docstring_mention():
    source = '''
def helper():
    """Run with: uv run --project configs/claude pytest foo.py"""
'''
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert calls == []
