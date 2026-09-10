"""tools/project_checks/dependency_checks.py: dependency.lock.node,
package.node-runtime, dependency.audit.python, dependency.audit.node.

npm/node/uv happen to be installed on THIS host. Most tests below fake the
external tool via `PATH` (mirroring `test_project_check_bodies.py::_fake_uv`)
so they stay deterministic regardless of ambient host state --
`tests/fixtures/advisory/*.json` supply the stub feed responses for the two
`dependency.audit.*` tests, no network involved. `package.node-runtime`'s
isolation property (no `NODE_PATH`, a real ESM `import` resolving only
inside the isolated copy) is NOT provable with a faked `node`: Node's ESM
resolver's behavior around `NODE_PATH` is exactly the property under test,
so `test_node_runtime_passes_with_real_node_and_isolated_esm_import` and its
FAIL sibling below run the REAL `npm`/`node` on this host against a small,
real, git-tracked fixture bundle.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools/project_checks/dependency_checks.py"
ADVISORY_FIXTURES = REPO_ROOT / "tests/fixtures/advisory"
NPM_AND_NODE_AVAILABLE = (
    shutil.which("npm") is not None and shutil.which("node") is not None
)


def _fake_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o755)


def _node_project(root: Path) -> Path:
    project = root / "plugins/stitch-design/runtime/node"
    project.mkdir(parents=True)
    (project / "package.json").write_text(json.dumps({"name": "fixture"}))
    (project / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3}))
    (project / "build.mjs").write_text(
        "const checkMode = process.argv.includes('--check');\n"
        "console.log(checkMode ? 'check ok' : 'build ok');\n"
    )
    return project


def _run(
    root: Path, check_id: str, *extra: str, path_prepend: Path
) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, "PATH": f"{path_prepend}:{os.environ['PATH']}"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), check_id, "--root", str(root), *extra],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


# --- dependency.lock.node ---------------------------------------------------


def test_lock_node_passes_when_npm_ci_dry_run_succeeds(tmp_path):
    root = tmp_path / "repo"
    _node_project(root)
    bin_dir = tmp_path / "bin"
    _fake_script(
        bin_dir / "npm",
        "import sys\n"
        "assert sys.argv[1:] == ['ci', '--dry-run', '--ignore-scripts', '--offline']\n"
        "raise SystemExit(0)\n",
    )
    result = _run(root, "dependency.lock.node", path_prepend=bin_dir)
    assert result.returncode == 0, result.stderr


def test_lock_node_blocked_when_offline_cache_missing(tmp_path):
    root = tmp_path / "repo"
    _node_project(root)
    bin_dir = tmp_path / "bin"
    _fake_script(
        bin_dir / "npm",
        "import sys\nprint('npm ERR! code ENOTCACHED', file=sys.stderr)\nraise SystemExit(1)\n",
    )
    result = _run(root, "dependency.lock.node", path_prepend=bin_dir)
    assert result.returncode == 3
    assert "BLOCKED" in result.stderr


def test_lock_node_fails_on_genuine_mismatch(tmp_path):
    root = tmp_path / "repo"
    _node_project(root)
    bin_dir = tmp_path / "bin"
    _fake_script(
        bin_dir / "npm",
        "import sys\nprint('npm ERR! lockfile out of date', file=sys.stderr)\nraise SystemExit(1)\n",
    )
    result = _run(root, "dependency.lock.node", path_prepend=bin_dir)
    assert result.returncode == 2


def test_lock_node_blocked_when_npm_absent(tmp_path):
    import os

    root = tmp_path / "repo"
    _node_project(root)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    env = {**os.environ, "PATH": str(empty_bin)}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "dependency.lock.node", "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 3
    assert "npm is unavailable" in result.stderr


# --- package.node-runtime ----------------------------------------------------
#
# `_isolated_bundle_copy` resolves its file set via `git ls-files` (never a
# raw filesystem walk -- see `debt_checks.py::_tracked_paths`), so every
# `package.node-runtime` fixture below must be a real git repo, not a bare
# directory tree.


def _git_commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "--quiet", "-m", message], check=True
    )


def _git_init(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "init", "--quiet"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)


def _git_tracked_node_bundle(root: Path) -> Path:
    project = _node_project(root)
    _git_init(root)
    _git_commit_all(root, "base")
    return project


def test_node_runtime_passes_end_to_end(tmp_path):
    root = tmp_path / "repo"
    _git_tracked_node_bundle(root)
    bin_dir = tmp_path / "bin"
    _fake_script(
        bin_dir / "npm",
        "import sys, pathlib\n"
        "assert sys.argv[1:] == ['ci', '--ignore-scripts', '--offline']\n"
        "pathlib.Path('node_modules').mkdir(exist_ok=True)\n"
        "raise SystemExit(0)\n",
    )
    _fake_script(
        bin_dir / "node",
        "import sys\n"
        "assert sys.argv[1] == 'build.mjs' and '--check' in sys.argv\n"
        "print('check ok')\nraise SystemExit(0)\n",
    )
    result = _run(root, "package.node-runtime", path_prepend=bin_dir)
    assert result.returncode == 0, result.stderr


def test_node_runtime_blocked_when_npm_offline_prerequisites_missing(tmp_path):
    root = tmp_path / "repo"
    _git_tracked_node_bundle(root)
    bin_dir = tmp_path / "bin"
    _fake_script(
        bin_dir / "npm",
        "import sys\nprint('npm ERR! network timeout', file=sys.stderr)\nraise SystemExit(1)\n",
    )
    _fake_script(bin_dir / "node", "raise SystemExit(0)\n")
    result = _run(root, "package.node-runtime", path_prepend=bin_dir)
    assert result.returncode == 3
    assert "npm offline install prerequisites unavailable" in result.stderr


def test_node_runtime_fails_when_build_check_fails(tmp_path):
    root = tmp_path / "repo"
    _git_tracked_node_bundle(root)
    bin_dir = tmp_path / "bin"
    _fake_script(bin_dir / "npm", "raise SystemExit(0)\n")
    _fake_script(
        bin_dir / "node",
        "import sys\nprint('SyntaxError: unexpected token', file=sys.stderr)\nraise SystemExit(1)\n",
    )
    result = _run(root, "package.node-runtime", path_prepend=bin_dir)
    assert result.returncode == 2


def _write_local_widget_package(bundle: Path) -> None:
    local_pkg = bundle / "local-pkg"
    local_pkg.mkdir(parents=True)
    (local_pkg / "package.json").write_text(
        json.dumps(
            {
                "name": "local-widget",
                "version": "1.0.0",
                "type": "module",
                "main": "index.mjs",
            }
        )
    )
    (local_pkg / "index.mjs").write_text(
        "export const widgetName = 'isolated-widget';\n"
    )


def _write_esm_node_project(bundle: Path) -> Path:
    project = bundle / "runtime" / "node"
    project.mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps(
            {
                "name": "fixture-node-runtime",
                "version": "1.0.0",
                "type": "module",
                "dependencies": {"local-widget": "file:../../local-pkg"},
            }
        )
    )
    (project / "build.mjs").write_text(
        "import { widgetName } from 'local-widget';\n"
        "if (process.argv.includes('--check')) {\n"
        "  console.log(`check ok: ${widgetName}`);\n"
        "}\n"
    )
    return project


def _real_esm_node_bundle(tmp_path: Path) -> Path:
    """A real, git-tracked ``plugins/stitch-design`` bundle whose
    ``runtime/node`` project statically ``import``s a package that exists
    ONLY via a ``file:`` dependency -- proving ``_isolated_bundle_copy`` +
    a REAL ``node`` process resolve the isolated ``node_modules`` with zero
    ``NODE_PATH`` override (a faked ``node`` cannot observe Node's own ESM
    resolver ignoring ``NODE_PATH`` -- that is exactly the property this
    fixture exists to exercise for real).
    """
    root = tmp_path / "repo"
    bundle = root / "plugins" / "stitch-design"
    _write_local_widget_package(bundle)
    project = _write_esm_node_project(bundle)
    # A real `npm install` (file: dependency -- no network) produces a real,
    # matching package-lock.json; hand-writing a v3 lockfile for a `file:`
    # dependency would not prove anything about real npm behavior anyway.
    subprocess.run(
        ["npm", "install"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    shutil.rmtree(project / "node_modules")  # the isolated copy must build its own
    _git_init(root)
    _git_commit_all(root, "base")
    return root


@pytest.mark.skipif(not NPM_AND_NODE_AVAILABLE, reason="npm/node not installed locally")
def test_node_runtime_passes_with_real_node_and_isolated_esm_import(tmp_path):
    root = _real_esm_node_bundle(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "package.node-runtime", "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "check ok: isolated-widget" in result.stdout
    # Isolation: the tracked project directory must never gain node_modules.
    tracked_node_modules = root / "plugins/stitch-design/runtime/node/node_modules"
    assert not tracked_node_modules.exists()


@pytest.mark.skipif(not NPM_AND_NODE_AVAILABLE, reason="npm/node not installed locally")
def test_node_runtime_fails_with_real_node_on_genuine_check_failure(tmp_path):
    """Real `npm ci` succeeds (the isolated `node_modules` is fully
    provisioned, proven by the ESM `import` itself succeeding); `build.mjs`'s
    own check logic then genuinely fails -- proving FAIL (not BLOCKED) is
    reported once npm/node ran fine but the artifact itself is wrong."""
    root = _real_esm_node_bundle(tmp_path)
    project = root / "plugins/stitch-design/runtime/node"
    (project / "build.mjs").write_text(
        "import { widgetName } from 'local-widget';\n"
        "if (process.argv.includes('--check') && widgetName !== 'not-the-real-widget') {\n"
        "  throw new Error('deliberate check failure');\n"
        "}\n"
    )
    _git_commit_all(root, "break check")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "package.node-runtime", "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2, result.stderr


# --- dependency.audit.python / .node: BLOCKED-when-feed-unreachable + advisory routing


def _init_git(root: Path) -> None:
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
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "--quiet", "-m", "base"], check=True
    )


def test_audit_python_blocked_when_feed_unreachable(tmp_path):
    root = tmp_path / "repo"
    _init_git(root)
    bin_dir = tmp_path / "bin"
    _fake_script(bin_dir / "uv", "raise SystemExit(0)\n")
    _fake_script(
        bin_dir / "pip-audit",
        "import sys\nprint('Connection refused', file=sys.stderr)\nraise SystemExit(2)\n",
    )
    result = _run(
        root,
        "dependency.audit.python",
        "--output",
        str(tmp_path / "output"),
        path_prepend=bin_dir,
    )
    assert result.returncode == 3
    assert "BLOCKED" in result.stderr


def test_audit_python_reports_fail_for_unexcused_advisory(tmp_path):
    root = tmp_path / "repo"
    _init_git(root)
    bin_dir = tmp_path / "bin"
    _fake_script(bin_dir / "uv", "raise SystemExit(0)\n")
    stub = (ADVISORY_FIXTURES / "pip-audit-stub.json").read_text()
    _fake_script(bin_dir / "pip-audit", f"print({stub!r})\nraise SystemExit(0)\n")
    result = _run(
        root,
        "dependency.audit.python",
        "--output",
        str(tmp_path / "output"),
        path_prepend=bin_dir,
    )
    assert result.returncode == 2, result.stderr
    assert "PYSEC-2024-0001" in result.stderr


def test_audit_node_blocked_when_feed_unreachable(tmp_path):
    root = tmp_path / "repo"
    _init_git(root)
    _node_project(root)
    bin_dir = tmp_path / "bin"
    _fake_script(
        bin_dir / "npm",
        "import sys\nprint('npm ERR! network timeout', file=sys.stderr)\nraise SystemExit(1)\n",
    )
    result = _run(
        root,
        "dependency.audit.node",
        "--output",
        str(tmp_path / "output"),
        path_prepend=bin_dir,
    )
    assert result.returncode == 3


def test_audit_node_reports_fail_for_unexcused_advisory(tmp_path):
    root = tmp_path / "repo"
    _init_git(root)
    _node_project(root)
    bin_dir = tmp_path / "bin"
    stub = (ADVISORY_FIXTURES / "npm-audit-stub.json").read_text()
    _fake_script(bin_dir / "npm", f"print({stub!r})\nraise SystemExit(1)\n")
    result = _run(
        root,
        "dependency.audit.node",
        "--output",
        str(tmp_path / "output"),
        path_prepend=bin_dir,
    )
    assert result.returncode == 2, result.stderr
    assert "GHSA-stub-0001" in result.stderr


# --- structure: audit checks are registered but never wired into a profile -


def test_dependency_audit_checks_are_not_in_any_profile():
    from manifest_agent.checks.registry import load_registry

    registry = load_registry(REPO_ROOT / "config/project-checks.json")
    ids_in_profiles = {
        check_id for ids in registry["profiles"].values() for check_id in ids
    }
    assert "dependency.audit.python" not in ids_in_profiles
    assert "dependency.audit.node" not in ids_in_profiles
    all_ids = {check.id for check in registry["checks"]}
    assert {"dependency.audit.python", "dependency.audit.node"} <= all_ids


# --- no TS project: the C5 scope-cut is data-backed, not just asserted -----


def test_no_tsconfig_json_tracked_means_no_typescript_compiler_check():
    """phase-3-5-decisions.md 3d: no TS compiler check because there is no
    TS project. If a REAL (non-template) tsconfig.json ever lands, this
    test starts failing -- that failure is the signal to re-evaluate the
    decision, not silently stay green. `project-scaffold`'s
    `templates/node/tsconfig.json` is a template this repo GENERATES for
    other people's projects, not a TS project of its own, so it is excluded
    the same way `.gitignore`-adjacent generated content is elsewhere in
    this suite (`debt_checks.py::_tracked_paths`'s docstring)."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "*tsconfig.json"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    real_project_configs = [
        line
        for line in result.stdout.splitlines()
        if line and "/templates/" not in line
    ]
    assert real_project_configs == []

    from manifest_agent.checks.registry import load_registry

    registry = load_registry(REPO_ROOT / "config/project-checks.json")
    for check in registry["checks"]:
        assert not any(
            Path(arg).name in ("tsc", "tsc.js") or arg == "tsc" for arg in check.argv
        )
