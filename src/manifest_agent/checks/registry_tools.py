"""Tool-record validation and normalization, split out of `registry.py`.

Kept separate purely for the Code Constitution's 500-line ceiling: `registry.py`
was already at the ceiling before `path_prepend` (C7i, phase-3-5-decisions.md
Correction 7 step 1) needed a new field validated and carried through into the
runtime tool record. Nothing here changes trust boundaries `registry.py`
itself does not already enforce -- `path_prepend` entries are validated with
the exact same `store:` grammar `toolchain.parse_store_executable` already
uses for `tools[NAME].executable`.
"""

from __future__ import annotations

from typing import Any

from .toolchain import parse_store_executable


def validate_tools(tools: dict[str, dict[str, Any]]) -> None:
    from .registry import _validate_argv, _validate_direct_invocation

    for tool_name, tool in tools.items():
        _validate_argv(tool["version_argv"], f"tool {tool_name!r} version")
        _validate_direct_invocation(tool["version_argv"], f"tool {tool_name!r} version")
        if "\0" in tool["executable"]:
            raise ValueError(f"tool {tool_name!r} executable contains NUL")
        for entry in tool.get("path_prepend", ()):
            if parse_store_executable(entry) is None:
                raise ValueError(
                    f"tool {tool_name!r} path_prepend entry must be a "
                    f"store: reference: {entry!r}"
                )


def normalized_tools(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The runtime `registry["tools"]` shape: every declared field, tupled
    where the source is a list, `path_prepend` defaulting to `()` for tools
    that do not declare it (most)."""
    return {
        name: {
            "executable": tool["executable"],
            "version_argv": tuple(tool["version_argv"]),
            "expected_version": tool["expected_version"],
            "required_modules": tuple(tool["required_modules"]),
            "path_prepend": tuple(tool.get("path_prepend", ())),
        }
        for name, tool in document["tools"].items()
    }
