"""Real materialization of `python-env` / `node-env` toolchain-store bundles.

Split out of `toolchain_provision.py` to keep it under the Code
Constitution's 500-line ceiling. Every subprocess this module runs resolves
its engine through `toolchain.resolve()` first (`store:uv/bin/uv`,
`store:node/bin/node`) -- never an ambient `uv`/`node`/`npm` found on
`PATH`. Callers that want that invariant enforced in a test run these
functions with `PATH=""` in the environment they pass in; the store bin
directory each engine resolves to is appended explicitly, not inherited.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import toolchain

_SYNC_TIMEOUT_SECONDS = 600.0


class MaterializationError(ValueError):
    """A python-env/node-env materialization step failed or was blocked."""


@dataclass(frozen=True)
class MaterializeContext:
    """The arguments every materialization path needs together."""

    lock: Mapping
    store: Path
    platform: str
    repo_root: Path
    env: Mapping[str, str]


def _engine_env(base_env: Mapping[str, str], *bin_dirs: Path) -> dict[str, str]:
    path = os.pathsep.join(
        [*(str(d) for d in bin_dirs), *(base_env.get("PATH", "").split(os.pathsep))]
    )
    return {**base_env, "PATH": path}


def _run(argv: list[str], *, cwd: Path, env: Mapping[str, str]) -> None:
    """Run `argv`, raising `MaterializationError` (never a raw traceback) on
    a missing/unreadable executable or a hung process."""
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_SYNC_TIMEOUT_SECONDS,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MaterializationError(f"{argv[0]} could not execute: {error}") from error
    if result.returncode != 0:
        raise MaterializationError(
            f"{argv[0]} exited {result.returncode}: {result.stdout[-4000:]}"
        )


def _resolved_uv(ctx: MaterializeContext) -> Path:
    resolved_uv = toolchain.resolve(
        "store:uv/bin/uv", lock=ctx.lock, store=ctx.store, platform=ctx.platform
    )
    if isinstance(resolved_uv, toolchain.BlockedReason):
        raise MaterializationError(resolved_uv.reason)
    return resolved_uv.executable


def materialize_python_env(
    ctx: MaterializeContext,
    env_root: Path,
    *,
    project_relative: str = "config/toolchain",
) -> None:
    """`uv sync --locked --no-dev` a python-env bundle into `env_root`,
    directly against `project_relative`'s OWN real `pyproject.toml` /
    `uv.lock` -- used for bundles that need a real, installed project
    (its own `[project.scripts]` entry point), e.g. `config-env`'s
    `bin/manifest` (`configs/claude/`). `project-env` (the ROOT project)
    uses `materialize_project_env` below instead -- it deliberately never
    installs the local project package.

    The engine is the store-attested `uv` (never ambient); the interpreter
    behind the venv is the ambient `python3` (out of scope for pinning --
    Correction 3, rule 2). Raises `MaterializationError` -- including when
    `uv` itself is not provisioned -- rather than ever falling back to a
    `PATH`-found `uv`.
    """
    uv_executable = _resolved_uv(ctx)
    env_root.mkdir(parents=True, exist_ok=True)
    project = ctx.repo_root / project_relative
    run_env = _engine_env(ctx.env, uv_executable.parent)
    run_env["UV_PROJECT_ENVIRONMENT"] = str(env_root)
    _run(
        [str(uv_executable), "sync", "--locked", "--no-dev", "--project", str(project)],
        cwd=project,
        env=run_env,
    )


def materialize_project_env(ctx: MaterializeContext, env_root: Path) -> None:
    """`uv sync --frozen --all-groups --no-install-project` the ROOT
    project's dependency set into `env_root` -- Correction 7 step 1.

    `--frozen` is the trust anchor: it forbids uv from ever regenerating
    or even re-resolving the lock, so this ALWAYS installs exactly the
    packages the committed `uv.lock` -- whose bytes `_provision_env_entry`
    already hashed against the lock's `sha256` before calling here --
    pins, never whatever `pyproject.toml` alone would currently resolve
    to. A plain COPY of just `pyproject.toml` + `uv.lock` (isolated from
    the rest of the tree) was tried and rejected: the root project has a
    local path dependency (`configs/claude/scripts/manifest_model_policy`)
    that `uv sync` must find on disk relative to the project root
    regardless of `--no-install-project`, so the sync has to run against
    the real checkout `manifest provision` is invoked from -- `--frozen`
    is what keeps that checkout-relative sync pinned to the verified lock
    instead of trusting the live tree's current dependency graph.

    `--no-install-project` is deliberate: this env carries the project's
    DEPENDENCIES only, never a baked-in copy of `manifest_agent` itself --
    a check that runs `store:project-env/bin/python -m pytest` puts the
    CANDIDATE's own `src/` on `PYTHONPATH` so a candidate-local edit is
    what gets tested, not a stale copy this env would otherwise freeze at
    provision time.
    """
    uv_executable = _resolved_uv(ctx)
    env_root.mkdir(parents=True, exist_ok=True)
    run_env = _engine_env(ctx.env, uv_executable.parent)
    run_env["UV_PROJECT_ENVIRONMENT"] = str(env_root)
    _run(
        [
            str(uv_executable),
            "sync",
            "--frozen",
            "--all-groups",
            "--no-install-project",
            "--project",
            str(ctx.repo_root),
        ],
        cwd=ctx.repo_root,
        env=run_env,
    )


def extract_subtree(data: bytes, prefix: str, destination: Path) -> None:
    """Extract every archive member under `prefix` into `destination`,
    stripping `prefix` itself -- used to pull `lib/node_modules/npm` (which
    embeds `npm-cli.js` and every one of npm's own bundled dependencies) out
    of the already-hash-verified node archive without a second download."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive.getmembers():
            if not member.name.startswith(prefix) or member.name == prefix:
                continue
            relative = member.name[len(prefix) :].lstrip("/")
            if not relative or ".." in Path(relative).parts:
                continue
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            target.write_bytes(extracted.read())
            target.chmod(member.mode | 0o200)


def materialize_node_env(ctx: MaterializeContext, env_root: Path, fetcher) -> None:
    """`npm ci` a node-env bundle into `env_root`, driven entirely by
    store-attested tools: `node` from the store, and `npm-cli.js` extracted
    from the SAME hash-verified node archive `node`'s own bundle already
    trusts -- never an ambient `node`/`npm` on `PATH`."""
    resolved_node = toolchain.resolve(
        "store:node/bin/node", lock=ctx.lock, store=ctx.store, platform=ctx.platform
    )
    if isinstance(resolved_node, toolchain.BlockedReason):
        raise MaterializationError(resolved_node.reason)
    node_entry = (ctx.lock.get("tools") or {}).get("node") or {}
    node_platform = node_entry.get("platforms", {}).get(ctx.platform) or {}
    node_url = node_platform.get("url")
    if not node_url:
        raise MaterializationError("toolchain: node unattested for " + ctx.platform)
    archive = fetcher(node_url)
    if hashlib.sha256(archive).hexdigest() != node_platform.get("sha256"):
        raise MaterializationError("toolchain: node digest mismatch")
    node_exe_prefix = node_platform["path_in_archive"].rsplit("/bin/node", 1)[0]
    npm_root = ctx.store / f"tools/node-env/_npm-cli/{node_entry.get('version')}"
    if not (npm_root / "bin" / "npm-cli.js").is_file():
        extract_subtree(archive, f"{node_exe_prefix}/lib/node_modules/npm/", npm_root)
    env_root.mkdir(parents=True, exist_ok=True)
    project = ctx.repo_root / "config" / "toolchain"
    for name in ("package.json", "package-lock.json"):
        try:
            data = (project / name).read_bytes()
        except OSError as error:
            raise MaterializationError(
                f"node-env project file unavailable: {name}: {error}"
            ) from error
        (env_root / name).write_bytes(data)
    run_env = _engine_env(ctx.env, resolved_node.executable.parent)
    _run(
        [str(resolved_node.executable), str(npm_root / "bin" / "npm-cli.js"), "ci"],
        cwd=env_root,
        env=run_env,
    )


def node_env_console_scripts(env_root: Path, names: list[str]) -> dict[str, str]:
    """Map each requested console-script name to its relative path under
    `env_root/node_modules/.bin` -- `npm ci`'s own linked-bin symlinks."""
    scripts: dict[str, str] = {}
    for name in names:
        candidate = env_root / "node_modules" / ".bin" / name
        if candidate.exists():
            scripts[name] = str(candidate.relative_to(env_root))
    return scripts


def python_env_console_scripts(env_root: Path, names: list[str]) -> dict[str, str]:
    """Map each requested console-script name to its relative path under
    `env_root/bin` -- `uv sync`'s own generated launchers."""
    scripts: dict[str, str] = {}
    for name in names:
        candidate = env_root / "bin" / name
        if candidate.exists():
            scripts[name] = str(candidate.relative_to(env_root))
    return scripts


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
