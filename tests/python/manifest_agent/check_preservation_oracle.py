"""Build and load the frozen check-preservation oracle from immutable blobs.

Split out of test_check_preservation.py (C2): this module owns everything
needed to reconstruct `expected_inventory()` from the three immutable Git
blobs at `REVISION` and to load/compare `config/check-preservation.json`
against it. `test_check_preservation.py` keeps only the `test_*` functions
that exercise this oracle -- the split moved the responsibility seam, not
just lines, so each file now has one job: build the oracle, or test it.
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
COMPONENTS_DATA = (
    Path(__file__).resolve().parent / "data" / "check_preservation_components.yml"
)


def _tuple_from_yaml(item):
    if isinstance(item, list):
        return tuple(_tuple_from_yaml(value) for value in item)
    return item


@lru_cache(maxsize=1)
def _components_and_boundaries():
    """Load the frozen CI-step component table (moved out of source, C-DATA).

    Each row describes one observed CI step, in source order. A tuple is a
    command-level decomposition; ':' denotes setup and '!' publication.
    Literal line boundaries retain every byte (including comments and shell
    guards) without pretending conditional shell fragments run independently.
    """
    with COMPONENTS_DATA.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    components = {
        job: tuple(_tuple_from_yaml(item) for item in items)
        for job, items in raw["components"].items()
    }
    boundaries = {}
    for key, bounds in raw["boundaries"].items():
        job, index = key.rsplit(":", 1)
        boundaries[(job, int(index))] = tuple(bounds)
    return components, boundaries


class SafeLoader(yaml.SafeLoader):
    """YAML 1.2 booleans: Actions' 'on' key must not become Python True."""


SafeLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in values if tag != "tag:yaml.org,2002:bool"]
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
SafeLoader.add_implicit_resolver(
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


@dataclass(frozen=True)
class ControlSpec:
    """The 8 fields that always travel together to build one control record."""

    identifier: str
    key: str
    ordinal: int
    destination: str
    command: dict
    tool_pin: dict
    platform: dict
    group: str


def control(spec: ControlSpec) -> dict:
    disposition = "retained"
    checks, workflow_controls = [spec.destination], []
    if spec.destination.startswith(":"):
        disposition, checks = "setup", []
    elif spec.destination.startswith("!"):
        disposition, checks = "publication", []
        workflow_controls = [spec.destination[1:]]
    elif spec.destination == "@hooks":
        checks = hook_ids()
    return {
        "id": spec.identifier,
        "source_key": spec.key,
        "component_ordinal": spec.ordinal,
        "component_key": f"command:{spec.ordinal}",
        "check_ids": checks,
        "workflow_control_ids": workflow_controls,
        "tool_pin": spec.tool_pin,
        "platform": spec.platform,
        "group": spec.group,
        "disposition": disposition,
        "command": spec.command,
    }


@lru_cache(maxsize=1)
def blobs():
    return {path: git("show", f"{REVISION}:{path}") for path in SOURCES}


@lru_cache(maxsize=1)
def observed():
    return {path: yaml.load(data, Loader=SafeLoader) for path, data in blobs().items()}


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
                        ControlSpec(
                            identifier,
                            key,
                            0,
                            identifier,
                            present(hook, "entry"),
                            optional(pin),
                            optional(None),
                            "pre-commit",
                        )
                    )
                ],
            )


def workflow_entries():
    components, boundaries = _components_and_boundaries()
    for path in (CI, RELEASE):
        workflow = observed()[path]
        workflow_context = {k: v for k, v in workflow.items() if k != "jobs"}
        for job_name, job in workflow["jobs"].items():
            assert len(job["steps"]) == len(components[job_name])
            job_context = {k: v for k, v in job.items() if k != "steps"}
            for index, step in enumerate(job["steps"]):
                key = f"workflow:{path}:job:{job_name}:step:{index}"
                destinations = components[job_name][index]
                if isinstance(destinations, str):
                    destinations = (destinations,)
                lines = step.get("run", "").splitlines(keepends=True)
                bounds = (0, *boundaries.get((job_name, index), ()), len(lines))
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
                            ControlSpec(
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


# Structural template for one config/check-preservation.json "equivalence"
# entry (C2, engines-not-wrappers). Unlike source_entries/controls these are
# hand-authored reviewed records, not reconstructed from the immutable
# REVISION blobs, so expected_inventory() cannot regenerate their content --
# but assert_shape() still uses this single template (list-shape rule: every
# actual list item is checked against expected[0]) to enforce every entry
# has exactly this field set, closed to typos or missing evidence.
_EQUIVALENCE_ENTRY_SHAPE = {
    "hook_id": "",
    "wrapper_rev": "",
    "engine": "",
    "engine_version": "",
    "wrapper_entry_argv": [""],
    "evidence": "",
    "fixture_corpus": "",
}


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
        "equivalence": [_EQUIVALENCE_ENTRY_SHAPE],
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
