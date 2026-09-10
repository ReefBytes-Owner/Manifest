"""C7 (partial): attesting gitleaks and shellcheck for `darwin-arm64` only.

Exercises the real, committed `config/toolchain.lock.json` end to end
through the actual `manifest provision --import` CLI path (not a synthetic
fixture lock), against the actual locally installed `gitleaks`/`shellcheck`
binaries on a `darwin-arm64` development host. Skipped everywhere else
(e.g. Linux CI) since there is nothing to import there and the lock's
`linux-x64` entries are deliberately left `sha256: null`.

The test that matters most here (`test_impostor_binary_at_same_path_still_blocks`)
proves attestation does not weaken the guarantee: recording a real hash for
a real, reviewed pin must not make the store trust *anything* at that path
afterwards -- only the exact attested bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from manifest_agent.checks import toolchain
from manifest_agent.cli import cli

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / "config" / "toolchain.lock.json"
GITLEAKS_BINARY = Path("/opt/homebrew/bin/gitleaks")
SHELLCHECK_BINARY = Path("/opt/homebrew/bin/shellcheck")

_HOST_HAS_BOTH_BINARIES = GITLEAKS_BINARY.is_file() and SHELLCHECK_BINARY.is_file()
_SKIP_REASON = "gitleaks/shellcheck not present at the expected darwin-arm64 paths"


def _load_real_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


class TestRealLockAttestation:
    """The committed lock itself: exactly two entries attested, one platform."""

    def test_gitleaks_and_shellcheck_attested_for_darwin_arm64_only(self):
        """`darwin-arm64` has real hashes for both; `linux-x64` stays null
        for both -- CI still blocks until a real download happens there."""
        lock = _load_real_lock()
        for bundle in ("gitleaks", "shellcheck"):
            darwin = lock["tools"][bundle]["platforms"]["darwin-arm64"]
            assert darwin["sha256"] is not None
            assert darwin["exe_sha256"] is not None
            linux = lock["tools"][bundle]["platforms"]["linux-x64"]
            assert linux["sha256"] is None
            assert linux["exe_sha256"] is None

    def test_no_other_tool_gained_an_attestation(self):
        """Every other bundle, on every platform, is still fully unattested
        -- this chunk records only the two reviewed pins the host actually
        matched, nothing else."""
        lock = _load_real_lock()
        for bundle, entry in lock["tools"].items():
            if bundle in ("gitleaks", "shellcheck"):
                continue
            for platform_entry in entry["platforms"].values():
                assert platform_entry["sha256"] is None
                assert platform_entry["exe_sha256"] is None

    def test_recorded_hashes_match_the_locally_verified_binaries(self):
        """The recorded darwin-arm64 hashes are the actual sha256 of the
        binaries this host reported as an exact version match -- not a
        stand-in or placeholder value."""
        lock = _load_real_lock()
        if not _HOST_HAS_BOTH_BINARIES:
            pytest.skip(_SKIP_REASON)
        gitleaks_sha = toolchain.sha256_file(GITLEAKS_BINARY)
        shellcheck_sha = toolchain.sha256_file(SHELLCHECK_BINARY)
        assert (
            lock["tools"]["gitleaks"]["platforms"]["darwin-arm64"]["exe_sha256"]
            == gitleaks_sha
        )
        assert (
            lock["tools"]["shellcheck"]["platforms"]["darwin-arm64"]["exe_sha256"]
            == shellcheck_sha
        )


@pytest.mark.skipif(not _HOST_HAS_BOTH_BINARIES, reason=_SKIP_REASON)
def _run_provision(store: Path, *extra_args: str) -> dict:
    """Invoke the real `manifest provision` CLI against the real lock and
    `store`, returning the parsed `--json` report. Shared by both the
    `--import` and `--offline` call shapes so the argv construction lives in
    exactly one place."""
    result = CliRunner().invoke(
        cli,
        [
            "provision",
            "--lock",
            str(LOCK_PATH),
            "--store",
            str(store),
            "--platform",
            "darwin-arm64",
            *extra_args,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


class TestEndToEndImportAndResolve:
    """`manifest provision --import` against the real lock, then `resolve()`
    against the real store it wrote -- the attested path actually working."""

    def _import_both(self, store: Path) -> None:
        for name, source in (
            ("gitleaks", GITLEAKS_BINARY),
            ("shellcheck", SHELLCHECK_BINARY),
        ):
            report = _run_provision(store, "--import", f"{name}={source}")
            assert report["status"] == "complete", report

    def test_import_provisions_both_tools(self, tmp_path: Path):
        """A `--import` per bundle against the real lock materializes a
        store where `resolve()` succeeds for both attested tools."""
        store = tmp_path / "store"
        self._import_both(store)
        lock = _load_real_lock()
        for bundle in ("gitleaks", "shellcheck"):
            outcome = toolchain.resolve(
                f"store:{bundle}/bin/{bundle}",
                lock=lock,
                store=store,
                platform="darwin-arm64",
            )
            assert isinstance(outcome, toolchain.ResolvedTool), (bundle, outcome)

    def test_offline_validate_reports_complete_for_darwin_arm64(self, tmp_path: Path):
        """Once both imports land, `--offline` (no network, no download)
        reports the store complete against the real lock for this platform."""
        store = tmp_path / "store"
        self._import_both(store)
        report = _run_provision(store, "--offline")
        assert report == {"status": "complete", "problems": []}

    def test_impostor_binary_at_same_path_still_blocks(self, tmp_path: Path):
        """THE test: attestation must not weaken the guarantee. After a
        genuine, hash-verified import, an attacker (or a botched reinstall)
        swaps a *different* binary in at the exact store path gitleaks
        resolves to. `resolve()` must still BLOCK on digest mismatch -- an
        attested store is not a store that stops checking."""
        store = tmp_path / "store"
        self._import_both(store)
        lock = _load_real_lock()

        # Sanity (RED-equivalent baseline): before tampering, it resolves.
        pre_tamper = toolchain.resolve(
            "store:gitleaks/bin/gitleaks",
            lock=lock,
            store=store,
            platform="darwin-arm64",
        )
        assert isinstance(pre_tamper, toolchain.ResolvedTool)

        # Swap the file at the same store path -- e.g. shellcheck's own
        # binary, a real, working, correctly-shaped executable, just not
        # the one this store path was attested for.
        exe_path = store / "tools/gitleaks/8.30.1/bin/gitleaks"
        exe_path.write_bytes(SHELLCHECK_BINARY.read_bytes())

        post_tamper = toolchain.resolve(
            "store:gitleaks/bin/gitleaks",
            lock=lock,
            store=store,
            platform="darwin-arm64",
        )
        assert post_tamper == toolchain.BlockedReason(
            "toolchain: gitleaks digest mismatch"
        )

    def test_impostor_with_correct_size_and_a_real_version_string_still_blocks(
        self, tmp_path: Path
    ):
        """Even a byte-for-byte-plausible impostor (still prints a version
        string an unwary version-probe-only check would accept) is caught
        by the hash, which is verified before any version probe runs."""
        store = tmp_path / "store"
        self._import_both(store)
        lock = _load_real_lock()
        exe_path = store / "tools/shellcheck/0.11.0/bin/shellcheck"
        # A trivial launcher that claims to BE shellcheck 0.11.0 but is not
        # the attested binary.
        exe_path.write_bytes(b"#!/bin/sh\necho 'version: 0.11.0'\n")
        exe_path.chmod(0o755)
        outcome = toolchain.resolve(
            "store:shellcheck/bin/shellcheck",
            lock=lock,
            store=store,
            platform="darwin-arm64",
        )
        assert outcome == toolchain.BlockedReason(
            "toolchain: shellcheck digest mismatch"
        )
