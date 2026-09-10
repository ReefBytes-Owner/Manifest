"""Frozen-oracle contract: `hook.*` selectors exactly match `.pre-commit-config.yaml`.

Split out of test_check_profile_parity.py (C7f) to keep that file under its
500-line ceiling and to give this responsibility -- one hook's `files`/
`types`/`types_or`/`exclude` must match the check that mirrors it -- its own
module. Reuses `test_hook_selector_parity.UPSTREAM_DEFAULT_TYPES` for the
hooks whose `.pre-commit-config.yaml` entry declares no `types`/`types_or` at
all (the type restriction then comes only from the hook's own upstream
manifest, which this repository's YAML never states).
"""

from __future__ import annotations

from tests.python.manifest_agent._check_profile_oracle import (
    DISPOSITIONS,
    SUPERSEDED,
    _check_by_id,
    _raw_documents,
)
from tests.python.manifest_agent.test_hook_selector_parity import (
    UPSTREAM_DEFAULT_TYPES,
)


def _hook_sources(preservation: dict) -> dict[str, dict]:
    sources = {}
    for entry in preservation["source_entries"]:
        if entry["kind"] != "hook":
            continue
        check_id = entry["component_ids"][0]
        assert check_id not in sources
        sources[check_id] = entry["value"]
    return sources


def _expected_exclude(source: dict) -> str:
    patterns = []
    for boundary in (source["global"]["exclude"], source["hook"]["exclude"]):
        if boundary["present"]:
            patterns.append(boundary["value"])
    if not patterns:
        return r"$^"
    return "|".join(f"(?:{pattern})" for pattern in patterns)


def _expected_types(check_id: str, hook: dict) -> list[str]:
    if hook["types"]["present"]:
        return hook["types"]["value"]
    return list(UPSTREAM_DEFAULT_TYPES.get(check_id.removeprefix("hook."), ()))


def _assert_hook_contract(preservation: dict, registry: dict) -> None:
    by_id = _check_by_id(registry)
    for check_id, source in _hook_sources(preservation).items():
        if check_id in SUPERSEDED:  # hook.pyright: oracle-only, see _c5_ids.py
            continue
        hook = source["hook"]
        check = by_id[check_id]
        expected_files = hook["files"]["value"] if hook["files"]["present"] else ""
        expected_types_or = (
            hook["types_or"]["value"] if hook["types_or"]["present"] else []
        )
        assert check["inputs"] == ["."]
        assert check.get("include_regex", "") == expected_files
        assert check.get("exclude_regex", r"$^") == _expected_exclude(source)
        assert check.get("types", []) == _expected_types(check_id, hook)
        assert check.get("types_or", []) == expected_types_or
        assert tuple(check["argv"]) == tuple(DISPOSITIONS[check_id][0])


def test_hook_args_and_path_filters_exactly_match_frozen_oracle():
    preservation, registry = _raw_documents()

    _assert_hook_contract(preservation, registry)
