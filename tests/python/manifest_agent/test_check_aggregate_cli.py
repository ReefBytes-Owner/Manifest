"""CLI contract for `manifest check-aggregate`: exit-code mapping and I/O."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from manifest_agent.checks.registry import load_registry
from manifest_agent.checks.runner import _config_digest
from manifest_agent.cli import cli
from tests.python.manifest_agent.aggregate_fixtures import (
    clean_pair,
    context,
    job,
    write_two_group_registry,
)
from tests.python.manifest_agent.check_registry_fixtures import (
    _registry_file as _registry_file,
)


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_receipts(directory: Path, receipts: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for index, one_receipt in enumerate(receipts):
        _write(directory / f"{one_receipt['group']}-{index}.json", one_receipt)


def _invoke(
    tmp_path: Path, registry_path: Path, receipts: list[dict], run_context: dict
):
    results_dir = tmp_path / "results"
    _write_receipts(results_dir, receipts)
    context_path = tmp_path / "context.json"
    _write(context_path, run_context)
    runner = CliRunner()
    return runner.invoke(
        cli,
        [
            "check-aggregate",
            "full",
            "--project-config",
            str(registry_path),
            "--results-dir",
            str(results_dir),
            "--context",
            str(context_path),
            "--json",
        ],
    )


def test_cli_exit_code_for_pass(tmp_path, registry_file):
    path = write_two_group_registry(registry_file)
    receipts, run_context = clean_pair(_config_digest(load_registry(path)))

    result = _invoke(tmp_path, path, receipts, run_context)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "PASS"


def test_cli_exit_code_for_fail(tmp_path, registry_file):
    path = write_two_group_registry(registry_file)
    receipts, run_context = clean_pair(_config_digest(load_registry(path)))
    receipts[0]["results"][0]["status"] = "FAIL"
    receipts[0]["results"][0]["returncode"] = 1
    receipts[0]["status"] = "FAIL"

    result = _invoke(tmp_path, path, receipts, run_context)

    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["status"] == "FAIL"


def test_cli_exit_code_for_blocked_missing_receipt(tmp_path, registry_file):
    path = write_two_group_registry(registry_file)
    receipts, run_context = clean_pair(_config_digest(load_registry(path)))

    result = _invoke(tmp_path, path, receipts[:1], run_context)

    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["status"] == "BLOCKED"


def test_cli_malformed_receipt_json_is_blocked(tmp_path, registry_file):
    path = write_two_group_registry(registry_file)
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "lint-0.json").write_text("{not json", encoding="utf-8")
    context_path = tmp_path / "context.json"
    _write(context_path, context(producer_jobs=[job("lint"), job("test")]))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "check-aggregate",
            "full",
            "--project-config",
            str(path),
            "--results-dir",
            str(results_dir),
            "--context",
            str(context_path),
            "--json",
        ],
    )

    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "BLOCKED"
    assert any("not valid JSON" in d for d in payload["diagnostics"])


def test_cli_malformed_context_json_is_blocked(tmp_path, registry_file):
    path = write_two_group_registry(registry_file)
    receipts, _ = clean_pair(_config_digest(load_registry(path)))
    results_dir = tmp_path / "results"
    _write_receipts(results_dir, receipts)
    context_path = tmp_path / "context.json"
    context_path.write_text("{not json", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "check-aggregate",
            "full",
            "--project-config",
            str(path),
            "--results-dir",
            str(results_dir),
            "--context",
            str(context_path),
            "--json",
        ],
    )

    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "BLOCKED"
    assert any("not valid JSON" in d for d in payload["diagnostics"])


def test_cli_missing_results_dir_is_blocked(tmp_path, registry_file):
    path = registry_file()
    context_path = tmp_path / "context.json"
    _write(context_path, context(producer_jobs=[]))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "check-aggregate",
            "full",
            "--project-config",
            str(path),
            "--results-dir",
            str(tmp_path / "missing"),
            "--context",
            str(context_path),
            "--json",
        ],
    )

    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["status"] == "BLOCKED"
