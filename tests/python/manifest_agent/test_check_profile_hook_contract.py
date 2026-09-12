"""Frozen commands and live selectors for migrated pre-commit hooks.

Commands retain the independent immutable oracle. Selectors come from the live
pre-commit configuration through `test_hook_selector_parity.py`, so reviewed
post-migration path changes remain guarded without being rejected as drift.
"""

from __future__ import annotations

from tests.python.manifest_agent._check_profile_oracle import (
    DISPOSITIONS,
    SUPERSEDED,
    _check_by_id,
    _raw_documents,
)
from tests.python.manifest_agent.test_hook_selector_parity import (
    _expected_selector,
    _pre_commit_hooks,
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


def _assert_hook_contract(preservation: dict, registry: dict) -> None:
    by_id = _check_by_id(registry)
    hooks, global_exclude = _pre_commit_hooks()
    for check_id in _hook_sources(preservation):
        if check_id in SUPERSEDED:  # hook.pyright: oracle-only, see _c5_ids.py
            continue
        check = by_id[check_id]
        expected = _expected_selector(
            hooks[check_id.removeprefix("hook.")], global_exclude
        )
        assert check["inputs"] == ["."]
        assert check.get("include_regex", "") == expected["include_regex"]
        assert tuple(check.get("types", ())) == expected["types"]
        assert tuple(check.get("types_or", ())) == expected["types_or"]
        assert check.get("exclude_regex", r"$^") == expected["exclude_regex"]
        assert tuple(check["argv"]) == tuple(DISPOSITIONS[check_id][0])


def test_hook_contract_matches_frozen_commands_and_live_selectors():
    preservation, registry = _raw_documents()

    _assert_hook_contract(preservation, registry)
