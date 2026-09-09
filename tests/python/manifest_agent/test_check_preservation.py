"""Freeze old controls independently of the future registry and migrated files.

The inventory is data, never executable shell. The oracle reads only three
immutable Git blobs; the hand-reviewed component table is deliberately test-only.
Changing selectors, losing a component, or routing setup into checks must fail.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
INVENTORY = ROOT / "config/check-preservation.json"
REVISION = "7741d4aa588ed57791af15ea0eedd8862305ff0c"
HOOKS = ".pre-commit-config.yaml"
CI = ".github/workflows/ci.yml"
RELEASE = ".github/workflows/manifest-release.yml"
SOURCES = (HOOKS, CI, RELEASE)
HOOK_FIELDS = (
    "id",
    "name",
    "entry",
    "language",
    "files",
    "types",
    "types_or",
    "exclude",
    "stages",
    "pass_filenames",
    "args",
    "additional_dependencies",
)
GLOBAL_FIELDS = ("exclude", "default_language_version", "default_stages")

# Each row describes one observed step, in source order. A tuple is a
# command-level decomposition; ':' denotes setup and '!' publication.
# Literal line boundaries retain every byte (including comments and shell
# guards) without pretending conditional shell fragments run independently.
COMPONENTS = {
    "lint": (
        ":checkout",
        ":skill-mirror",
        (":shellcheck-install", ":shellcheck-version"),
        "lint.shell.scripts",
        ("lint.shell.bootstrap", "lint.shell.bootstrap"),
        "lint.shell.arrays",
        "lint.bats.assertions",
        "package.self-contained",
        ":yamllint-install",
        "lint.yaml.config",
        "lint.markdown.keydocs",
        ":python-setup",
        (":pyyaml-install", "syntax.yaml.config"),
        "generated.commands-doc",
        ":uv-install",
        "structure.bundle-references",
        (
            ":shell-options",
            "generated.plugin-views",
            "generated.vendor",
            "structure.runtime-paths",
            "structure.agent-frontmatter",
            "generated.capability-inventory",
            "generated.capability-matrix",
        ),
        (":build-output-cleanup", "package.coordinator", "package.coordinator"),
        "dependency.lock.config",
        "package.config",
        "dependency.lock.delegate",
        ":pre-commit-cache",
        ":pre-commit-install",
        "@hooks",
    ),
    "test": (
        ":checkout",
        ":skill-mirror",
        ":bats-cache",
        ":bats-install",
        ":uv-install",
        ":runtime-sync",
        (
            ":gitleaks-download",
            ":gitleaks-checksum",
            ":gitleaks-extract",
            ":gitleaks-install",
            ":gitleaks-cleanup",
            ":gitleaks-version",
        ),
        "test.bats",
        ":python-setup",
        (":model-policy-install", ":test-dependencies"),
        "test.python",
        "test.hooks",
        "test.smoke.lite",
    ),
    "validate": (
        ":checkout",
        ":skill-mirror",
        ":python-setup",
        ":pyyaml-install",
        "structure.symlinks",
        "structure.case-collision",
        "syntax.shell.project",
        "structure.inventory",
        "structure.skill-paths",
        "test.bundle-partition",
        "structure.skill-references",
        "generated.cursor",
    ),
    "release": (
        ":checkout",
        ":python-setup",
        ":uv-setup",
        ":skill-mirror",
        ("package.coordinator", "package.release-archive"),
        "package.release-manifest",
        "!release.inspect-existing",
        "!release.ensure-tag",
        "!release.create-draft",
        "!release.upload-assets",
        "!release.publish",
    ),
}
BOUNDARIES = {
    ("lint", 2): (1,),
    ("lint", 4): (1,),
    ("lint", 12): (1,),
    ("lint", 16): (1, 2, 3, 4, 9, 10),
    ("lint", 17): (2, 3),
    ("test", 6): (6, 7, 8, 9, 10),
    ("test", 9): (1,),
    ("release", 4): (2,),
}


class ActionsLoader(yaml.SafeLoader):
    """YAML 1.2 booleans: Actions' 'on' key must not become Python True."""


ActionsLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in values if tag != "tag:yaml.org,2002:bool"]
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
ActionsLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list("tTfF"),
)


def present(mapping, key):
    return {"present": key in mapping, "value": mapping.get(key)}


def optional(value):
    return {"present": value is not None, "value": value}


def git(*args):
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=15,
    ).stdout


def pins(job, index, step):
    """Record literal pins, including the intentional absence of a uv pin."""
    if "uses" in step:
        values = [step["uses"]]
        for name in ("python-version", "version"):
            if name in step.get("with", {}):
                values.append(f"{name}={step['with'][name]}")
        return optional(values)
    if job == "lint":
        if index in (2, 3, 4):
            return optional(["shellcheck-py==0.11.0.1"])
        if index in (8, 9):
            return optional(["yamllint==1.38.0"])
        if index in (12, 13):
            return optional(["python-version=3.14", "pyyaml==6.0.2"])
    if job == "test":
        if index in (3, 7):
            return optional(["bats@1.11.1"])
        if index == 6:
            return optional([f"GITLEAKS_VERSION={step['env']['GITLEAKS_VERSION']}"])
        if index in (9, 10, 11):
            return optional(["python-version=3.14"])
    if job == "validate" and index in (3, 11):
        return optional(["python-version=3.14", "pyyaml==6.0.2"])
    if job == "release" and index in (4, 5):
        values = ["python-version=3.14"]
        if index == 4:
            values.append("version=0.12.6")
        return optional(values)
    return optional(None)


def control(identifier, key, ordinal, destination, command, tool_pin, platform, group):
    disposition = "retained"
    checks, workflow_controls = [destination], []
    if destination.startswith(":"):
        disposition, checks = "setup", []
    elif destination.startswith("!"):
        disposition, checks = "publication", []
        workflow_controls = [destination[1:]]
    elif destination == "@hooks":
        checks = hook_ids()
    return {
        "id": identifier,
        "source_key": key,
        "component_ordinal": ordinal,
        "component_key": f"command:{ordinal}",
        "check_ids": checks,
        "workflow_control_ids": workflow_controls,
        "tool_pin": tool_pin,
        "platform": platform,
        "group": group,
        "disposition": disposition,
        "command": command,
    }


@lru_cache(maxsize=1)
def blobs():
    return {path: git("show", f"{REVISION}:{path}") for path in SOURCES}


@lru_cache(maxsize=1)
def observed():
    return {
        path: yaml.load(data, Loader=ActionsLoader) for path, data in blobs().items()
    }


def hook_ids():
    return [
        f"hook.{hook['id']}"
        for repo in observed()[HOOKS]["repos"]
        for hook in repo["hooks"]
    ]


def hook_entries():
    baseline = observed()
    base_step = baseline[CI]["jobs"]["lint"]["steps"][23]
    for repo_index, repo in enumerate(baseline[HOOKS]["repos"]):
        for hook_index, hook in enumerate(repo["hooks"]):
            assert not set(hook) - set(HOOK_FIELDS)
            key = f"hook:{repo_index}:{hook_index}:{hook['id']}"
            identifier = f"hook.{hook['id']}"
            value = {
                "repository": repo["repo"],
                "revision": present(repo, "rev"),
                "hook": {field: present(hook, field) for field in HOOK_FIELDS},
                "global": {
                    field: present(baseline[HOOKS], field) for field in GLOBAL_FIELDS
                },
                "base_selection": {
                    "source_key": f"workflow:{CI}:job:lint:step:23",
                    "env": base_step["env"],
                    "run": base_step["run"],
                },
            }
            entry = {
                "key": key,
                "kind": "hook",
                "value": value,
                "component_ids": [identifier],
            }
            pin = [f"{repo['repo']}@{repo['rev']}"] if "rev" in repo else None
            yield (
                entry,
                [
                    control(
                        identifier,
                        key,
                        0,
                        identifier,
                        present(hook, "entry"),
                        optional(pin),
                        optional(None),
                        "pre-commit",
                    )
                ],
            )


def workflow_entries():
    for path in (CI, RELEASE):
        workflow = observed()[path]
        workflow_context = {k: v for k, v in workflow.items() if k != "jobs"}
        for job_name, job in workflow["jobs"].items():
            assert len(job["steps"]) == len(COMPONENTS[job_name])
            job_context = {k: v for k, v in job.items() if k != "steps"}
            for index, step in enumerate(job["steps"]):
                key = f"workflow:{path}:job:{job_name}:step:{index}"
                destinations = COMPONENTS[job_name][index]
                if isinstance(destinations, str):
                    destinations = (destinations,)
                lines = step.get("run", "").splitlines(keepends=True)
                bounds = (0, *BOUNDARIES.get((job_name, index), ()), len(lines))
                assert len(bounds) == len(destinations) + 1
                controls = []
                for ordinal, destination in enumerate(destinations):
                    command = (
                        "".join(lines[bounds[ordinal] : bounds[ordinal + 1]])
                        if "run" in step
                        else None
                    )
                    controls.append(
                        control(
                            f"{key}:component:{ordinal}",
                            key,
                            ordinal,
                            destination,
                            optional(command),
                            pins(job_name, index, step),
                            present(job, "runs-on"),
                            job_name,
                        )
                    )
                yield (
                    {
                        "key": key,
                        "kind": "workflow-step",
                        "value": {
                            "step": step,
                            "job": job_context,
                            "workflow": workflow_context,
                        },
                        "component_ids": [item["id"] for item in controls],
                    },
                    controls,
                )


@lru_cache(maxsize=1)
def expected_inventory():
    entries, controls = [], []
    for entry, components in (*hook_entries(), *workflow_entries()):
        entries.append(entry)
        controls.extend(components)
    return {
        "schema_version": 1,
        "observed_revision": REVISION,
        "sources": {
            path: {
                "blob_id": git("rev-parse", f"{REVISION}:{path}").decode().strip(),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for path, data in blobs().items()
        },
        "source_entries": entries,
        "controls": controls,
    }


def assert_shape(actual, expected, path="$"):
    """Closed objects at every depth, even opaque normalized source mappings."""
    assert type(actual) is type(expected), f"{path}: incorrect type"
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), f"{path}: unknown/missing fields"
        for key, value in actual.items():
            assert_shape(value, expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if not expected:
            assert not actual, f"{path}: unexpected values"
        for index, value in enumerate(actual):
            assert_shape(
                value, expected[min(index, len(expected) - 1)], f"{path}[{index}]"
            )


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, f"duplicate JSON key: {key}"
        result[key] = value
    return result


def load_inventory():
    assert INVENTORY.is_file(), "missing frozen control inventory"
    data = json.loads(INVENTORY.read_text(), object_pairs_hook=reject_duplicate_keys)
    assert_shape(data, expected_inventory())
    return data


@dataclass(frozen=True)
class Comparison:
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def ok(self):
        return not (self.missing or self.unexpected or self.changed)


class Preservation:
    def __init__(self, data=None):
        self._data = data

    @property
    def data(self):
        if self._data is None:
            self._data = load_inventory()
        return self._data

    @property
    def required_ids(self):
        return tuple(item["id"] for item in expected_inventory()["controls"])

    def without(self, identifier):
        data = copy.deepcopy(self.data)
        data["controls"] = [c for c in data["controls"] if c["id"] != identifier]
        return Preservation(data)

    def compare_observed_sources(self):
        expected = expected_inventory()
        missing, unexpected, changed = [], [], []
        for section, key in (("source_entries", "key"), ("controls", "id")):
            actual_rows = {row[key]: row for row in self.data[section]}
            expected_rows = {row[key]: row for row in expected[section]}
            if len(actual_rows) != len(self.data[section]):
                changed.append(f"{section}:duplicate")
            missing.extend(k for k in expected_rows if k not in actual_rows)
            unexpected.extend(k for k in actual_rows if k not in expected_rows)
            changed.extend(
                k
                for k in expected_rows.keys() & actual_rows.keys()
                if expected_rows[k] != actual_rows[k]
            )
        for key in ("schema_version", "observed_revision", "sources"):
            if self.data[key] != expected[key]:
                changed.append(key)
        return Comparison(tuple(missing), tuple(unexpected), tuple(sorted(changed)))


@pytest.fixture
def preservation():
    return Preservation()


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
