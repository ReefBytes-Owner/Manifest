#!/usr/bin/env python3
"""Project check bodies for the node runtime and dependency-integrity controls
added in Phase 3 chunk C5: ``dependency.lock.node``, ``package.node-runtime``,
``dependency.audit.python``, ``dependency.audit.node``.

``dependency.lock.root`` reuses ``packages.py``'s existing ``uv lock --check``
body (same mechanism as ``dependency.lock.config``/``dependency.lock.delegate``,
just pointed at the repository root) and is NOT duplicated here.

The two ``dependency.audit.*`` checks transmit dependency metadata to an
external feed (PyPI/OSV, the npm registry) -- an outstanding human decision
(chunk C8, phase-3-5-decisions.md 3d). Their BODIES are built and registered
here so the BLOCKED path is real and testable, but neither is wired into any
profile; ``manifest check`` never runs them today.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from manifest_agent.checks import debt  # noqa: E402

PASS, FAIL, BLOCKED = 0, 2, 3
DEFAULT_BASELINE = "config/debt-baseline.json"
NODE_BUNDLE_ROOT = "plugins/stitch-design"
NODE_PROJECT = f"{NODE_BUNDLE_ROOT}/runtime/node"
CHECK_IDS = (
    "dependency.lock.node",
    "package.node-runtime",
    "dependency.audit.python",
    "dependency.audit.node",
)
_OFFLINE_WORDS = ("offline", "cache", "network", "econnrefused", "enotfound", "timeout")


class BlockedError(RuntimeError):
    """An offline tool, cache, feed, or source input is unavailable."""


def _which(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise BlockedError(f"{name} is unavailable")
    return executable


def _root(root_arg: Path) -> Path:
    try:
        return root_arg.expanduser().resolve(strict=True)
    except OSError as error:
        raise BlockedError(f"root unavailable: {error}") from error


def _node_project(root: Path) -> Path:
    declared = root / NODE_PROJECT
    if declared.is_symlink():
        raise BlockedError(f"node project escapes root or is symlinked: {NODE_PROJECT}")
    try:
        project = declared.resolve(strict=True)
    except OSError as error:
        raise BlockedError(
            f"node project unavailable: {NODE_PROJECT}: {error}"
        ) from error
    if not project.is_relative_to(root):
        raise BlockedError(f"node project escapes root: {NODE_PROJECT}")
    for name in ("package.json", "package-lock.json"):
        member = project / name
        if not member.is_file() or member.is_symlink():
            raise BlockedError(
                f"node project metadata unavailable: {NODE_PROJECT}/{name}"
            )
    return project


def _run(
    argv: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"command unavailable: {error}") from error


def _emit(result: subprocess.CompletedProcess[str]) -> str:
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return (result.stdout + result.stderr).lower()


def _isolated_output(
    root: Path, output_arg: Path | None
) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    """``--output`` is optional (registry checks never pass it, matching
    ``packages.py::_output``'s convention): a fresh, disjoint temp directory
    is used and cleaned up by the caller when none is given."""
    if output_arg is None:
        temporary = tempfile.TemporaryDirectory(prefix="manifest-dependency-check-")
        return Path(temporary.name).resolve(), temporary
    if output_arg.expanduser().is_symlink():
        raise BlockedError("output directory must not be a symlink")
    output = output_arg.expanduser().resolve(strict=False)
    if output == root or output.is_relative_to(root) or root.is_relative_to(output):
        raise BlockedError("output directory must be disjoint from root")
    output.mkdir(parents=True, exist_ok=True)
    return output, None


def _lock_node(root: Path) -> int:
    project = _node_project(root)
    npm = _which("npm")
    result = _run([npm, "ci", "--dry-run", "--ignore-scripts", "--offline"], project)
    diagnostic = _emit(result)
    if result.returncode == 0:
        return PASS
    if any(word in diagnostic for word in _OFFLINE_WORDS):
        raise BlockedError("npm offline lock validation prerequisites unavailable")
    return FAIL


def _tracked_bundle_files(root: Path) -> list[str]:
    """Git-tracked, repo-relative paths under ``NODE_BUNDLE_ROOT`` -- a
    filesystem walk would also pick up gitignored local artifacts (stray
    ``node_modules``, editor files) that must never enter the isolated
    copy (mirrors ``debt_checks.py::_tracked_paths``)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", NODE_BUNDLE_ROOT],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise BlockedError(f"cannot list tracked files: {error}") from error
    return [line for line in result.stdout.splitlines() if line]


def _isolated_bundle_copy(root: Path, output: Path) -> Path:
    """A self-contained copy of the tracked ``plugins/stitch-design`` tree.

    ``build.mjs`` does a static ESM ``import { build } from 'esbuild'`` --
    Node's ESM resolver ignores ``NODE_PATH`` entirely (a CJS-only legacy
    mechanism), so no environment trick can make that import see an
    ``isolated/node_modules`` sitting next to a COPY of just
    ``runtime/node``. ``build.mjs`` also resolves its own entry points and
    its drift-check target (``runtime/dist``) relative to the surrounding
    bundle root, not just its own directory. Copying the whole tracked
    bundle -- never the source worktree itself -- and running ``npm ci``
    and ``node build.mjs --check`` from the copy's own
    ``runtime/node/`` is what makes Node's ordinary node_modules walk find
    the isolated install with zero env overrides, while every relative
    path inside ``build.mjs`` still resolves the same way it does in the
    real tree.
    """
    files = _tracked_bundle_files(root)
    if not files:
        raise BlockedError(f"no tracked files under {NODE_BUNDLE_ROOT}")
    destination = output / "stitch-design-bundle"
    for relative in files:
        target = destination / Path(relative).relative_to(NODE_BUNDLE_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    project = destination / "runtime" / "node"
    for name in ("package.json", "package-lock.json", "build.mjs"):
        if not (project / name).is_file():
            raise BlockedError(
                f"node project metadata unavailable: {NODE_PROJECT}/{name}"
            )
    return project


_ESM_RESOLUTION_FAILURE_WORDS = (
    "cannot find module",
    "cannot find package",
    "err_module_not_found",
)


def _node_runtime(root: Path, output: Path) -> int:
    npm = _which("npm")
    node = _which("node")
    project = _isolated_bundle_copy(root, output)
    install = _run([npm, "ci", "--ignore-scripts", "--offline"], project)
    diagnostic = _emit(install)
    if install.returncode != 0:
        if any(word in diagnostic for word in _OFFLINE_WORDS):
            raise BlockedError("npm offline install prerequisites unavailable")
        return FAIL
    # No NODE_PATH: `build.mjs`'s node_modules resolution now walks up from
    # its own (isolated-copy) directory and finds the install above --
    # exactly what an unmodified `node build.mjs --check` invocation does.
    build = _run([node, "build.mjs", "--check"], project)
    build_diagnostic = _emit(build)
    if build.returncode == 0:
        return PASS
    if any(word in build_diagnostic for word in _ESM_RESOLUTION_FAILURE_WORDS):
        raise BlockedError("node build prerequisites unavailable")
    return FAIL


def _advisory_findings_python(root: Path, output: Path) -> list[debt.RawFinding]:
    uv = _which("uv")
    requirements = output / "requirements.txt"
    export = _run(
        [
            uv,
            "export",
            "--frozen",
            "--no-hashes",
            "--project",
            str(root),
            "-o",
            str(requirements),
        ],
        root,
    )
    if export.returncode != 0:
        raise BlockedError(
            f"uv export prerequisites unavailable: {export.stderr[:300]}"
        )
    pip_audit = _which("pip-audit")
    result = _run([pip_audit, "-r", str(requirements), "--format", "json"], root)
    if result.returncode not in (0, 1):
        raise BlockedError(
            f"pip-audit feed unreachable or errored (exit {result.returncode}): "
            f"{result.stderr[:300]}"
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as error:
        raise BlockedError(f"pip-audit produced unusable output: {error}") from error
    return [
        debt.RawFinding(
            check="advisory",
            path=f"{dependency['name']}=={dependency['version']}",
            anchor="",
            message=vulnerability["id"],
            line=0,
        )
        for dependency in payload.get("dependencies", [])
        for vulnerability in dependency.get("vulns", [])
    ]


def _advisory_id_from_via(via: dict) -> str:
    url = via.get("url", "")
    return url.rsplit("/", 1)[-1] if url else via.get("title", "unknown-advisory")


def _installed_node_version(project: Path, name: str) -> str | None:
    """The actually-installed version of ``name`` from ``package-lock.json``
    (lockfile v2/v3's ``packages`` map) -- ``npm audit --json``'s
    per-package ``range`` is the affected semver RANGE, not the pinned
    version this repo's lock resolved to; identity should key on what is
    actually installed, per phase-3-5-decisions.md 3d."""
    try:
        lock = json.loads((project / "package-lock.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = (lock.get("packages") or {}).get(f"node_modules/{name}")
    version = entry.get("version") if isinstance(entry, dict) else None
    return version if isinstance(version, str) else None


def _advisory_findings_node(root: Path) -> list[debt.RawFinding]:
    project = _node_project(root)
    npm = _which("npm")
    result = _run([npm, "audit", "--omit=dev", "--audit-level=high", "--json"], project)
    diagnostic = (result.stdout + result.stderr).lower()
    # npm audit legitimately exits 1 both for "vulnerabilities found" (a real
    # result) and for a network failure -- the exit code alone cannot tell
    # those apart, so the offline-word check runs before, not after, the
    # exit-code gate (unlike `_lock_node`/`_node_runtime`, whose analogous
    # success case is exit 0).
    if any(word in diagnostic for word in _OFFLINE_WORDS):
        raise BlockedError("npm audit feed unreachable")
    if result.returncode not in (0, 1):
        raise BlockedError(f"npm audit errored (exit {result.returncode})")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as error:
        raise BlockedError(f"npm audit produced unusable output: {error}") from error
    findings = []
    for name, entry in payload.get("vulnerabilities", {}).items():
        version = _installed_node_version(project, name) or entry.get(
            "range", "unknown"
        )
        for via in entry.get("via", []):
            if not isinstance(via, dict):
                continue
            findings.append(
                debt.RawFinding(
                    check="advisory",
                    path=f"{name}@{version}",
                    anchor="",
                    message=_advisory_id_from_via(via),
                    line=0,
                )
            )
    return findings


def _evaluate_advisories(
    findings: list[debt.RawFinding], root: Path, baseline_rel: str
) -> debt.DebtReport:
    try:
        baseline_cand = debt.Baseline.load(root / baseline_rel)
    except (ValueError, OSError) as error:
        raise BlockedError(f"invalid baseline: {error}") from error
    inputs = debt.EvaluationInputs(
        findings_cand=findings,
        findings_base=[],
        baseline_cand=baseline_cand,
        baseline_base=debt.Baseline(),
        today=date.today(),
        commit_date=lambda sha: debt.commit_date(root, sha),
        baseline_from_base=False,
    )
    return debt.evaluate(inputs)


def _render_advisories(report: debt.DebtReport) -> None:
    for v in report.fails:
        print(f"FAIL: {v.path} [{v.check}] {v.reason}: {v.message}", file=sys.stderr)
    for reason in report.blocked_reasons:
        print(f"BLOCKED: {reason}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_id", choices=CHECK_IDS)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    arguments = parser.parse_args(argv)
    temporary = None
    try:
        root = _root(arguments.root)
        if arguments.check_id == "dependency.lock.node":
            return _lock_node(root)
        output, temporary = _isolated_output(root, arguments.output)
        if arguments.check_id == "package.node-runtime":
            return _node_runtime(root, output)
        if arguments.check_id == "dependency.audit.python":
            findings = _advisory_findings_python(root, output)
        else:
            findings = _advisory_findings_node(root)
        report = _evaluate_advisories(findings, root, arguments.baseline)
        _render_advisories(report)
        return {"PASS": PASS, "FAIL": FAIL, "BLOCKED": BLOCKED}[report.status]
    except BlockedError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
