"""tools/project_checks/analysis_checks.py: types.python (pyright) and
security.semgrep -- store-resolved scanner + debt.py identity routing.

No pyright or semgrep is installed in this environment (verified: `which
pyright`/`which semgrep` both fail here). Every test below therefore either
(a) proves the honest BLOCKED path against the REAL, committed, unattested
`config/toolchain.lock.json` (`exe_sha256: null`), or (b) builds a FAKE
store + FAKE scanner executable (mirroring `toolchain_fixtures.py`'s pattern
for `test_toolchain_resolve.py`) so the real subprocess-invocation,
JSON-parsing, anchor-computation, and debt-evaluation code in
`analysis_checks.py` actually runs end to end -- only the third-party
scanner binary itself is a stand-in, exactly like `packages.py`'s tests fake
`uv`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools/project_checks/analysis_checks.py"

sys.path.insert(0, str(REPO_ROOT))
from manifest_agent.checks import toolchain
from tools.project_checks import analysis_checks


def _write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _fake_store(store: Path, bundle: str, relative: str, script_body: str) -> dict:
    """A one-executable fake store + lock consistent enough for
    ``toolchain.resolve()`` to accept -- schema mirrors ``toolchain_fixtures.py``.
    Returns the lock document; the caller writes it to the candidate's own
    ``config/toolchain.lock.json`` (``resolve_scanner`` reads the CANDIDATE's
    copy, never this test module's).
    """
    exe_bytes = f"#!{sys.executable}\n{script_body}".encode()
    exe_path = store / "tools" / bundle / relative
    exe_sha = _write(exe_path, exe_bytes)
    exe_path.chmod(0o755)
    platform_entry = {
        "url": "file://fixture",
        "sha256": "src-hash",
        "exe_sha256": exe_sha,
        "path_in_archive": ".",
    }
    lock = {
        "schema_version": 1,
        "tools": {
            bundle: {
                # A single-executable "binary"-style fixture, not a real
                # python-env/node-env distribution set (C7b: those kinds
                # verify a whole materialized env's distribution-set digest,
                # which this fixture never builds). The bundle KEY is still
                # "node-env"/"python-env" -- resolve_scanner looks it up by
                # that name -- only the verification algorithm differs.
                "kind": "binary",
                "version": "fixture",
                "platforms": {
                    "linux-x64": platform_entry,
                    "darwin-arm64": platform_entry,
                },
            }
        },
    }
    manifest = {
        "schema_version": 1,
        "lock_digest": toolchain.lock_digest(lock),
        "tools": {
            bundle: {
                "source_sha256": "src-hash",
                "executables": {
                    relative: {"path": f"tools/{bundle}/{relative}", "sha256": exe_sha}
                },
            }
        },
    }
    (store / "manifest.json").write_text(json.dumps(manifest))
    return lock


def _init_repo(root: Path, extra_setup=None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir()
    (root / "config" / "debt-baseline.json").write_text(
        json.dumps({"version": 2, "entries": []})
    )
    subprocess.run(["git", "-C", str(root), "init", "--quiet"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
    if extra_setup:
        extra_setup(root)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "--quiet", "-m", "base"], check=True
    )


def _run(
    root: Path, check_id: str, *extra: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(SCRIPT), check_id, "--root", str(root), *extra],
        capture_output=True,
        text=True,
        timeout=60,
        env=full_env,
    )


# --- BLOCKED path: proven against the REAL committed, unattested lock ------


def test_types_python_blocked_against_real_committed_unattested_lock(tmp_path):
    result = _run(
        REPO_ROOT,
        "types.python",
        env={"MANIFEST_TOOLCHAIN_STORE": str(tmp_path / "empty-store")},
    )
    assert result.returncode == 3
    assert "toolchain: node-env" in result.stderr
    assert "unattested" in result.stderr or "not provisioned" in result.stderr


def test_security_semgrep_blocked_against_real_committed_unattested_lock(tmp_path):
    result = _run(
        REPO_ROOT,
        "security.semgrep",
        env={"MANIFEST_TOOLCHAIN_STORE": str(tmp_path / "empty-store")},
    )
    assert result.returncode == 3
    assert "toolchain: python-env" in result.stderr
    assert "unattested" in result.stderr or "not provisioned" in result.stderr


def test_types_python_missing_pyrightconfig_is_blocked(tmp_path):
    root = tmp_path / "repo"
    _init_repo(root)
    result = _run(
        root, "types.python", env={"MANIFEST_TOOLCHAIN_STORE": str(tmp_path / "store")}
    )
    assert result.returncode == 3
    assert "pyrightconfig.json" in result.stderr


# --- fake-scanner path: exercises the real body end to end -----------------


def _fake_pyright_with_error(
    root: Path, relative_path: str, line0: int, message: str
) -> str:
    """A fake pyright script with the candidate root baked into its source at
    fixture-build time (never via the child's env: `_run_scanner` builds a
    deliberately minimal env -- `PATH`/`LC_ALL`/`LANG` only -- matching what
    the real body actually hands a resolved scanner)."""
    payload = json.dumps(
        {
            "generalDiagnostics": [
                {
                    "file": str(root / relative_path),
                    "severity": "error",
                    "message": message,
                    "range": {"start": {"line": line0, "character": 0}},
                }
            ]
        }
    )
    return f"print({payload!r})\n"


def _pyright_fixture_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    target_relpath = "tests/fixtures/types/pkg_b.py"
    fixture = REPO_ROOT / target_relpath

    def setup(r: Path) -> None:
        (r / "pyrightconfig.json").write_text("{}")
        dest = r / target_relpath
        dest.parent.mkdir(parents=True)
        dest.write_text(fixture.read_text())

    _init_repo(root, setup)
    return root, target_relpath


def _pyright_fixture_with_error(tmp_path: Path) -> tuple[Path, Path, str]:
    """A candidate repo, a fake store, and the target's repo-relative path --
    the fake pyright reports one error on line 15 (1-based) of the real
    `tests/fixtures/types/pkg_b.py` fixture (`use()`'s
    `return double(get_value())`; pyright itself reports 0-based lines)."""
    root, target_relpath = _pyright_fixture_repo(tmp_path)
    store = tmp_path / "store"
    script = _fake_pyright_with_error(
        root, target_relpath, 14, "Argument type mismatch"
    )
    lock = _fake_store(store, "node-env", "bin/pyright", script)
    (root / "config" / "toolchain.lock.json").write_text(json.dumps(lock))
    return root, store, target_relpath


def test_types_python_reports_fail_and_computes_python_anchor(tmp_path):
    """Real body, faked pyright binary AND a matching lock (so store
    resolution succeeds) -- proves subprocess invocation, JSON parsing, and
    anchor computation against the real `tests/fixtures/types/` cross-package
    fixture, not a mock of `analysis_checks.py` itself."""
    root, store, target_relpath = _pyright_fixture_with_error(tmp_path)
    result = _run(
        root, "types.python", "--json", env={"MANIFEST_TOOLCHAIN_STORE": str(store)}
    )
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "FAIL"
    (fail,) = payload["fails"]
    assert fail["path"] == target_relpath
    assert fail["anchor"] == "use"
    assert fail["reason"] == "new debt"


def test_types_python_clean_scan_passes(tmp_path):
    root, _ = _pyright_fixture_repo(tmp_path)
    store = tmp_path / "store"
    script = "import json\nprint(json.dumps({'generalDiagnostics': []}))\n"
    lock = _fake_store(store, "node-env", "bin/pyright", script)
    (root / "config" / "toolchain.lock.json").write_text(json.dumps(lock))

    result = _run(root, "types.python", env={"MANIFEST_TOOLCHAIN_STORE": str(store)})
    assert result.returncode == 0, result.stderr


def test_types_python_finding_excused_by_baseline_entry_passes(tmp_path):
    root, store, target_relpath = _pyright_fixture_with_error(tmp_path)
    env = {"MANIFEST_TOOLCHAIN_STORE": str(store)}
    first = _run(root, "types.python", "--json", env=env)
    identity = json.loads(first.stdout)["fails"][0]["identity"]

    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    entry = {
        "identity": identity,
        "check": "types.python",
        "path": target_relpath,
        "anchor": "use",
        "reason": "reviewed cross-package mismatch, tracked in a follow-up",
        "owner": "@reviewer",
        "introduced_base": head,
        "expires": (date.today() + timedelta(days=30)).isoformat(),
        "retired_base": None,
    }
    (root / "config" / "debt-baseline.json").write_text(
        json.dumps({"version": 2, "entries": [entry]})
    )
    second = _run(root, "types.python", env=env)
    assert second.returncode == 0, second.stderr


# --- security.semgrep: argv contract and anchor scoping ---------------------


def test_semgrep_argv_contains_metrics_off_and_local_config_only():
    argv = analysis_checks._semgrep_argv("/store/bin/semgrep")
    assert "--metrics=off" in argv
    assert argv[:2] == ["/store/bin/semgrep", "scan"]
    config_index = argv.index("--config")
    assert argv[config_index + 1] == analysis_checks.SEMGREP_CONFIG
    assert not any(value.startswith("p/") for value in argv)


def test_semgrepignore_excludes_only_the_fixture_directory():
    """Explicit, committed decision (not left to whatever semgrep's own
    default ignores happen to do): `.semgrepignore` -- unaffected by the
    argv's `--no-git-ignore`, which only disables .gitignore consultation
    -- excludes exactly the deliberately-vulnerable rule fixtures, and
    nothing else in `tests/`. Without this, a real `security.semgrep` run
    would FAIL on this repo's own conformance fixtures the moment semgrep
    is provisioned (C7)."""
    semgrepignore = REPO_ROOT / ".semgrepignore"
    assert semgrepignore.is_file()
    patterns = [
        line.strip()
        for line in semgrepignore.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert patterns == ["tests/fixtures/semgrep/"]


def test_python_anchor_finds_innermost_function():
    fixture = REPO_ROOT / "tests/fixtures/types/pkg_b.py"
    # `return double(get_value())` inside `use` is the fixture's 15th line.
    assert analysis_checks.python_anchor(fixture, 15) == "use"


def test_python_anchor_is_empty_for_unparsable_file(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text("def f(:\n")
    assert analysis_checks.python_anchor(broken, 1) == ""


@pytest.mark.skipif(
    __import__("shutil").which("semgrep") is None,
    reason="semgrep is not installed locally; real-tool verification lands in C7",
)
@pytest.mark.parametrize(
    "rule_dir",
    sorted((REPO_ROOT / "tests/fixtures/semgrep").iterdir()),
    ids=lambda p: p.name,
)
def test_semgrep_rule_fixtures_hit_exactly_once(rule_dir):
    rule_id = rule_dir.name
    for kind, expected_hits in (("positive", 1), ("negative", 0)):
        # Bash-rule fixtures are extensionless (relying on semgrep's shebang
        # sniffing) so they fall outside `hook.constitution-check`'s file-type
        # selection -- see the module docstring of `config/semgrep/manifest.yml`.
        target = next(p for p in rule_dir.iterdir() if p.name.startswith(kind))
        result = subprocess.run(
            [
                "semgrep",
                "scan",
                "--config",
                str(REPO_ROOT / "config/semgrep/manifest.yml"),
                "--metrics=off",
                "--json",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        payload = json.loads(result.stdout)
        hits = [r for r in payload["results"] if r["check_id"].endswith(rule_id)]
        assert len(hits) == expected_hits, (kind, target, payload)
