"""Static release-artifact assertions for the protected live parity workflow."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tools.build_manifest_release import build_release

_ARCHIVE_BASE_URL = "https://downloads.example.invalid/manifest"


def _workflow(repo_root: Path) -> str:
    return (repo_root / ".github/workflows/plugin-parity-live.yml").read_text(
        encoding="utf-8"
    )


def _verifier(workflow: str) -> str:
    marker = (
        'python3 - "$ARCHIVE" "$METADATA" "$GITHUB_SHA" "$ARCHIVE_BASE_URL" <<\'PY\'\n'
    )
    verifier = workflow.split(marker, 1)[1].split("          PY\n", 1)[0]
    return "\n".join(line.removeprefix("          ") for line in verifier.splitlines())


def _run_verifier(verifier: str, archive: Path, metadata: Path, commit: str) -> None:
    previous_argv = sys.argv
    try:
        sys.argv = [
            "verify-release",
            str(archive),
            str(metadata),
            commit,
            _ARCHIVE_BASE_URL,
        ]
        exec(compile(verifier, "plugin-parity-live-verifier", "exec"), {})
    finally:
        sys.argv = previous_argv


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repo), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )


def _release_artifact(repo_root: Path, tmp_path: Path) -> tuple[Path, Path, str]:
    # `git clone` (or any other `git ... <repo_root>` invocation) of the
    # running tree fails when this test itself runs inside a materialized
    # candidate: the candidate has a synthetic `.git`, not a real, clonable
    # repo. Build the fixture entirely under tmp_path by copying the tree
    # from the filesystem -- never invoking git against repo_root -- then
    # give the fixture its own real, fresh git history (same rule as
    # test_check_runner_preparation_groups.py's tmp_path fixture repos).
    source = tmp_path / "release-source"
    shutil.copytree(
        repo_root,
        source,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git"),
    )
    _git(source, "init", "--quiet")
    _git(source, "config", "user.email", "manifest@example.invalid")
    _git(source, "config", "user.name", "Manifest Test")
    _git(source, "add", ".")
    _git(source, "commit", "--quiet", "-m", "release fixture")
    release = build_release(source, tmp_path / "release", _ARCHIVE_BASE_URL)
    commit = _git(source, "rev-parse", "HEAD").stdout.strip()
    return release.archive, release.metadata, commit


def _rewrite_archive(archive: Path, mutate) -> None:
    entries = []
    with tarfile.open(archive, mode="r:gz") as source:
        for member in source.getmembers():
            payload = source.extractfile(member)
            assert payload is not None
            info = copy.copy(member)
            entries.append((info, mutate(member.name, payload.read(), info)))
    with tarfile.open(archive, mode="w:gz") as destination:
        for info, payload in entries:
            info.size = len(payload)
            destination.addfile(info, fileobj=io.BytesIO(payload))


def _update_archive_checksum(archive: Path, metadata: Path) -> None:
    document = json.loads(metadata.read_text(encoding="utf-8"))
    document["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    metadata.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_live_parity_workflow_builds_and_uploads_immutable_release_artifacts() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    workflow = _workflow(repo_root)

    assert "git archive" not in workflow
    assert "uv run python tools/build_manifest_release.py" in workflow
    assert "--output-dir live-release" in workflow
    assert '--archive-base-url "$ARCHIVE_BASE_URL"' in workflow
    assert (
        'ARCHIVE_BASE_URL="https://github.com/${GITHUB_REPOSITORY}/actions/runs/'
        '${GITHUB_RUN_ID}/artifacts"'
    ) in workflow
    assert "Verify immutable release metadata" in workflow
    assert 'metadata["commit"] == commit' in workflow
    assert "release commit differs from workflow commit" in workflow
    assert 'metadata["archive_sha256"] == checksum(archive.read_bytes())' in workflow
    assert "release archive checksum does not match metadata" in workflow
    assert (
        'sha256sum "$WHEEL" "$ARCHIVE" "$METADATA" | tee release-checksums.txt'
        in workflow
    )
    assert "live-release/manifest-plugins-*.tar.gz" in workflow
    assert "live-release/manifest-release.json" in workflow
    assert 'uvx --from "$WHEEL" manifest install' in workflow
    assert 'uvx --from "$WHEEL" manifest reconcile' in workflow
    assert 'uvx --from "$WHEEL" manifest uninstall' in workflow


def test_live_parity_workflow_only_runs_pull_requests_with_the_live_label() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    workflow = _workflow(repo_root)

    assert "  workflow_dispatch:\n" in workflow
    assert "  workflow_call:\n" in workflow
    assert "  pull_request:\n    types: [labeled]\n" in workflow
    assert "pull_request_target:" not in workflow
    assert "types: [opened" not in workflow
    assert "types: [synchronize" not in workflow
    assert "types: [reopened" not in workflow
    assert "github.event_name != 'pull_request' ||" in workflow
    assert (
        "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    )
    assert "github.event.label.name == 'manifest-live-parity'" in workflow


def test_live_parity_workflow_embeds_a_strict_archive_verifier() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    verifier = _verifier(_workflow(repo_root))

    compile(verifier, "plugin-parity-live-verifier", "exec")
    assert "DOMAIN_BUNDLES = (" in verifier
    assert "TOP_LEVEL_KEYS" in verifier
    assert "BUNDLE_KEYS" in verifier
    assert "FILE_KEYS" in verifier
    assert 'tarfile.open(archive, mode="r:gz")' in verifier
    assert "archive has duplicate member" in verifier
    assert "archive has unlisted member" in verifier
    assert "archive member mode differs" in verifier
    assert 'member.uname == "" and member.gname == ""' in verifier
    assert "set(observed) == set(files)" in verifier
    assert "bundle_checksum(name, files)" in verifier
    assert "release marketplace bundle set is invalid" in verifier
    assert "canonical_marketplace(version)" in verifier
    assert "release marketplace metadata differs" in verifier


def test_embedded_verifier_rejects_archive_and_marketplace_mutations(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    verifier = _verifier(_workflow(repo_root))
    archive, metadata, commit = _release_artifact(repo_root, tmp_path)
    original_archive = archive.read_bytes()
    original_metadata = metadata.read_bytes()

    _run_verifier(verifier, archive, metadata, commit)

    archive.write_bytes(original_archive)
    metadata.write_bytes(original_metadata)
    special_mode_mutated = False

    def add_special_mode(_name: str, payload: bytes, info) -> bytes:
        nonlocal special_mode_mutated
        if info.mode == 0o755:
            info.mode = 0o4755
            special_mode_mutated = True
        return payload

    _rewrite_archive(
        archive,
        add_special_mode,
    )
    assert special_mode_mutated
    _update_archive_checksum(archive, metadata)
    with pytest.raises(SystemExit, match="archive member mode differs"):
        _run_verifier(verifier, archive, metadata, commit)

    for field, value in (
        ("source", "https://example.invalid/manifest-docs"),
        ("version", "9.9.9"),
        ("description", "tampered marketplace metadata"),
    ):
        archive.write_bytes(original_archive)
        metadata.write_bytes(original_metadata)

        def mutate(
            name: str, payload: bytes, _info, field: str = field, value: str = value
        ) -> bytes:
            if name.endswith("/.claude-plugin/marketplace.json"):
                document = json.loads(payload)
                document["plugins"][0][field] = value
                payload = (
                    json.dumps(document, indent=2, sort_keys=True) + "\n"
                ).encode()
                index = json.loads(metadata.read_text(encoding="utf-8"))
                record = index["files"][".claude-plugin/marketplace.json"]
                record["sha256"] = hashlib.sha256(payload).hexdigest()
                record["size"] = len(payload)
                metadata.write_text(
                    json.dumps(index, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            return payload

        _rewrite_archive(archive, mutate)
        _update_archive_checksum(archive, metadata)
        with pytest.raises(SystemExit, match="release marketplace metadata differs"):
            _run_verifier(verifier, archive, metadata, commit)
