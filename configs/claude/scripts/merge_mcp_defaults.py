#!/usr/bin/env python3
"""Merge Manifest MCP defaults into a user JSON config without losing auth."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

LEGACY_CONTEXT7_URL = "https://mcp.context7.com/mcp/oauth"


def load_object(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON value must be an object")
    return value


def is_manifest_legacy_context7(name: str, config: object) -> bool:
    return (
        name == "context7"
        and isinstance(config, dict)
        and config.get("url") == LEGACY_CONTEXT7_URL
        and set(config).issubset({"url", "type"})
    )


def merge_defaults(source: dict, target: dict) -> bool:
    defaults = source.get("mcpServers", {})
    if not isinstance(defaults, dict):
        raise ValueError("source mcpServers must be an object")

    current = target.setdefault("mcpServers", {})
    if not isinstance(current, dict):
        raise ValueError("target mcpServers must be an object")

    changed = False
    for name, config in defaults.items():
        if name not in current or is_manifest_legacy_context7(name, current[name]):
            current[name] = deepcopy(config)
            changed = True
    return changed


def write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("--help", "-h"):
        print(
            "Usage: merge_mcp_defaults.py SOURCE_JSON TARGET_JSON\n\n"
            "Merges SOURCE_JSON's MCP server defaults into TARGET_JSON, writing "
            "TARGET_JSON privately (0600) only when it changed."
        )
        return 0
    if len(argv) != 2:
        print("Usage: merge_mcp_defaults.py SOURCE_JSON TARGET_JSON", file=sys.stderr)
        return 2

    source_path, target_path = map(Path, argv)
    try:
        source = load_object(source_path)
        target = load_object(target_path) if target_path.exists() else {}
        changed = merge_defaults(source, target)
        if changed or not target_path.exists():
            write_private_json(target_path, target)
        elif target_path.is_file():
            os.chmod(target_path, 0o600)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"merge_mcp_defaults.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
