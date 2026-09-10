"""Execution proof for the shadow-CI producer-rejection fix (Task 9, defect 2).

`manifest check` exits 2 (FAIL) or 3 (BLOCKED) exactly when a group
legitimately produced a receipt, so the shadow step's own step `outcome` was
"failure" both when the check crashed with NO receipt and when it ran
cleanly and returned FAIL/BLOCKED WITH one. Gating the aggregate on that
`outcome` therefore rejected every real run once any group returned
FAIL/BLOCKED -- which, before Phase 3, is every group -- so `manifest
check-aggregate` never actually ran and every real run published `Status:
UNKNOWN (no aggregate receipt was written)`.

The fix: the shadow step itself now captures `manifest check`'s exit code,
always exits 0, and separately reports whether the declared `--output` file
exists as a `receipt_written` job output. The aggregate's rejection step
reads that instead of `steps.shadow.outcome`.

These tests extract the real `run:` scripts from `.github/workflows/ci.yml`
and execute them for real (bash / python3, no network, no `gh`/`uv` calls --
those are stubbed) to prove the two cases this fix must tell apart:

1. A producer whose check exits 2 or 3 but writes a receipt file must NOT be
   treated as rejected (`receipt_written=true`, and the aggregate's rejection
   step must not sys.exit(1) on that alone).
2. A producer that crashes without writing a receipt must still be rejected
   (`receipt_written=false`, and the aggregate's rejection step must
   sys.exit(1)).

This is the strongest proof available without a live Actions run: it proves
the *scripts as committed* behave correctly under both cases. It does not
prove GitHub's own `continue-on-error`/`needs.*.outputs` plumbing wires those
same script outputs through correctly end-to-end -- only a real workflow run
can confirm that.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW_PATH = ROOT / ".github/workflows/ci.yml"


def _jobs() -> dict[str, Any]:
    with CI_WORKFLOW_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)["jobs"]


def _shadow_step_script(job_name: str) -> str:
    job = _jobs()[job_name]
    (step,) = [
        step
        for step in job["steps"]
        if re.search(r"\bmanifest\s+check\b", step.get("run", ""))
    ]
    return step["run"]


def _rejection_script() -> str:
    job = _jobs()["checks-aggregate-full"]  # C6b rename (was shadow-checks-aggregate)
    (step,) = [
        step
        for step in job["steps"]
        if re.search(r'!=\s*["\']true["\']', step.get("run", ""))
    ]
    (python_c,) = re.findall(r'python3 -c "\n(.*)\n\s*"', step["run"], re.DOTALL)
    return python_c


def _fake_uv_bin(tmp_path: Path, exit_code: int, write_receipt: bool) -> Path:
    """A `uv` shim on PATH: `uv run manifest check ... --output X.json` either
    writes a receipt file (mimicking a real, completed check run) or does not
    (mimicking a crash before any report was written), then exits with the
    given code -- mirroring `manifest check`'s real exit codes 0/2/3."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "uv"
    output_flag = "--output"
    body = f"""#!/bin/sh
set -e
prev=""
for arg in "$@"; do
  if [ "$prev" = "{output_flag}" ]; then
    out_path="$arg"
  fi
  prev="$arg"
done
"""
    if write_receipt:
        body += 'echo \'{"status": "irrelevant-for-this-fixture"}\' > "$out_path"\n'
    body += f"exit {exit_code}\n"
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return bin_dir


def _run_shadow_step(job_name: str, tmp_path: Path, exit_code: int, write_receipt: bool):
    script = _shadow_step_script(job_name)
    bin_dir = _fake_uv_bin(tmp_path, exit_code, write_receipt)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    output_path = tmp_path / "github_output"
    output_path.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "BASE_SHA": "deadbeef",
        "GITHUB_OUTPUT": str(output_path),
    }
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=work_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    outputs = dict(
        line.split("=", 1) for line in output_path.read_text().splitlines() if "=" in line
    )
    return result, outputs


class TestShadowStepReceiptSignal:
    def test_fail_exit_with_receipt_is_reported_as_receipt_written(
        self, tmp_path: Path
    ) -> None:
        # exit 2 == FAIL, receipt written -- the exact case defect 2 misread.
        result, outputs = _run_shadow_step(
            "shadow-checks-structure", tmp_path, exit_code=2, write_receipt=True
        )
        assert result.returncode == 0, (
            f"shadow step must always exit 0 regardless of the check's own "
            f"exit code: stderr={result.stderr!r}"
        )
        assert outputs.get("receipt_written") == "true"
        assert outputs.get("exit_code") == "2"

    def test_blocked_exit_with_receipt_is_reported_as_receipt_written(
        self, tmp_path: Path
    ) -> None:
        # exit 3 == BLOCKED, receipt written.
        result, outputs = _run_shadow_step(
            "shadow-checks-structure", tmp_path, exit_code=3, write_receipt=True
        )
        assert result.returncode == 0
        assert outputs.get("receipt_written") == "true"
        assert outputs.get("exit_code") == "3"

    def test_crash_with_no_receipt_is_reported_as_receipt_not_written(
        self, tmp_path: Path
    ) -> None:
        # Nonzero exit, no receipt file -- a genuine crash, must be caught.
        result, outputs = _run_shadow_step(
            "shadow-checks-structure", tmp_path, exit_code=1, write_receipt=False
        )
        assert result.returncode == 0
        assert outputs.get("receipt_written") == "false"


def _run_rejection_script(results: dict[str, str]) -> subprocess.CompletedProcess:
    body = _rejection_script()
    env = {
        **os.environ,
        "RECEIPT_STRUCTURE": results["structure"],
        "RECEIPT_LINT": results["lint"],
        "RECEIPT_TEST": results["test"],
        "RECEIPT_SECURITY": results["security"],
        "RECEIPT_PACKAGE": results["package"],
    }
    return subprocess.run(
        [sys.executable, "-c", body],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


ALL_TRUE = {"structure": "true", "lint": "true", "test": "true", "security": "true", "package": "true"}


class TestAggregateRejectionGate:
    def test_all_receipts_present_is_accepted(self) -> None:
        result = _run_rejection_script(ALL_TRUE)
        assert result.returncode == 0, result.stderr

    def test_producer_with_no_receipt_is_rejected(self) -> None:
        # The genuine-crash case: no receipt at all. The guard's real purpose.
        results = {**ALL_TRUE, "test": "false"}
        result = _run_rejection_script(results)
        assert result.returncode == 1, (
            "a producer with no receipt evidence must still be rejected"
        )
        assert "test" in result.stderr

    def test_producer_that_failed_but_wrote_a_receipt_is_not_rejected(self) -> None:
        # This is the false-rejection defect: a group that ran and legitimately
        # returned FAIL/BLOCKED (and so wrote a receipt) must NOT be treated
        # as a rejected producer -- only the receipt's own status feeds the
        # eventual aggregate verdict, not this gate.
        result = _run_rejection_script(ALL_TRUE)
        assert result.returncode == 0, (
            "a producer whose check exited 2/3 but wrote a receipt "
            f"(receipt_written='true') must not be rejected: {result.stderr}"
        )
