"""C7: every `binary`-kind tool in the committed lock is attested for both
`linux-x64` and `darwin-arm64`, from the pinned release artifacts only.

Each hash was recorded from the archive at the entry's own `url` (archive
digest cross-checked against the publisher's checksum manifest or GitHub's
asset digest) and from the executable extracted at `path_in_archive` --
never from a locally installed build. C7-partial had recorded the Homebrew
builds of gitleaks/shellcheck (same version string, different bytes) as the
darwin-arm64 attestation and reused the binary hash as the *archive*
`sha256`, so a real `manifest provision` download would have BLOCKed on
"digest mismatch". Both defects are corrected in the lock; the offline tests
below pin the structural invariant that would have caught them.

The end-to-end download tests run only when `MANIFEST_C7_NETWORK=1` (they
fetch the real artifacts); everywhere else they skip -- a skip, never a
fake pass. The `python-env`/`node-env` kinds stay unattested until their
provisioner exists (C7b).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from manifest_agent.checks import toolchain
from manifest_agent.cli import cli

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / "config" / "toolchain.lock.json"
PLATFORMS = ("linux-x64", "darwin-arm64")
BINARY_TOOLS = ("gitleaks", "shfmt", "shellcheck", "uv", "node")
ENV_TOOLS = ("python-env", "node-env")
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz")
HOMEBREW_GITLEAKS = Path("/opt/homebrew/bin/gitleaks")

_NETWORK = os.environ.get("MANIFEST_C7_NETWORK") == "1"
_NETWORK_SKIP = "set MANIFEST_C7_NETWORK=1 to download the pinned artifacts"


def _load_real_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _platform_entries():
    lock = _load_real_lock()
    for bundle in BINARY_TOOLS:
        for platform in PLATFORMS:
            yield bundle, platform, lock["tools"][bundle]["platforms"][platform]


class TestRealLockAttestation:
    """The committed lock itself: five binary tools, two platforms, and the
    archive/executable digest relationship that proves the source."""

    def test_every_binary_tool_attested_on_both_platforms(self):
        """No `binary` entry is left `null` on either platform any more."""
        for bundle, platform, entry in _platform_entries():
            assert entry["sha256"] is not None, (bundle, platform)
            assert entry["exe_sha256"] is not None, (bundle, platform)

    def test_environment_and_cache_entries_are_attested_on_both_platforms(self):
        """Environment and npm-cache digests come from reviewed native runs."""
        lock = _load_real_lock()
        for bundle in ENV_TOOLS:
            platforms = lock["tools"][bundle]["platforms"]
            for platform, platform_entry in platforms.items():
                assert platform_entry["sha256"] is not None, (bundle, platform)
                assert platform_entry["exe_sha256"] is not None, (bundle, platform)

        cache_platforms = lock["caches"]["node-cache"]["platforms"]
        for platform, platform_entry in cache_platforms.items():
            assert platform_entry["digest"] is not None, ("node-cache", platform)

    def test_hashes_are_lowercase_sha256_hex(self):
        for bundle, platform, entry in _platform_entries():
            for field in ("sha256", "exe_sha256"):
                value = entry[field]
                assert len(value) == 64 and value == value.lower(), (
                    bundle,
                    platform,
                    field,
                )
                int(value, 16)

    def test_archive_digest_is_never_the_binary_digest(self):
        """The invariant C7-partial broke: for a tar archive, `sha256` is the
        archive's digest and `exe_sha256` is the extracted file's, and the
        two can never coincide. A raw-binary URL (shfmt) is the one case
        where they must be identical, with `path_in_archive` naming the
        download itself."""
        for bundle, platform, entry in _platform_entries():
            filename = entry["url"].rsplit("/", 1)[1]
            if filename.endswith(ARCHIVE_SUFFIXES):
                assert entry["sha256"] != entry["exe_sha256"], (bundle, platform)
                assert entry["path_in_archive"] != filename, (bundle, platform)
            else:
                assert entry["sha256"] == entry["exe_sha256"], (bundle, platform)
                assert entry["path_in_archive"] == filename, (bundle, platform)


def _run_provision(store: Path, platform: str, *extra_args: str) -> tuple[int, dict]:
    """Invoke the real `manifest provision` CLI against the real lock."""
    result = CliRunner().invoke(
        cli,
        [
            "provision",
            "--lock",
            str(LOCK_PATH),
            "--store",
            str(store),
            "--platform",
            platform,
            *extra_args,
            "--json",
        ],
    )
    return result.exit_code, json.loads(result.output)


def _homebrew_gitleaks_is_a_different_build() -> bool:
    if not HOMEBREW_GITLEAKS.is_file():
        return False
    probe = subprocess.run(
        [str(HOMEBREW_GITLEAKS), "version"], capture_output=True, text=True, timeout=30
    )
    if probe.returncode != 0 or "8.30.1" not in probe.stdout:
        return False
    recorded = _load_real_lock()["tools"]["gitleaks"]["platforms"]["darwin-arm64"]
    return toolchain.sha256_file(HOMEBREW_GITLEAKS) != recorded["exe_sha256"]


class TestSameVersionDifferentBuild:
    """A locally installed build that reports the pinned version but is not
    the pinned artifact must not be adoptable -- the exact failure mode of
    the C7-partial attestation."""

    def test_import_of_a_same_version_different_build_blocks(self, tmp_path: Path):
        if not _homebrew_gitleaks_is_a_different_build():
            pytest.skip("no Homebrew gitleaks 8.30.1 with different bytes on this host")
        exit_code, report = _run_provision(
            tmp_path / "store",
            "darwin-arm64",
            "--import",
            f"gitleaks={HOMEBREW_GITLEAKS}",
        )
        assert exit_code != 0
        outcomes = {o["bundle"]: o for o in report["outcomes"]}
        assert outcomes["gitleaks"]["status"] == "blocked"
        assert "digest mismatch" in outcomes["gitleaks"]["reason"]


@pytest.mark.skipif(not _NETWORK, reason=_NETWORK_SKIP)
class TestEndToEndDownload:
    """Real downloads of the pinned artifacts through the real CLI: every
    binary tool provisions, the store validates offline, and an impostor
    swapped in afterwards still BLOCKs."""

    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_download_provisions_every_binary_tool(self, tmp_path: Path, platform: str):
        store = tmp_path / platform
        exit_code, report = _run_provision(store, platform)
        outcomes = {o["bundle"]: o for o in report["outcomes"]}
        for bundle in BINARY_TOOLS:
            assert outcomes[bundle]["status"] == "provisioned", outcomes[bundle]
        for bundle in ENV_TOOLS:
            assert outcomes[bundle]["status"] == "blocked"
            assert "not yet provisionable" in outcomes[bundle]["reason"]
        # The env kinds keep the overall status honest: still blocked.
        assert exit_code == 3 and report["status"] == "blocked"
        offline_exit, offline_report = _run_provision(store, platform, "--offline")
        assert offline_exit == 0
        assert offline_report == {"status": "complete", "problems": []}

    def test_impostor_binary_at_same_path_still_blocks(self, tmp_path: Path):
        """After a genuine, hash-verified download, a different real binary
        swapped in at the exact store path must still BLOCK on digest
        mismatch -- an attested store is not a store that stops checking."""
        store = tmp_path / "store"
        _run_provision(
            store, "darwin-arm64", "--only", "gitleaks", "--only", "shellcheck"
        )
        lock = _load_real_lock()
        resolve = lambda: toolchain.resolve(  # noqa: E731
            "store:gitleaks/bin/gitleaks",
            lock=lock,
            store=store,
            platform="darwin-arm64",
        )
        assert isinstance(resolve(), toolchain.ResolvedTool)
        exe_path = store / "tools/gitleaks/8.30.1/bin/gitleaks"
        exe_path.write_bytes(
            (store / "tools/shellcheck/0.11.0/bin/shellcheck").read_bytes()
        )
        assert resolve() == toolchain.BlockedReason(
            "toolchain: gitleaks digest mismatch"
        )

    def test_impostor_with_a_real_version_string_still_blocks(self, tmp_path: Path):
        store = tmp_path / "store"
        _run_provision(store, "darwin-arm64", "--only", "shellcheck")
        exe_path = store / "tools/shellcheck/0.11.0/bin/shellcheck"
        exe_path.write_bytes(b"#!/bin/sh\necho 'version: 0.11.0'\n")
        exe_path.chmod(0o755)
        outcome = toolchain.resolve(
            "store:shellcheck/bin/shellcheck",
            lock=_load_real_lock(),
            store=store,
            platform="darwin-arm64",
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: shellcheck digest mismatch"
        )
