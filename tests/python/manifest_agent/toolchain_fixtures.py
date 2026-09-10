"""Shared fixture-store builders for the toolchain resolver test files."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from manifest_agent.checks import toolchain


def _write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _lock(
    *, exe_sha256: str | None, sha256: str | None = None, platform: str = "linux-x64"
) -> dict:
    """A one-bundle (`gitleaks`) lock. `sha256` (archive hash) defaults to
    `exe_sha256` since these fixtures have no real archive step."""
    return {
        "schema_version": 1,
        "tools": {
            "gitleaks": {
                "kind": "binary",
                "version": "8.30.1",
                "platforms": {
                    platform: {
                        "url": "https://example.invalid/gitleaks.tar.gz",
                        "sha256": sha256 if sha256 is not None else exe_sha256,
                        "exe_sha256": exe_sha256,
                        "path_in_archive": "gitleaks",
                    }
                },
            }
        },
    }


@dataclass(frozen=True)
class _StoreOverrides:
    """Everything `_provision_store` may need to vary between tests, bundled
    to stay inside the 5-parameter constitution ceiling."""

    exe_bytes: bytes = b"#!/bin/sh\necho gitleaks\n"
    relative_exe: str = "tools/gitleaks/8.30.1/bin/gitleaks"
    lock_digest: str | None = None
    source_sha256: str | None = None
    manifest_exe_sha256: str | None = None


_DEFAULT_OVERRIDES = _StoreOverrides()


def _provision_store(
    store: Path, lock: dict, overrides: _StoreOverrides = _DEFAULT_OVERRIDES
) -> str:
    """Write a fixture store manifest for the `gitleaks` bundle; returns the
    real sha256 of the exe bytes actually written to disk."""
    exe_path = store / overrides.relative_exe
    actual_sha = _write(exe_path, overrides.exe_bytes)
    platform_entry = lock["tools"]["gitleaks"]["platforms"]["linux-x64"]
    manifest = {
        "schema_version": 1,
        "lock_digest": overrides.lock_digest or toolchain.lock_digest(lock),
        "tools": {
            "gitleaks": {
                "source_sha256": overrides.source_sha256 or platform_entry["sha256"],
                "executables": {
                    "bin/gitleaks": {
                        "path": overrides.relative_exe,
                        "sha256": overrides.manifest_exe_sha256 or actual_sha,
                    }
                },
            }
        },
    }
    (store / "manifest.json").write_text(json.dumps(manifest))
    return actual_sha
