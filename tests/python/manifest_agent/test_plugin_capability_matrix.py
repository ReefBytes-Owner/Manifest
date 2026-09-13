"""Generated capability-matrix release evidence."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path


def _renderer_module():
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "render_plugin_capability_matrix",
        root / "tools/render_plugin_capability_matrix.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matrix_has_an_explicit_state_for_every_harness_cell() -> None:
    renderer = _renderer_module()
    root = Path(__file__).resolve().parents[3]
    inspection = renderer._load_inspection(
        root / "tests/fixtures/plugin_capability_inspection.json"
    )
    rendered = renderer.render(inspection)

    lines = [line for line in rendered.splitlines() if line.startswith("|")][2:]
    assert lines
    assert all(line.count("|") == 9 for line in lines)
    assert "|  |" not in rendered
    assert all(state in rendered for state in ("READY", "DEGRADED(", "N/A("))
    for identity in ("skill:i-have-adhd", "executable:python3"):
        row = next(
            line for line in lines if f"`manifest-i-have-adhd:{identity}`" in line
        )
        assert row.count("READY") == 6
        assert "N/A(" not in row
    for identity in (
        "hook:adhd-session-start",
        "guidance:adhd-always-on-guidance",
        "runtime:adhd-hook-runtime",
    ):
        row = next(
            line for line in lines if f"`manifest-i-have-adhd:{identity}`" in line
        )
        assert row.count("READY") == 6
        assert "BLOCKED(" not in row

    for identity in (
        "hook:codex-session-start",
        "hook:codex-stop",
        "hook:codex-permission-request",
    ):
        row = next(line for line in lines if f"`manifest-workspace:{identity}`" in line)
        assert row.count("READY") == 1
        assert row.count("N/A(") == 5


def test_matrix_checked_in_rendering_is_current() -> None:
    renderer = _renderer_module()
    root = Path(__file__).resolve().parents[3]
    inspection = renderer._load_inspection(
        root / "tests/fixtures/plugin_capability_inspection.json"
    )

    assert (root / "docs/PLUGIN_CAPABILITY_MATRIX.md").read_text(
        encoding="utf-8"
    ) == renderer.render(inspection)


def test_synthetic_fixture_evidence_is_not_rendered_as_live_native_inspection() -> None:
    renderer = _renderer_module()
    root = Path(__file__).resolve().parents[3]
    inspection = renderer._load_inspection(
        root / "tests/fixtures/plugin_capability_inspection.json"
    )

    assert inspection is not None
    assert inspection["provenance"] == "synthetic-fixture"
    rendered = renderer.render(inspection)
    assert "synthetic fixture evidence; not live native inspection" in rendered


def test_matrix_without_inspection_is_explicitly_blocked() -> None:
    renderer = _renderer_module()

    rendered = renderer.render()

    assert "no native adapter inspection evidence" in rendered
    assert "verified native adapter inspection evidence" not in rendered
    assert "BLOCKED(adapter inspection missing)" in rendered


def test_ready_contract_names_all_enforced_evidence_predicates() -> None:
    renderer = _renderer_module()

    rendered = renderer.render()

    assert (
        "`READY` requires a verified native harness state and non-empty native version,"
        in rendered
    )
    assert "matching installed plugin, component, and capability evidence." in rendered


def test_matrix_blocks_ready_harness_without_matching_plugin_component_or_capability(
    tmp_path: Path,
) -> None:
    renderer = _renderer_module()
    root = Path(__file__).resolve().parents[3]
    inspection = renderer._load_inspection(
        root / "tests/fixtures/plugin_capability_inspection.json"
    )
    assert inspection is not None
    missing = copy.deepcopy(inspection)
    claude = missing["harnesses"]["claude"]
    claude["installed_plugin_ids"].remove("manifest-docs")
    claude["components"]["manifest-code-quality"].remove("skill:ai-code-audit")
    claude["capabilities"]["manifest-code-quality"].remove("executable:git")

    rendered = renderer.render(missing)

    assert "BLOCKED(plugin 'manifest-docs' is not installed)" in rendered
    assert (
        "BLOCKED(components evidence missing manifest-code-quality:skill:ai-code-audit)"
        in rendered
    )
    assert (
        "BLOCKED(capabilities evidence missing manifest-code-quality:executable:git)"
        in rendered
    )
    evidence_path = tmp_path / "inspection.json"
    evidence_path.write_text(json.dumps(missing), encoding="utf-8")
    assert renderer.main(["--check", "--inspection", str(evidence_path)]) == 2
