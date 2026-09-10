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
    if result.returncode != 0:
        raise MaterializationError(
            f"{argv[0]} exited {result.returncode}: {result.stdout[-4000:]}"
        )


def materialize_python_env(ctx: MaterializeContext, env_root: Path) -> None:
    """`uv sync --locked --no-dev` a python-env bundle into `env_root`.

    The engine is the store-attested `uv` (never ambient); the interpreter
    behind the venv is the ambient `python3` (out of scope for pinning --
    Correction 3, rule 2). Raises `MaterializationError` -- including when
    `uv` itself is not provisioned -- rather than ever falling back to a
    `PATH`-found `uv`.
    """
    resolved_uv = toolchain.resolve(
        "store:uv/bin/uv", lock=ctx.lock, store=ctx.store, platform=ctx.platform
    )
    if isinstance(resolved_uv, toolchain.BlockedReason):
        raise MaterializationError(resolved_uv.reason)
    env_root.mkdir(parents=True, exist_ok=True)
    project = ctx.repo_root / "config" / "toolchain"
    run_env = _engine_env(ctx.env, resolved_uv.executable.parent)
    run_env["UV_PROJECT_ENVIRONMENT"] = str(env_root)
    _run(
        [
            str(resolved_uv.executable),
            "sync",
            "--locked",
            "--no-dev",
            "--project",
            str(project),
        ],
        cwd=project,
        env=run_env,
    )


def _extract_subtree(data: bytes, prefix: str, destination: Path) -> None:
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
        _extract_subtree(archive, f"{node_exe_prefix}/lib/node_modules/npm/", npm_root)
    env_root.mkdir(parents=True, exist_ok=True)
    project = ctx.repo_root / "config" / "toolchain"
    for name in ("package.json", "package-lock.json"):
        (env_root / name).write_bytes((project / name).read_bytes())
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
