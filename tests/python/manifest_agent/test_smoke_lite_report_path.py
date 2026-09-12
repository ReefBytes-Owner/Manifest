"""`test.smoke.lite` must never write its own report into the candidate.

`manifest smoke run`'s default `--junit`/`--report` path (`smoke-report.xml`)
resolves against cwd; run through the check registry that cwd IS the
disposable candidate, so the report file itself trips the strict identity
check with "candidate identity changed" -- a check BLOCKING on evidence it
produced itself. The fix (`smoke_orchestrator.cli._default_report_path`)
resolves the default against `MANIFEST_RUN_TMP`, the runner's per-run temp
directory, whenever that env var is set (Correction 10, rule 5).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from manifest_agent.checks import run_profile
from manifest_agent.checks.registry import load_registry
from tests.python.manifest_agent.test_check_candidate import git, materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "configs" / "claude" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _real_store() -> str:
    override = os.environ.get("MANIFEST_TOOLCHAIN_STORE")
    if override:
        return override
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return str(base / "manifest" / "toolchain")


def _smoke_lite_registry() -> dict:
    registry = load_registry(REPO_ROOT / "config" / "project-checks.json")
    registry = dict(registry)
    checks = [check for check in registry["checks"] if check.id == "test.smoke.lite"]
    registry["checks"] = checks
    registry["profiles"] = {p: ["test.smoke.lite"] for p in registry["profiles"]}
    registry["coverage_pending"] = {p: [] for p in registry["coverage_pending"]}
    return registry


ONE_CASE_LITE_CATALOG = (
    Path(__file__).parent / "data" / "smoke_lite_one_case_catalog.yaml"
).read_text()


@pytest.fixture
def smoke_candidate(source, tmp_path):
    root, _base = source
    catalog_dir = root / "smoke-catalog"
    catalog_dir.mkdir()
    (catalog_dir / "manifest.yaml").write_text(ONE_CASE_LITE_CATALOG)
    git(root, "add", "smoke-catalog")
    git(root, "commit", "-qm", "add one-case Lite smoke catalog")
    return materialize(source, tmp_path)


@pytest.mark.native
def test_smoke_lite_check_leaves_candidate_unchanged_under_real_runner(
    smoke_candidate,
):
    """Real registry run of `test.smoke.lite`: no "candidate identity changed".

    Requires a provisioned toolchain store (`manifest provision`) on this
    host -- marked native for the same reason the CLI-presence tests are:
    it depends on host state this suite cannot fabricate.
    """
    registry = _smoke_lite_registry()
    env = {
        "MANIFEST_TOOLCHAIN_STORE": _real_store(),
        "HOME": os.environ.get("HOME", ""),
    }
    report = run_profile(registry, "full", "test", smoke_candidate, env)
    diagnostics = " ".join(
        result.get("diagnostics", "") for result in report["results"]
    )
    assert "candidate identity changed" not in diagnostics, diagnostics


def test_report_flag_writes_where_told(tmp_path):
    """`--report <path>` (the `--junit` alias) writes the JUnit XML there."""
    from smoke_orchestrator.cli import main

    catalog_dir = tmp_path / "smoke-catalog"
    catalog_dir.mkdir()
    (catalog_dir / "manifest.yaml").write_text(ONE_CASE_LITE_CATALOG)
    destination_dir = tmp_path / "elsewhere"
    destination_dir.mkdir()
    destination = destination_dir / "out.xml"

    exit_code = main(
        [
            "run",
            "--app",
            "manifest",
            "--tier",
            "Lite",
            "--catalog-dir",
            str(catalog_dir),
            "--report",
            str(destination),
        ]
    )

    assert exit_code == 0
    assert destination.is_file()
    assert not (tmp_path / "smoke-report.xml").exists()


def test_default_report_path_honors_manifest_run_tmp(tmp_path, monkeypatch):
    """The default (no `--report`/`--junit` given) follows `MANIFEST_RUN_TMP`."""
    from smoke_orchestrator.cli import _default_report_path

    monkeypatch.delenv("MANIFEST_RUN_TMP", raising=False)
    assert _default_report_path() == "smoke-report.xml"

    monkeypatch.setenv("MANIFEST_RUN_TMP", str(tmp_path))
    assert _default_report_path() == str(tmp_path / "smoke-report.xml")
