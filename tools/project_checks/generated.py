"""Invoke the repository's native non-mutating generated-output verifiers."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PASS = 0
FAIL = 2
BLOCKED = 3


class BlockedError(RuntimeError):
    """A verifier or required input is unavailable."""


def _context(arguments: argparse.Namespace) -> Path:
    try:
        root = arguments.root.expanduser().resolve(strict=True)
    except OSError as error:
        raise BlockedError(f"root unavailable: {error}") from error
    if not root.is_dir():
        raise BlockedError(f"root is not a directory: {root}")
    if arguments.output_dir is not None:
        output = arguments.output_dir.expanduser().resolve(strict=False)
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise BlockedError("output directory must be disjoint from root")
    return root


def _command(root: Path, check_id: str) -> tuple[str, ...]:
    python = sys.executable
    commands = {
        "generated.commands-doc": (
            python,
            str(root / "configs/claude/scripts/generate_commands_doc.py"),
            "--check",
        ),
        "generated.plugin-views": (
            python,
            str(root / "tools/generate_plugin_views.py"),
            "--check",
            "--repo-root",
            str(root),
        ),
        "generated.vendor": (
            python,
            str(root / "tools/vendor_bundle_dependencies.py"),
            "--check",
        ),
        "generated.capability-inventory": (
            python,
            str(root / "tools/render_capability_inventory.py"),
            "--check",
        ),
        "generated.capability-matrix": (
            python,
            str(root / "tools/render_plugin_capability_matrix.py"),
            "--check",
            "--inspection",
            str(root / "tests/fixtures/plugin_capability_inspection.json"),
        ),
        "generated.cursor": (
            "bash",
            str(root / "configs/claude/scripts/generate_cursor_rules.sh"),
            "--dry-run",
        ),
    }
    return commands[check_id]


def _preflight(root: Path, check_id: str) -> None:
    required = {
        "generated.commands-doc": (root / "configs/claude/scripts/command_catalog.py",),
        "generated.plugin-views": (),
        "generated.vendor": (
            root / "uv.lock",
            root
            / "plugins/manifest-code-quality/skills/smoke-manage/vendor/VENDOR.json",
        ),
        "generated.capability-inventory": (
            root / "src/manifest_agent/data/legacy_inventory.yml",
        ),
        "generated.capability-matrix": (
            root / "tests/fixtures/plugin_capability_inspection.json",
        ),
    }.get(check_id, ())
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise BlockedError(f"verifier inputs unavailable: {', '.join(missing)}")
    # generated.capability-inventory and generated.capability-matrix target
    # verifiers that bootstrap their own `sys.path` (``sys.path.insert(0,
    # ROOT / "src")``) instead of requiring manifest_agent to be pre-installed
    # in the running interpreter. The preflight probe must mirror that
    # bootstrap; otherwise it BLOCKs on environments where manifest_agent
    # isn't globally installed even though the real verifier would run fine.
    imports = {
        "generated.commands-doc": "import command_catalog",
        "generated.plugin-views": (
            "import yaml, manifest_model_policy, manifest_agent.command_catalog, "
            "manifest_agent.contracts, manifest_agent.plugin_view_renderers"
        ),
        "generated.capability-inventory": (
            "import sys; sys.path.insert(0, 'src'); import manifest_agent.migration"
        ),
        "generated.capability-matrix": (
            "import sys; sys.path.insert(0, 'src'); import manifest_agent.contracts"
        ),
    }.get(check_id)
    if imports is None:
        return
    probe_cwd = (
        root / "configs/claude/scripts"
        if check_id == "generated.commands-doc"
        else root
    )
    try:
        result = subprocess.run(
            (sys.executable, "-c", imports),
            cwd=probe_cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"verifier import probe unavailable: {error}") from error
    if result.returncode:
        raise BlockedError("verifier Python imports are unavailable")


def _cursor_preflight(root: Path) -> None:
    bash = shutil.which("bash")
    python = shutil.which("python3")
    if bash is None:
        raise BlockedError("bash is unavailable")
    if python is None:
        raise BlockedError("python3 is unavailable")
    required = (
        root / "configs/claude/scripts/generate_cursor_rules.sh",
        root / "configs/claude/scripts/generate_commands_doc.py",
        root / "configs/claude/scripts/generate_cursor_mcp.py",
        root / "configs/claude/scripts/generate_cursor_agents.py",
        root / "configs/claude/config/mcp_servers.yml",
    )
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise BlockedError(f"Cursor verifier inputs unavailable: {', '.join(missing)}")
    skills = root / "configs/claude/skills"
    if not skills.is_dir() or not any(skills.glob("*/SKILL.md")):
        raise BlockedError("Cursor skill inputs unavailable")
    agent_roots = (
        root / "configs/claude/agents",
        root / "configs/claude/agents-devpanel",
    )
    if not any(any(folder.glob("*.md")) for folder in agent_roots if folder.is_dir()):
        raise BlockedError("Cursor agent inputs unavailable")
    try:
        probe = subprocess.run(
            (python, "-c", "import yaml"),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"Python dependency probe unavailable: {error}") from error
    if probe.returncode:
        raise BlockedError("PyYAML is unavailable to Cursor verifier")
    try:
        compact = subprocess.run(
            (python, str(required[1]), "--compact"),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"commands-index verifier unavailable: {error}") from error
    if compact.returncode or not compact.stdout.strip():
        raise BlockedError("commands-index generation could not complete")


def _run(root: Path, check_id: str) -> int:
    if check_id == "generated.cursor":
        _cursor_preflight(root)
    command = _command(root, check_id)
    verifier = Path(command[1])
    if not verifier.is_file():
        raise BlockedError(f"verifier unavailable: {verifier.relative_to(root)!s}")
    _preflight(root, check_id)
    try:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BlockedError(f"verifier unavailable: {error}") from error
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    diagnostic = result.stdout + result.stderr
    unavailable = any(
        marker in diagnostic
        for marker in (
            "ModuleNotFoundError",
            "ImportError",
            "PermissionError",
            "FileNotFoundError",
        )
    )
    if check_id == "generated.cursor":
        required_markers = ("Cursor rules:", "Cursor mcp.json:", "Cursor agents:")
        missing = [marker for marker in required_markers if marker not in diagnostic]
        drift = any(marker in diagnostic for marker in ("[DRY-RUN]", "would remove:"))
        if missing:
            print(
                f"BLOCKED: Cursor verifier incomplete; missing output: {', '.join(missing)}",
                file=sys.stderr,
            )
        if unavailable:
            print(
                f"BLOCKED: verifier runtime unavailable (exit {result.returncode})",
                file=sys.stderr,
            )
        if drift:
            print("FAIL: Cursor generated outputs are out of sync", file=sys.stderr)
            return FAIL
        if unavailable or missing:
            return BLOCKED
        return PASS if result.returncode == 0 else FAIL
    if result.returncode != 0:
        if unavailable:
            raise BlockedError(
                f"verifier runtime unavailable (exit {result.returncode})"
            )
        return FAIL
    return PASS


CHECK_IDS = (
    "generated.commands-doc",
    "generated.plugin-views",
    "generated.vendor",
    "generated.capability-inventory",
    "generated.capability-matrix",
    "generated.cursor",
)

TASK7_DISPOSITIONS = {
    check_id: (
        (
            "python3",
            "tools/project_checks/generated.py",
            check_id,
            "--root",
            ".",
        ),
        "project",
    )
    for check_id in CHECK_IDS
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_id", choices=CHECK_IDS)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    arguments = parser.parse_args(argv)
    try:
        return _run(_context(arguments), arguments.check_id)
    except BlockedError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
