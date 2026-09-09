"""Map executed-process outcomes to the check-runner's PASS/FAIL/BLOCKED status.

Extracted from ``runner.py`` (Finding 5, final fix wave for Tasks 9-10) so the
status-contract decision -- which exit codes are honest evidence of a real
PASS/FAIL versus an unverifiable BLOCKED outcome -- lives in one place
alongside the diagnostic-bounding helpers it depends on.
"""

from __future__ import annotations

from manifest_agent.process import redact_text

from .models import CheckResult, CheckSpec, PreparationSpec
from .process import CAPTURE_LIMIT, TRUNCATION_MARKER, ProcessResult

_CONTRACT_EXIT_CODES = frozenset({0, 2, 3})


def diagnostics(result: ProcessResult) -> str:
    return bounded_text(result.error + result.stdout + result.stderr)


def bounded_text(value: str) -> str:
    value = redact_text(value).encode()
    if len(value) > CAPTURE_LIMIT:
        value = value[: CAPTURE_LIMIT - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    return value.decode("utf-8", errors="ignore")


def blocked(
    check: CheckSpec | PreparationSpec, diagnostic: str, result: ProcessResult
) -> CheckResult:
    return CheckResult(
        check.id,
        "BLOCKED",
        result.returncode,
        result.duration_seconds,
        redact_text(diagnostic),
        (),
    )


def executed_status(check: CheckSpec, result: ProcessResult) -> tuple[str, str]:
    """Map an executed process's exit code to (PASS/FAIL/BLOCKED, diagnostic note).

    Repo-owned check bodies (``tools/project_checks/*.py``) deliberately
    implement this project's 0/2/3 status contract; only those checks, as
    declared by ``honors_status_contract`` in the registry, get exit 3
    honored as BLOCKED. Third-party tool exit codes carry no such meaning
    and stay FAIL whenever the process actually ran.

    A contract-honoring body that exits with anything outside {0, 2, 3} --
    e.g. 1 from an uncaught traceback -- has violated the contract it
    declared: its exit code no longer distinguishes FAIL from BLOCKED, so
    the outcome cannot be honestly recorded as either "ran and passed" or
    "ran and found problems". Such a run is reported as BLOCKED with a
    diagnostic naming the observed exit code, rather than silently reading
    as FAIL.
    """
    if result.returncode == 0:
        return "PASS", ""
    if check.honors_status_contract:
        if result.returncode == 3:
            return "BLOCKED", ""
        if result.returncode not in _CONTRACT_EXIT_CODES:
            return (
                "BLOCKED",
                "contract violation: honors_status_contract check exited "
                f"{result.returncode} (expected 0, 2, or 3)",
            )
    return "FAIL", ""
