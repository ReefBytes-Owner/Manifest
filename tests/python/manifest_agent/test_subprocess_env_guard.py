"""Guard: every Python-subprocess `env=` dict literal in `tests/python` must
carry bytecode isolation, either via `PYTHONDONTWRITEBYTECODE` written out
literally or by being built through `isolated_env()`
(`tests/python/manifest_agent/_subprocess_env.py`).

Why this exists: `manifest check`'s strict per-check candidate walk (C7d)
runs `test.python` -- this very suite -- inside a temporary candidate with
`PYTHONDONTWRITEBYTECODE=1`/`PYTHONPYCACHEPREFIX` set on its own child env,
so a bare `pytest --collect-only` never writes `__pycache__` into the
candidate. A test that spawns its OWN nested Python subprocess with a
hand-built `env={...}` dict silently drops those two variables, so the
nested interpreter writes bytecode straight into
`candidate/src/manifest_agent/**` or `candidate/tools/project_checks/**`,
and the strict walk (correctly) reports "candidate identity changed".

Scope is deliberately narrow: only `env=` dict LITERALS on calls whose argv
targets a Python interpreter (`sys.executable`, a bare `"python3"`/`"python"`
argv[0], or a `dict(...)` built the same way) are in scope -- non-Python
children (git, bats, shellcheck, node) never write `.pyc` files and are not
part of this defect's surface. A call that legitimately needs a minimal,
exact env (proving a production env-isolation or env-merge CONTRACT rather
than spawning a script that could import `manifest_agent`/`tools`) opts out
with a `# subprocess-env: exempt -- <reason>` comment on the line
immediately above the call or the `env=` keyword itself.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
_EXEMPT_MARKER = "subprocess-env: exempt"
_PYTHON_MARKERS = ("sys.executable", "python3", "'python'", '"python"')

# This guard file and the helper it enforces use of are not scanned: the
# guard's own synthetic fixtures intentionally contain "violations" as
# strings, not live code, and the helper has no subprocess calls at all.
_SELF_EXEMPT_NAMES = {"test_subprocess_env_guard.py", "_subprocess_env.py"}


def _is_python_targeting(call: ast.Call, source: str) -> bool:
    if not call.args:
        return False
    argv_src = ast.get_source_segment(source, call.args[0]) or ""
    return any(marker in argv_src for marker in _PYTHON_MARKERS)


def _has_dontwritebytecode_key(node: ast.AST) -> bool:
    return "PYTHONDONTWRITEBYTECODE" in ast.dump(node)


def _is_exempted(source_lines: list[str], lineno: int) -> bool:
    # Accept the marker on the flagged line itself or up to 3 lines above
    # the statement, so a comment can sit above either the call or the
    # `env=` keyword line.
    start = max(0, lineno - 4)
    window = source_lines[start:lineno]
    return any(_EXEMPT_MARKER in line for line in window)


def find_violations(source: str, *, filename: str = "<test>") -> list[tuple[int, str]]:
    """Return `(lineno, reason)` for every unguarded Python-subprocess
    `env=` dict literal in `source`. Pure AST analysis -- never executes
    or imports `source`."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    source_lines = source.splitlines()
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "env":
                continue
            value = kw.value
            is_literal_dict = isinstance(value, ast.Dict)
            is_dict_call = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "dict"
            )
            if not (is_literal_dict or is_dict_call):
                continue  # isolated_env(...) or any other builder: fine.
            if not _is_python_targeting(node, source):
                continue
            if _has_dontwritebytecode_key(value):
                continue
            if _is_exempted(source_lines, node.lineno):
                continue
            violations.append(
                (
                    node.lineno,
                    "Python-subprocess env= dict literal lacks "
                    "PYTHONDONTWRITEBYTECODE and is not built via "
                    "isolated_env() or marked "
                    f"'# {_EXEMPT_MARKER} -- <reason>'",
                )
            )
    return violations


def test_no_unguarded_python_subprocess_env_dicts_in_tests_python():
    all_violations: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path.name in _SELF_EXEMPT_NAMES:
            continue
        source = path.read_text()
        for lineno, reason in find_violations(source, filename=str(path)):
            rel = path.relative_to(TESTS_ROOT.parents[1])
            all_violations.append(f"{rel}:{lineno}: {reason}")
    assert not all_violations, "unguarded subprocess env= dict(s):\n" + "\n".join(
        all_violations
    )


# --- Synthetic accept/reject pins -------------------------------------
# These exercise the scanner directly, independent of the live tree, so a
# future edit to the scanner itself is pinned against both shapes.


def test_accepts_a_literal_dict_carrying_pythondontwritebytecode():
    source = textwrap.dedent(
        """
        import subprocess, sys
        subprocess.run(
            [sys.executable, "script.py"],
            env={"PATH": "/usr/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        """
    )
    assert find_violations(source) == []


def test_accepts_an_isolated_env_call():
    source = textwrap.dedent(
        """
        import subprocess, sys
        from tests.python.manifest_agent._subprocess_env import isolated_env
        subprocess.run(
            [sys.executable, "script.py"],
            env=isolated_env(PATH="/usr/bin"),
        )
        """
    )
    assert find_violations(source) == []


def test_accepts_an_exempt_marked_literal_dict():
    source = textwrap.dedent(
        """
        import subprocess, sys
        # subprocess-env: exempt -- proves the child's explicit-only env contract.
        subprocess.run(
            [sys.executable, "script.py"],
            env={"PATH": "/usr/bin"},
        )
        """
    )
    assert find_violations(source) == []


def test_ignores_non_python_argv_dict_literals():
    source = textwrap.dedent(
        """
        import subprocess
        subprocess.run(
            ["git", "status"],
            env={"PATH": "/usr/bin", "LC_ALL": "C"},
        )
        """
    )
    assert find_violations(source) == []


def test_rejects_a_bare_literal_dict_targeting_python():
    source = textwrap.dedent(
        """
        import subprocess, sys
        subprocess.run(
            [sys.executable, "script.py"],
            env={"PATH": "/usr/bin"},
        )
        """
    )
    violations = find_violations(source, filename="fixture.py")
    assert len(violations) == 1
    assert "isolated_env" in violations[0][1]


def test_rejects_a_dict_call_targeting_a_bare_python3_argv():
    source = textwrap.dedent(
        """
        import subprocess
        subprocess.run(
            ["python3", "-c", "print(1)"],
            env=dict(PATH="/usr/bin"),
        )
        """
    )
    violations = find_violations(source, filename="fixture.py")
    assert len(violations) == 1
