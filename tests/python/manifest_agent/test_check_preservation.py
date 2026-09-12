"""Freeze old controls independently of the future registry and migrated files.

The inventory is data, never executable shell. The oracle reads only three
immutable Git blobs; the hand-reviewed component table is deliberately test-only.
Changing selectors, losing a component, or routing setup into checks must fail.

The oracle itself (expected_inventory(), Preservation, the blob readers) lives
in check_preservation_oracle.py (C2 split, responsibility seam: build the
oracle there, test it here).
"""

from __future__ import annotations

import copy
import json

import pytest
import yaml

from manifest_agent.checks.registry import load_registry
from tests.python.manifest_agent.check_preservation_oracle import (
    CI,
    HOOK_FIELDS,
    HOOKS,
    ROOT,
    Preservation,
    assert_shape,
    expected_inventory,
    reject_duplicate_keys,
)
from tests.python.manifest_agent.check_preservation_oracle import (
    preservation as preservation_fixture,
)

preservation = preservation_fixture
REGISTRY_PATH = ROOT / "config/project-checks.json"


def test_ci_test_checkout_contains_the_immutable_preservation_revision() -> None:
    """The preservation oracle's reviewed blobs must exist in the CI clone."""
    workflow = yaml.safe_load((ROOT / CI).read_text(encoding="utf-8"))
    checkout = next(
        step
        for step in workflow["jobs"]["test"]["steps"]
        if step.get("name") == "Checkout code"
    )
    assert checkout["with"].get("fetch-depth") == 0


def test_inventory_matches_immutable_observed_sources(preservation):
    assert preservation.compare_observed_sources().ok
    entries = preservation.data["source_entries"]
    assert sum(e["kind"] == "hook" for e in entries) == 37
    assert sum(e["kind"] == "workflow-step" for e in entries) == 60
    for entry in entries:
        components = [
            c for c in preservation.data["controls"] if c["source_key"] == entry["key"]
        ]
        assert entry["component_ids"] == [c["id"] for c in components]
        if entry["kind"] == "workflow-step" and "run" in entry["value"]["step"]:
            assert (
                "".join(c["command"]["value"] for c in components)
                == entry["value"]["step"]["run"]
            )


def test_missing_existing_hook_is_rejected(preservation):
    assert "hook.detect-private-key" in preservation.required_ids
    broken = preservation.without("hook.detect-private-key")
    assert broken.compare_observed_sources().missing == ("hook.detect-private-key",)


@pytest.mark.parametrize(
    "field", (*HOOK_FIELDS, "revision", "global", "base_selection")
)
def test_altered_hook_contract_is_rejected(preservation, field):
    broken = copy.deepcopy(preservation.data)
    value = broken["source_entries"][0]["value"]
    if field in HOOK_FIELDS:
        value["hook"][field] = {"present": True, "value": "altered"}
    elif field == "revision":
        value[field]["value"] = "v0.0.0"
    elif field == "global":
        value[field]["exclude"]["value"] = "^$"
    else:
        value[field]["run"] = "pre-commit run --all-files\n"
    assert not Preservation(broken).compare_observed_sources().ok


def test_absent_selector_cannot_be_replaced_by_explicit_default(preservation):
    broken = copy.deepcopy(preservation.data)
    selector = broken["source_entries"][0]["value"]["hook"]["pass_filenames"]
    assert selector == {"present": False, "value": None}
    selector.update(present=True, value=True)
    assert not Preservation(broken).compare_observed_sources().ok


@pytest.mark.parametrize(
    "hook_id,field,replacement",
    [
        ("shfmt", "args", ["-i", "2", "-w"]),
        ("pyright", "stages", ["pre-commit"]),
        ("terraform_fmt", "types_or", ["file"]),
        ("check-stale-repo-paths", "exclude", "^docs/"),
    ],
)
def test_existing_literal_hook_settings_cannot_be_changed(
    preservation,
    hook_id,
    field,
    replacement,
):
    broken = copy.deepcopy(preservation.data)
    entry = next(
        e
        for e in broken["source_entries"]
        if e["kind"] == "hook" and e["value"]["hook"]["id"]["value"] == hook_id
    )
    assert entry["value"]["hook"][field]["present"]
    entry["value"]["hook"][field]["value"] = replacement
    assert entry["key"] in Preservation(broken).compare_observed_sources().changed


@pytest.mark.parametrize("field", ["blob_id", "sha256"])
def test_altered_source_identity_is_rejected(preservation, field):
    broken = copy.deepcopy(preservation.data)
    broken["sources"][CI][field] = "0" * len(broken["sources"][CI][field])
    assert "sources" in Preservation(broken).compare_observed_sources().changed


def test_baseline_revision_cannot_follow_a_migrated_checkout(preservation):
    broken = copy.deepcopy(preservation.data)
    broken["observed_revision"] = "HEAD"
    assert (
        "observed_revision" in Preservation(broken).compare_observed_sources().changed
    )


def test_missing_ci_step_is_rejected(preservation):
    broken = copy.deepcopy(preservation.data)
    key = f"workflow:{CI}:job:validate:step:4"
    broken["source_entries"] = [e for e in broken["source_entries"] if e["key"] != key]
    assert Preservation(broken).compare_observed_sources().missing == (key,)


def test_omitted_composite_component_is_rejected_even_if_parent_remains(preservation):
    identifier = f"workflow:{CI}:job:lint:step:16:component:3"
    broken = preservation.without(identifier)
    for entry in broken.data["source_entries"]:
        entry["component_ids"] = [i for i in entry["component_ids"] if i != identifier]
    result = broken.compare_observed_sources()
    assert result.missing == (identifier,)
    assert f"workflow:{CI}:job:lint:step:16" in result.changed


@pytest.mark.parametrize(
    "field,value",
    [
        ("tool_pin", {"present": True, "value": ["wrong==0"]}),
        ("platform", {"present": True, "value": "windows-latest"}),
        ("group", "wrong"),
        ("check_ids", []),
        ("workflow_control_ids", ["wrong"]),
        ("component_ordinal", 999),
        ("component_key", "command:999"),
        ("command", {"present": True, "value": "true"}),
    ],
)
def test_altered_control_contract_is_rejected(preservation, field, value):
    broken = copy.deepcopy(preservation.data)
    broken["controls"][0][field] = value
    assert not Preservation(broken).compare_observed_sources().ok


@pytest.mark.parametrize("disposition", ["retained", "setup", "publication"])
def test_disposition_misclassification_is_rejected(preservation, disposition):
    broken = copy.deepcopy(preservation.data)
    item = next(c for c in broken["controls"] if c["disposition"] == disposition)
    item.update(
        disposition="setup" if disposition != "setup" else "retained",
        check_ids=[] if disposition != "setup" else ["invented.check"],
        workflow_control_ids=[],
    )
    assert not Preservation(broken).compare_observed_sources().ok


@pytest.mark.parametrize("section,key", [("controls", "id"), ("source_entries", "key")])
def test_duplicate_and_unknown_entries_are_rejected(preservation, section, key):
    broken = copy.deepcopy(preservation.data)
    broken[section].append(copy.deepcopy(broken[section][0]))
    assert not Preservation(broken).compare_observed_sources().ok
    broken[section][-1][key] = "unknown"
    assert Preservation(broken).compare_observed_sources().unexpected == ("unknown",)


@pytest.mark.parametrize(
    "location", ["root", "source", "entry", "hook", "presence", "control"]
)
def test_unknown_fields_are_rejected(preservation, location):
    broken = copy.deepcopy(preservation.data)
    entry = broken["source_entries"][0]
    targets = {
        "root": broken,
        "source": broken["sources"][HOOKS],
        "entry": entry,
        "hook": entry["value"]["hook"],
        "presence": entry["value"]["hook"]["files"],
        "control": broken["controls"][0],
    }
    targets[location]["unreviewed"] = True
    with pytest.raises(AssertionError, match="unknown/missing fields"):
        assert_shape(broken, expected_inventory())


def test_duplicate_json_fields_are_rejected():
    with pytest.raises(AssertionError, match="duplicate JSON key"):
        json.loads(
            '{"schema_version": 1, "schema_version": 2}',
            object_pairs_hook=reject_duplicate_keys,
        )


def test_equivalence_entries_reference_real_checks_with_matching_version_and_fixtures(
    preservation,
):
    """Beyond the closed field-shape assert_shape() enforces, each recorded
    equivalence must be real: hook_id names an actual registered check,
    engine_version is the same version the registry pins for it, and
    fixture_corpus exists with both a passing and a failing input."""
    registry = load_registry(REGISTRY_PATH)
    checks_by_id = {check.id: check for check in registry["checks"]}
    entries = preservation.data["equivalence"]
    assert entries, "no equivalence entries recorded"
    for entry in entries:
        check = checks_by_id.get(entry["hook_id"])
        assert check is not None, f"{entry['hook_id']} is not a registered check"
        assert entry["engine_version"] in check.version, (
            entry["hook_id"],
            entry["engine_version"],
            check.version,
        )
        corpus = ROOT / entry["fixture_corpus"]
        assert corpus.is_dir(), corpus
        names = {path.stem for path in corpus.iterdir()}
        assert "valid" in names, f"{corpus} has no valid.* fixture"
        assert "invalid" in names, f"{corpus} has no invalid.* fixture"
