"""C7j / Correction 8 rule 1: `distribution_set_digest` reproducibility.

Materializing `project-env`/`config-env` twice from the SAME committed lock,
on two DIFFERENT checkout paths, previously produced two DIFFERENT digests --
`toolchain.resolve()` then BLOCKed every dependent check with "digest
mismatch" on a fresh store, even though nothing about the lock or the
installed packages had actually changed.

Found by materializing both bundles twice for real (`git worktree add
--detach` to a second checkout path, `manifest provision --only project-env
--only config-env` into two fresh store roots) and diffing the resulting
`RECORD` files byte-for-byte: every ordinary (non-path) dependency's RECORD
was identical; only the root project's two local path dependencies
(`manifest-model-policy`, `manifest-runtime`, both installed editable)
differed, and only in three files -- `*.dist-info/direct_url.json`,
`*.dist-info/uv_cache.json`, and a top-level `*.pth` file -- each of which
embeds the checkout's own absolute path. See
`toolchain_env._is_location_or_time_dependent_record_line` for the fix and
its full docstring.

`TestDigestExcludesLocationDependentMetadata` reproduces that finding
offline, on hand-built fixtures, for each of the three shapes individually
(plus a control: a real payload tamper still flips the digest).
`TestRealMaterializationDigestIsReproducibleAcrossCheckouts` proves it live,
network-gated on `MANIFEST_C7B_NETWORK=1` (real `git worktree`, real `uv
sync`, real `manifest provision`, two store roots, then a THIRD fresh store
proving `toolchain.resolve()` accepts the result).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from manifest_agent.checks import toolchain
from manifest_agent.checks import toolchain_env as te
from manifest_agent.checks import toolchain_provision as provision_mod

REPO_ROOT = Path(__file__).resolve().parents[3]
_NETWORK = os.environ.get("MANIFEST_C7B_NETWORK") == "1"
_NETWORK_SKIP = "set MANIFEST_C7B_NETWORK=1 to materialize the real envs"


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _env_with_editable_metadata(root: Path, *, checkout: str) -> None:
    """A hand-built env carrying the exact three location-dependent shapes
    the real materialization diff found, each keyed by `checkout` so two
    calls with different `checkout` values reproduce two different
    checkouts' output byte-for-byte."""
    dist_info = root / "lib/python3.11/site-packages/demo_pkg-1.0.dist-info"
    _write(
        dist_info / "RECORD",
        (
            b"demo_pkg/__init__.py,sha256=abc,10\n"
            b"../../../bin/demo,sha256=xyz,20\n"
            b"_demo_pkg.pth,sha256=pth" + checkout.encode() + b",30\n"
            b"demo_pkg-1.0.dist-info/direct_url.json,sha256=url"
            + checkout.encode()
            + b",40\n"
            b"demo_pkg-1.0.dist-info/uv_cache.json,sha256=cache"
            + checkout.encode()
            + b",50\n"
        ),
    )
    _write(
        root / "lib/python3.11/site-packages" / "_demo_pkg.pth",
        f"/checkouts/{checkout}/src\n".encode(),
    )
    _write(
        dist_info / "direct_url.json",
        f'{{"url": "file:///checkouts/{checkout}"}}'.encode(),
    )
    _write(
        dist_info / "uv_cache.json",
        f'{{"path": "/checkouts/{checkout}"}}'.encode(),
    )
    _write(root / "bin/python", b"fake-interpreter")
    (root / "bin/demo").write_text(f"#!{root / 'bin/python'}\ndemo\n")
    (root / "bin/demo").chmod(0o700)


def _record_path(root: Path) -> Path:
    return root / "lib/python3.11/site-packages/demo_pkg-1.0.dist-info/RECORD"


_NSPKG_CONTENT = (
    "import sys, types, os;"
    "p = os.path.join(sys._getframe(1).f_locals['sitedir'], *('google',));"
    "importlib = __import__('importlib.util');"
    "__import__('importlib.machinery');"
    "m = sys.modules.setdefault('google', importlib.util.module_from_spec("
    "importlib.machinery.PathFinder.find_spec('google', [os.path.dirname(p)])));"
    "m = m or sys.modules.setdefault('google', types.ModuleType('google'));"
    "mp = (m or []) and m.__dict__.setdefault('__path__',[]);"
    "(p not in mp) and mp.append(p)\n"
)


def _nspkg_env(root: Path) -> Path:
    """An env carrying ONLY a setuptools namespace-package `.pth` shim --
    isolates its contribution to the digest from any other dist-info."""
    dist_info = root / "lib/python3.11/site-packages/nspkg-1.0.dist-info"
    _write(dist_info / "RECORD", b"google_nspkg.pth,sha256=abc,10\n")
    _write(
        root / "lib/python3.11/site-packages/google_nspkg.pth", _NSPKG_CONTENT.encode()
    )
    return root


def _checkout_root(checkout: str) -> Path:
    """The absolute root `_env_with_editable_metadata`'s `.pth` line embeds
    for `checkout` -- `/checkouts/{checkout}/src`'s parent."""
    return Path(f"/checkouts/{checkout}")


class TestDigestExcludesLocationDependentMetadata:
    """Offline reproduction of the real-checkout RECORD diff, one shape at
    a time -- never a network call, never the real store."""

    def test_digest_is_identical_across_two_different_checkout_paths(
        self, tmp_path: Path
    ):
        root_a = tmp_path / "checkout-a" / "env"
        root_b = tmp_path / "checkout-b" / "env"
        _env_with_editable_metadata(root_a, checkout="checkout-a")
        _env_with_editable_metadata(root_b, checkout="checkout-b")
        digest_a = te.distribution_set_digest(
            root_a, "python-env", checkout_root=_checkout_root("checkout-a")
        )
        digest_b = te.distribution_set_digest(
            root_b, "python-env", checkout_root=_checkout_root("checkout-b")
        )
        assert digest_a == digest_b

    def test_direct_url_json_alone_is_excluded(self, tmp_path: Path):
        root = tmp_path / "env"
        _env_with_editable_metadata(root, checkout="one")
        checkout_root = _checkout_root("one")
        before = te.distribution_set_digest(
            root, "python-env", checkout_root=checkout_root
        )
        record = _record_path(root)
        record.write_bytes(record.read_bytes().replace(b"urlone", b"urlDIFFERENT"))
        assert (
            te.distribution_set_digest(root, "python-env", checkout_root=checkout_root)
            == before
        )

    def test_uv_cache_json_alone_is_excluded(self, tmp_path: Path):
        root = tmp_path / "env"
        _env_with_editable_metadata(root, checkout="one")
        checkout_root = _checkout_root("one")
        before = te.distribution_set_digest(
            root, "python-env", checkout_root=checkout_root
        )
        record = _record_path(root)
        record.write_bytes(record.read_bytes().replace(b"cacheone", b"cacheDIFFERENT"))
        assert (
            te.distribution_set_digest(root, "python-env", checkout_root=checkout_root)
            == before
        )

    def test_top_level_pth_record_hash_tamper_is_ignored(self, tmp_path: Path):
        """The `.pth` file's RECORD hash/size fields are never part of the
        digest input -- only the (normalized) file content is -- so
        tampering just the RECORD line's own hash field changes nothing."""
        root = tmp_path / "env"
        _env_with_editable_metadata(root, checkout="one")
        checkout_root = _checkout_root("one")
        before = te.distribution_set_digest(
            root, "python-env", checkout_root=checkout_root
        )
        record = _record_path(root)
        record.write_bytes(record.read_bytes().replace(b"pthone", b"pthDIFFERENT"))
        assert (
            te.distribution_set_digest(root, "python-env", checkout_root=checkout_root)
            == before
        )

    def test_payload_line_tamper_still_changes_the_digest(self, tmp_path: Path):
        """Control: the exclusions above must never swallow a real payload
        change -- tampering the package's own source-file RECORD line still
        flips the digest."""
        root = tmp_path / "env"
        _env_with_editable_metadata(root, checkout="one")
        checkout_root = _checkout_root("one")
        before = te.distribution_set_digest(
            root, "python-env", checkout_root=checkout_root
        )
        record = _record_path(root)
        record.write_bytes(record.read_bytes().replace(b"sha256=abc", b"sha256=BAD"))
        assert (
            te.distribution_set_digest(root, "python-env", checkout_root=checkout_root)
            != before
        )

    def test_pth_import_line_is_untrusted(self, tmp_path: Path):
        """Correction 9, rule 1: a `.pth` line that is not a plain existing
        path -- e.g. an `import` hook, which Python's site machinery
        executes at interpreter start -- must BLOCK, never silently hash to
        a placeholder like a real path would."""
        root = tmp_path / "env"
        _env_with_editable_metadata(root, checkout="one")
        pth = root / "lib/python3.11/site-packages/_demo_pkg.pth"
        pth.write_text("import _demo_hook\n")
        with pytest.raises(te.UntrustedPthError):
            te.distribution_set_digest(
                root, "python-env", checkout_root=_checkout_root("one")
            )

    def test_pth_pointing_outside_known_roots_changes_the_digest(self, tmp_path: Path):
        """A `.pth` whose path is outside BOTH the store and the recorded
        checkout is untrusted -- the digest it would need to match never
        gets produced (raises), so it can never equal the attested one:
        the attestation BLOCKs rather than silently accepting an escaped
        `.pth`."""
        root = tmp_path / "env"
        _env_with_editable_metadata(root, checkout="one")
        attested = te.distribution_set_digest(
            root, "python-env", checkout_root=_checkout_root("one")
        )
        pth = root / "lib/python3.11/site-packages/_demo_pkg.pth"
        pth.write_text("/somewhere/else/entirely\n")
        with pytest.raises(te.UntrustedPthError):
            te.distribution_set_digest(
                root, "python-env", checkout_root=_checkout_root("one")
            )
        # No successfully-computed digest can equal the attested one if the
        # call never returns a digest at all -- confirmed by the raise
        # above; `attested` is retained to document the pre-tamper value.
        assert attested

    def test_setuptools_namespace_package_pth_is_trusted_verbatim(self, tmp_path: Path):
        """A real finding from materializing `config-env` for real:
        `google-generativeai` installs a setuptools namespace-package
        `.pth` shim (`<dist>-<version>-<pyver>-nspkg.pth`) whose content
        is an `import ...` line -- but it is a fixed, well-known template
        naming no checkout or store path at all (only `sitedir`, resolved
        at import time), so it must be trusted verbatim, not rejected as
        an untrusted `import` hook the way an arbitrary one would be. Two
        envs carrying the IDENTICAL shim, under different roots, must
        digest identically -- its bytes never vary with the checkout."""
        digest_a = te.distribution_set_digest(
            _nspkg_env(tmp_path / "checkout-a" / "env"), "python-env"
        )
        digest_b = te.distribution_set_digest(
            _nspkg_env(tmp_path / "checkout-b" / "env"), "python-env"
        )
        assert digest_a == digest_b


def _provision_into(repo_root: Path, store: Path, lock: dict, platform: str) -> None:
    outcomes = provision_mod.provision(
        lock,
        store,
        platform=platform,
        only=frozenset({"uv", "project-env", "config-env"}),
        repo_root=repo_root,
        env={"PATH": ""},
    )
    assert all(o.status == "provisioned" for o in outcomes), outcomes


def _bundle_digest(store: Path, bundle: str) -> str:
    (env_dir,) = (store / "tools" / bundle).iterdir()
    manifest = json.loads((store / "manifest.json").read_text())
    source_checkout = manifest["tools"][bundle].get("source_checkout")
    checkout_root = Path(source_checkout) if source_checkout else None
    return te.distribution_set_digest(
        env_dir, "python-env", store=store, checkout_root=checkout_root
    )


@pytest.mark.skipif(not _NETWORK, reason=_NETWORK_SKIP)
class TestRealMaterializationDigestIsReproducibleAcrossCheckouts:
    def test_project_env_and_config_env_digests_match_across_checkouts(
        self, tmp_path: Path
    ):
        checkout_b = tmp_path / "checkout-b"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(checkout_b), "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        try:
            self._provision_and_compare(checkout_b, tmp_path)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(checkout_b)],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
            )
            shutil.rmtree(checkout_b, ignore_errors=True)

    def _provision_and_compare(self, checkout_b: Path, tmp_path: Path) -> None:
        subprocess.run(
            ["git", "submodule", "update", "--init"],
            cwd=checkout_b,
            check=True,
            capture_output=True,
        )
        lock = json.loads((REPO_ROOT / "config" / "toolchain.lock.json").read_text())
        platform = toolchain.current_platform()
        store_a, store_b = tmp_path / "store-a", tmp_path / "store-b"
        _provision_into(REPO_ROOT, store_a, lock, platform)
        _provision_into(checkout_b, store_b, lock, platform)
        for bundle in ("project-env", "config-env"):
            digest_a = _bundle_digest(store_a, bundle)
            digest_b = _bundle_digest(store_b, bundle)
            assert digest_a == digest_b, (bundle, digest_a, digest_b)
        self._assert_resolves_on_a_third_fresh_store(lock, platform, tmp_path)

    def _assert_resolves_on_a_third_fresh_store(
        self, lock: dict, platform: str, tmp_path: Path
    ) -> None:
        store_c = tmp_path / "store-c"
        _provision_into(REPO_ROOT, store_c, lock, platform)
        resolved = toolchain.resolve(
            "store:project-env/bin/python", lock=lock, store=store_c, platform=platform
        )
        assert isinstance(resolved, toolchain.ResolvedTool), resolved
