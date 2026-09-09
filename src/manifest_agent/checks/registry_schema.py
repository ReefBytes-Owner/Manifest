"""Structural JSON-schema validation for project-check registries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def _schema_path() -> Path:
    repository_schema = (
        Path(__file__).resolve().parents[3] / "schemas" / "project-checks.schema.json"
    )
    if repository_schema.is_file():
        return repository_schema
    return Path(__file__).resolve().parents[1] / "data" / "project-checks.schema.json"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_validated_document(path: Path) -> dict[str, Any]:
    """Read a registry document and validate it against the bundled schema."""
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except OSError as exc:
        raise ValueError(f"cannot read registry {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in registry {path}: {exc.msg}") from exc
    if not isinstance(document, dict):
        raise ValueError("registry schema validation failed: root must be an object")

    try:
        schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load project-check registry schema: {exc}") from exc
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: list(error.path),
    )
    if errors:
        error = errors[0]
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise ValueError(
            f"registry schema validation failed at {location}: {error.message}"
        )
    return document
