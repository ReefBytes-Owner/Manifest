"""Shared oracle-loading primitives for the check-profile parity tests.

Split out of test_check_profile_parity.py (C7f) so the hook-selector
contract (test_check_profile_hook_contract.py) and the retained/profile
contract (test_check_profile_parity.py) can both load
`config/check-preservation.json`/`config/project-checks.json` and resolve
the frozen `TASK7_DISPOSITIONS`/superseded-id sets without importing from
each other.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.python.manifest_agent import _c5_ids
from tools.project_checks.generated import TASK7_DISPOSITIONS as GENERATED
from tools.project_checks.gitleaks_check import TASK7_DISPOSITIONS as GITLEAKS
from tools.project_checks.hook_lint import TASK7_DISPOSITIONS as HOOK_LINT
from tools.project_checks.hooks import TASK7_DISPOSITIONS as HOOKS
from tools.project_checks.packages import TASK7_DISPOSITIONS as PACKAGES
from tools.project_checks.structure import TASK7_DISPOSITIONS as STRUCTURE

ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = ROOT / "config/project-checks.json"
PRESERVATION_PATH = ROOT / "config/check-preservation.json"
DISPOSITIONS = STRUCTURE | GENERATED | HOOKS | HOOK_LINT | PACKAGES | GITLEAKS
SUPERSEDED = _c5_ids.SUPERSEDED_IDS


def _raw_documents() -> tuple[dict, dict]:
    return (
        json.loads(PRESERVATION_PATH.read_text(encoding="utf-8")),
        json.loads(REGISTRY_PATH.read_text(encoding="utf-8")),
    )


def _check_by_id(registry: dict) -> dict[str, dict]:
    return {check["id"]: check for check in registry["checks"]}
