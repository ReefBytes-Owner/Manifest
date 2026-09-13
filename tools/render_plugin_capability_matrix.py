#!/usr/bin/env python3
"""Render checked release evidence for every authoritative plugin contract.

This rendering is deliberately contract evidence, not a substitute for the
protected live workflow.  A live report may be supplied with ``--inspection``;
unknown, skipped, or unverified records make ``--check`` fail instead of
turning a release gate green by omission.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from manifest_agent.contracts import (  # noqa: E402
    DOMAIN_BUNDLES,
    CapabilityTier,
    load_addon_contracts,
    load_domain_contracts,
)

HARNESSES = ("claude", "codex", "gemini", "cursor", "antigravity", "devin")
_COMPONENT_KINDS = (
    ("skill", "skills"),
    ("agent", "agents"),
    ("hook", "hooks"),
    ("runtime", "runtime"),
    ("guidance", "guidance"),
)
_ALLOWED_PREFIXES = ("READY", "DEGRADED(", "BLOCKED(", "N/A(")


class MatrixError(ValueError):
    """Evidence is incomplete or malformed."""


def _status(status: Any, *, optional: bool = False) -> str:
    if optional:
        return "N/A(contract optional; not selected)"
    if status.mode in {"native", "generated", "imported"}:
        return "READY"
    if status.mode == "not_applicable":
        return f"N/A({status.reason or 'harness-specific contract surface'})"
    if status.mode == "degraded":
        return f"DEGRADED({status.reason or 'contract-declared degradation'})"
    if status.mode == "unsupported":
        return f"BLOCKED({status.reason or 'required contract surface unsupported'})"
    return f"BLOCKED(unknown contract compatibility mode {status.mode!r})"


def _inspection_records(document: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if document is None:
        return {
            harness: {"state": "BLOCKED(adapter inspection missing)"}
            for harness in HARNESSES
        }
    provenance = document.get("provenance")
    if provenance not in {"native-adapter-inspection", "synthetic-fixture"}:
        raise MatrixError(
            "inspection evidence must declare native-adapter-inspection or synthetic-fixture provenance"
        )
    records = document.get("harnesses")
    if not isinstance(records, dict):
        raise MatrixError("inspection evidence must contain a harnesses object")
    validated: dict[str, dict[str, Any]] = {}
    for harness in HARNESSES:
        record = records.get(harness)
        if not isinstance(record, dict):
            raise MatrixError(f"inspection is missing harness {harness!r}")
        state, version, verified = (
            record.get("state"),
            record.get("version"),
            record.get("verified"),
        )
        if not isinstance(state, str) or not state.startswith(_ALLOWED_PREFIXES):
            raise MatrixError(f"inspection {harness!r} has an invalid state")
        if not isinstance(version, str) or not version.strip() or verified is not True:
            raise MatrixError(
                f"inspection {harness!r} is missing a verified native version"
            )
        plugin_ids = record.get("installed_plugin_ids")
        components = record.get("components")
        capabilities = record.get("capabilities")
        if not isinstance(plugin_ids, list) or not all(
            isinstance(item, str) for item in plugin_ids
        ):
            raise MatrixError(f"inspection {harness!r} must list installed plugin IDs")
        if not isinstance(components, dict) or not isinstance(capabilities, dict):
            raise MatrixError(
                f"inspection {harness!r} must map plugin components and capabilities"
            )
        for label, values in (
            ("components", components),
            ("capabilities", capabilities),
        ):
            if any(
                not isinstance(bundle, str)
                or not isinstance(entries, list)
                or not all(isinstance(entry, str) for entry in entries)
                for bundle, entries in values.items()
            ):
                raise MatrixError(
                    f"inspection {harness!r} has invalid {label} evidence"
                )
        validated[harness] = record
    return validated


def _evidence_status(
    record: dict[str, Any], bundle: str, kind: str, identifier: str
) -> str | None:
    """Return a precise blocking reason when verified evidence does not join."""
    state = record["state"]
    if state != "READY" and not all(
        key in record for key in ("installed_plugin_ids", "components", "capabilities")
    ):
        return state
    if bundle not in record["installed_plugin_ids"]:
        return f"BLOCKED(plugin {bundle!r} is not installed)"
    collection = (
        "components"
        if kind in {"skill", "agent", "hook", "runtime", "guidance"}
        else "capabilities"
    )
    expected = f"{kind}:{identifier}"
    observed = record[collection].get(bundle, [])
    if expected not in observed:
        return f"BLOCKED({collection} evidence missing {bundle}:{expected})"
    return None


def _append_row(
    rows: list[tuple[str, str, list[str]]],
    inspection_records: dict[str, dict[str, Any]],
    identity: str,
    source: str,
    statuses: dict[str, Any],
    bundle_name: str,
    evidence_kind: str,
    evidence_id: str,
    optional: bool = False,
) -> None:
    """Append one verified contract row to the capability matrix."""
    cells = []
    for harness in HARNESSES:
        contract_state = _status(statuses[harness], optional=optional)
        if contract_state != "READY":
            cells.append(contract_state)
            continue
        evidence_state = _evidence_status(
            inspection_records[harness], bundle_name, evidence_kind, evidence_id
        )
        cells.append(evidence_state or "READY")
    if any(not cell or not cell.startswith(_ALLOWED_PREFIXES) for cell in cells):
        raise MatrixError(f"{identity}: blank or unverified harness evidence")
    rows.append((identity, source, cells))


def _rows(inspection: dict[str, Any] | None = None) -> list[tuple[str, str, list[str]]]:
    domains = load_domain_contracts(ROOT / "plugins")
    if tuple(contract.name for contract in domains) != DOMAIN_BUNDLES:
        raise MatrixError("domain contracts are incomplete or unexpectedly ordered")
    contracts = (*domains, *load_addon_contracts(ROOT / "plugins"))
    rows: list[tuple[str, str, list[str]]] = []
    inspection_records = _inspection_records(inspection)
    for contract in contracts:
        skill_names: set[str] = set()
        root = ROOT / "plugins" / contract.name / contract.components.skills_root
        for include in contract.components.skills_include:
            skill_names.update(
                path.parent.name for path in root.glob(include) if path.is_file()
            )
        for name in sorted(skill_names):
            _append_row(
                rows,
                inspection_records,
                f"{contract.name}:skill:{name}",
                "contract skill",
                dict(contract.compatibility),
                contract.name,
                "skill",
                name,
            )
        for kind, attribute in _COMPONENT_KINDS[1:]:
            for component in sorted(
                getattr(contract.components, attribute), key=lambda item: item.id
            ):
                _append_row(
                    rows,
                    inspection_records,
                    f"{contract.name}:{kind}:{component.id}",
                    f"contract {kind}",
                    dict(component.compatibility or contract.compatibility),
                    contract.name,
                    kind,
                    component.id,
                )
        for capability_kind, values in (
            ("mcp", contract.capabilities.mcp),
            ("executable", contract.capabilities.executables),
        ):
            for tier in CapabilityTier:
                for identifier in values[tier]:
                    _append_row(
                        rows,
                        inspection_records,
                        f"{contract.name}:{capability_kind}:{identifier}",
                        f"contract {tier.value} {capability_kind}",
                        dict(contract.compatibility),
                        contract.name,
                        capability_kind,
                        identifier,
                        tier is CapabilityTier.OPTIONAL,
                    )
    return rows


def render(inspection: dict[str, Any] | None = None) -> str:
    """Return deterministic Markdown, one applicability state per cell."""
    provenance = None if inspection is None else inspection.get("provenance")
    if provenance == "synthetic-fixture":
        evidence_description = "synthetic fixture evidence; not live native inspection"
    elif provenance is None:
        evidence_description = (
            "no native adapter inspection evidence; missing inspections render as "
            "`BLOCKED(adapter inspection missing)`"
        )
    else:
        evidence_description = "verified native adapter inspection evidence"
    lines = [
        "# Plugin Capability Matrix",
        "",
        f"Generated from portable contracts and {evidence_description}; do not edit by hand.",
        "`READY` requires a verified native harness state and non-empty native version,",
        "matching installed plugin, component, and capability evidence.",
        "",
        "| Capability | Evidence | Claude | Codex | Gemini | Cursor | Antigravity | Devin |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for identity, source, cells in _rows(inspection):
        lines.append("| " + " | ".join((f"`{identity}`", source, *cells)) + " |")
    return "\n".join(lines) + "\n"


def _load_inspection(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MatrixError(f"unable to load inspection evidence: {error}") from error
    if not isinstance(parsed, dict):
        raise MatrixError("inspection evidence must be a JSON object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--inspection", type=Path)
    args = parser.parse_args(argv)
    if args.check and args.inspection is None:
        print(
            "render_plugin_capability_matrix.py: --check requires --inspection evidence",
            file=sys.stderr,
        )
        return 2
    try:
        output = render(_load_inspection(args.inspection))
    except MatrixError as error:
        print(f"render_plugin_capability_matrix.py: {error}", file=sys.stderr)
        return 2
    target = ROOT / "docs/PLUGIN_CAPABILITY_MATRIX.md"
    if args.check:
        if "BLOCKED(" in output:
            print(
                "inspection evidence contains blocked capability cells", file=sys.stderr
            )
            return 2
        if not target.exists() or target.read_text(encoding="utf-8") != output:
            print(
                "docs/PLUGIN_CAPABILITY_MATRIX.md is stale; run tools/render_plugin_capability_matrix.py",
                file=sys.stderr,
            )
            return 1
        return 0
    target.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
