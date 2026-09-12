"""Parity between `.pre-commit-config.yaml` hooks and their `hook.*` mirrors.

Regression coverage for the C7f finding: every `hook.*` check in
`config/project-checks.json` had `types: []`, so `selection: changed`
forwarded every changed path to every hook regardless of file type --
`hook.shfmt`/`hook.check-yaml`/`hook.check-json`/`hook.markdownlint-cli2` all
FAILed against unrelated inputs (e.g. `docs/SHARED_CHECKS.md`, `src/**/*.py`).

Part (a): a static parity test that parses `.pre-commit-config.yaml` and
asserts every mirrored `hook.<id>` check's `types`/`types_or`/`include_regex`/
`exclude_regex` equals the hook's own effective selection. Part (b): a
functional test that drives the real runner selection entry point
(`_selection_outcome`) against the production registry to prove a changed
`.md` file is not forwarded to `hook.shfmt`/`hook.check-json`/
`hook.check-yaml`, and a changed `.sh` file is forwarded to `hook.shfmt`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from manifest_agent.checks.models import Candidate
from manifest_agent.checks.registry import load_registry
from manifest_agent.checks.runner import _selection_outcome

ROOT = Path(__file__).resolve().parents[3]
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
REGISTRY_PATH = ROOT / "config/project-checks.json"
UPSTREAM_TYPES_DATA = (
    Path(__file__).resolve().parent / "data" / ("mirrored_hook_upstream_types.yml")
)


def _upstream_default_types() -> dict[str, tuple[str, ...]]:
    """Load the pinned upstream `types` table for undeclared hook mirrors.

    See `data/mirrored_hook_upstream_types.yml` for provenance: these hooks
    declare neither `types` nor `types_or` in `.pre-commit-config.yaml`, so
    the effective type requirement is not recoverable from this repository's
    YAML alone.
    """
    document = yaml.safe_load(UPSTREAM_TYPES_DATA.read_text(encoding="utf-8"))
    return {
        hook_id: tuple(tags)
        for hook_id, tags in document["upstream_default_types"].items()
    }


UPSTREAM_DEFAULT_TYPES = _upstream_default_types()


def _pre_commit_hooks() -> dict[str, dict]:
    document = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    hooks: dict[str, dict] = {}
    for repo in document["repos"]:
        for hook in repo["hooks"]:
            hooks[hook["id"]] = hook
    return hooks, document.get("exclude", "")


def _expected_selector(hook: dict, global_exclude: str) -> dict[str, object]:
    types = tuple(hook.get("types") or UPSTREAM_DEFAULT_TYPES.get(hook["id"], ()))
    types_or = tuple(hook.get("types_or") or ())
    include = hook.get("files") or ""
    hook_exclude = hook.get("exclude") or ""
    exclude = f"(?:{global_exclude})"
    if hook_exclude:
        exclude += f"|(?:{hook_exclude})"
    return {
        "types": types,
        "types_or": types_or,
        "include_regex": include,
        "exclude_regex": exclude,
    }


def _mirrored_registry_checks() -> dict[str, dict]:
    hooks, _ = _pre_commit_hooks()
    registry = load_registry(REGISTRY_PATH)
    return {
        check.id: check
        for check in registry["checks"]
        if check.id.startswith("hook.") and check.id.removeprefix("hook.") in hooks
    }


@pytest.mark.parametrize("hook_id", sorted(_pre_commit_hooks()[0]))
def test_mirrored_hook_selector_matches_pre_commit_config(hook_id: str) -> None:
    hooks, global_exclude = _pre_commit_hooks()
    registry_id = f"hook.{hook_id}"
    registry = load_registry(REGISTRY_PATH)
    checks = {check.id: check for check in registry["checks"]}
    if registry_id not in checks:
        pytest.skip(f"{registry_id} is not (yet) mirrored in the registry")
    expected = _expected_selector(hooks[hook_id], global_exclude)
    check = checks[registry_id]
    assert check.types == expected["types"], hook_id
    assert check.types_or == expected["types_or"], hook_id
    assert check.include_regex == expected["include_regex"], hook_id
    assert check.exclude_regex == expected["exclude_regex"], hook_id


def _candidate(root: Path, changed: tuple[str, ...]) -> Candidate:
    return Candidate(root, root, "head", "base", "tree", "source", changed, root / "r")


def test_a_changed_markdown_file_is_not_forwarded_to_shfmt_or_json_or_yaml_hooks(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "SHARED_CHECKS.md"
    doc.write_text("# doc\n", encoding="utf-8")
    names = ("SHARED_CHECKS.md",)
    registry = load_registry(REGISTRY_PATH)
    checks = {check.id: check for check in registry["checks"]}
    candidate = _candidate(tmp_path, names)

    for check_id in ("hook.shfmt", "hook.check-json", "hook.check-yaml"):
        selected, outcome = _selection_outcome(checks[check_id], candidate, names)
        assert selected == (), check_id
        assert outcome is not None and outcome.status == "NOT_APPLICABLE", check_id


def test_a_changed_shell_script_is_forwarded_to_shfmt(tmp_path: Path) -> None:
    script = tmp_path / "deploy.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    names = ("deploy.sh",)
    registry = load_registry(REGISTRY_PATH)
    check = {c.id: c for c in registry["checks"]}["hook.shfmt"]
    candidate = _candidate(tmp_path, names)

    selected, outcome = _selection_outcome(check, candidate, names)

    assert selected == ("deploy.sh",)
    assert outcome is None
