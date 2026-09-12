"""`manifest provision` CLI contract, exercised through the real entry point.

`file://` lock URLs let these tests drive the actual `default_fetcher` (real
`urllib.request.urlopen`, no injected fake) while staying fully offline --
`file://` never touches the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

from click.testing import CliRunner, Result

from manifest_agent.cli import cli


def _tar_gz_with(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name=name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _write_lock(
    tmp_path: Path,
    *,
    sha256: str | None,
    archive_path: Path,
    exe_sha256: str | None = "unset",
) -> Path:
    """`exe_sha256` defaults to `sha256` unless a distinct value is given."""
    lock = {
        "schema_version": 1,
        "tools": {
            "demo": {
                "kind": "binary",
                "version": "1.0.0",
                "platforms": {
                    "the-platform": {
                        "url": f"file://{archive_path}",
                        "sha256": sha256,
                        "exe_sha256": sha256 if exe_sha256 == "unset" else exe_sha256,
                        "path_in_archive": "demo",
                    }
                },
            }
        },
    }
    lock_path = tmp_path / "toolchain.lock.json"
    lock_path.write_text(json.dumps(lock))
    return lock_path


def _invoke(lock_path: Path, store: Path, *extra: str) -> Result:
    return CliRunner().invoke(
        cli,
        [
            "provision",
            "--lock",
            str(lock_path),
            "--store",
            str(store),
            "--platform",
            "the-platform",
            *extra,
        ],
    )


def test_offline_against_empty_store_exits_3(tmp_path):
    archive_path = tmp_path / "demo.tar.gz"
    archive_path.write_bytes(_tar_gz_with("demo", b"content"))
    lock_path = _write_lock(tmp_path, sha256="a" * 64, archive_path=archive_path)
    store = tmp_path / "store"
    result = _invoke(lock_path, store, "--offline", "--json")
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "incomplete"
    assert payload["problems"]


def test_provision_then_offline_check_succeeds(tmp_path):
    content = b"#!/bin/sh\necho demo\n"
    archive_bytes = _tar_gz_with("demo", content)
    archive_path = tmp_path / "demo.tar.gz"
    archive_path.write_bytes(archive_bytes)
    sha = hashlib.sha256(archive_bytes).hexdigest()
    exe_sha = hashlib.sha256(content).hexdigest()
    lock_path = _write_lock(
        tmp_path, sha256=sha, archive_path=archive_path, exe_sha256=exe_sha
    )
    store = tmp_path / "store"

    provision_result = _invoke(lock_path, store, "--json")
    assert provision_result.exit_code == 0, provision_result.output
    payload = json.loads(provision_result.output)
    assert payload["status"] == "complete"
    assert payload["outcomes"][0]["status"] == "provisioned"

    offline_result = _invoke(lock_path, store, "--offline", "--json")
    assert offline_result.exit_code == 0, offline_result.output
    assert json.loads(offline_result.output)["status"] == "complete"


def test_provision_digest_mismatch_exits_3(tmp_path):
    archive_bytes = _tar_gz_with("demo", b"content")
    archive_path = tmp_path / "demo.tar.gz"
    archive_path.write_bytes(archive_bytes)
    lock_path = _write_lock(tmp_path, sha256="0" * 64, archive_path=archive_path)
    store = tmp_path / "store"
    result = _invoke(lock_path, store, "--json")
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert "digest mismatch" in payload["outcomes"][0]["reason"]


def test_provision_refuses_when_exe_sha256_is_unset(tmp_path):
    """Archive `sha256` alone is not enough: `exe_sha256: null` must still
    BLOCK as unattested, matching the committed real lock's current state."""
    archive_bytes = _tar_gz_with("demo", b"content")
    archive_path = tmp_path / "demo.tar.gz"
    archive_path.write_bytes(archive_bytes)
    sha = hashlib.sha256(archive_bytes).hexdigest()
    lock_path = _write_lock(
        tmp_path, sha256=sha, archive_path=archive_path, exe_sha256=None
    )
    store = tmp_path / "store"
    result = _invoke(lock_path, store, "--json")
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert "unattested" in payload["outcomes"][0]["reason"]


def test_import_wrong_hash_is_refused(tmp_path):
    archive_path = tmp_path / "demo.tar.gz"
    archive_path.write_bytes(_tar_gz_with("demo", b"irrelevant"))
    lock_path = _write_lock(tmp_path, sha256="f" * 64, archive_path=archive_path)
    store = tmp_path / "store"
    external = tmp_path / "external-binary"
    external.write_bytes(b"not the pinned bytes")
    result = _invoke(lock_path, store, "--import", f"demo={external}", "--json")
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert "digest mismatch" in payload["outcomes"][0]["reason"]
    assert not (store / "tools").exists()


def test_import_combined_with_offline_is_a_usage_error(tmp_path):
    archive_path = tmp_path / "demo.tar.gz"
    archive_path.write_bytes(_tar_gz_with("demo", b"irrelevant"))
    lock_path = _write_lock(tmp_path, sha256="f" * 64, archive_path=archive_path)
    store = tmp_path / "store"
    external = tmp_path / "external-binary"
    external.write_bytes(b"irrelevant")
    result = _invoke(
        lock_path, store, "--import", f"demo={external}", "--offline", "--json"
    )
    assert result.exit_code == 2, result.output
    assert "cannot be combined" in result.output


def test_import_combined_with_only_is_a_usage_error(tmp_path):
    archive_path = tmp_path / "demo.tar.gz"
    archive_path.write_bytes(_tar_gz_with("demo", b"irrelevant"))
    lock_path = _write_lock(tmp_path, sha256="f" * 64, archive_path=archive_path)
    store = tmp_path / "store"
    external = tmp_path / "external-binary"
    external.write_bytes(b"irrelevant")
    result = _invoke(
        lock_path, store, "--import", f"demo={external}", "--only", "demo", "--json"
    )
    assert result.exit_code == 2, result.output
    assert "cannot be combined" in result.output


def test_store_inside_repo_cwd_is_refused(tmp_path):
    archive_path = tmp_path / "demo.tar.gz"
    archive_path.write_bytes(_tar_gz_with("demo", b"content"))
    lock_path = _write_lock(tmp_path, sha256="a" * 64, archive_path=archive_path)
    unsafe_store = Path.cwd() / ".manifest-toolchain-store-test"
    result = _invoke(lock_path, unsafe_store, "--offline", "--json")
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert "must not resolve inside" in payload["problems"][0]
