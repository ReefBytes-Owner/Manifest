"""`.pth` normalization for the python-env/node-env distribution-set digest.

Split out of `toolchain_env.py` to keep it under the Code Constitution's
500-line ceiling. Dependency-free like its sibling, for the same reason:
`normalized_pth_digest_line` must stay a pure function of on-disk bytes plus
two path parameters, callable from an offline test against a hand-built fake
env -- never the network, the lock, or the store manifest.

Correction 9 (phase-3-5-decisions.md): a `.pth` file is executed by Python's
site machinery at interpreter start (an `import ...` line in a `.pth` runs
code), so it cannot be excluded from the distribution-set digest the way
`direct_url.json`/`uv_cache.json` are (toolchain_env.py,
`_is_location_or_time_dependent_record_line`) -- excluding it would leave an
unanchored code-execution surface inside the trusted env. Instead every
`.pth` line is NORMALIZED to a checkout/store-independent placeholder before
hashing, and anything that is not a trusted plain-path shape is rejected.

`checkout_root` is never hardcoded here: it is the `source_checkout`
`manifest provision` records in the store manifest for a bundle at
materialization time (`toolchain_provision._record_env_bundle`) -- location
metadata, not a security-relevant hash. This is what makes the same lock
verify an env whether it was materialized from checkout A or checkout B: a
`.pth` line naming `<checkout>/configs/claude/scripts/manifest_model_policy`
normalizes to the identical `<CHECKOUT>/configs/claude/scripts/
manifest_model_policy` placeholder regardless of what `<checkout>` actually
was, because the caller always passes THAT materialization's own recorded
checkout root, not a fixed guess.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvTrust:
    """The store and (optional) provisioning-checkout root
    `toolchain_env.verify_env_exe` needs together to re-derive a python-env/
    node-env bundle's distribution-set digest (Correction 9): `checkout_root`
    is the `source_checkout` `manifest provision` recorded in the store
    manifest for this bundle -- `None` for a manifest written before
    Correction 9, or for a bundle with no editable path dependencies --
    used only to recognize a `.pth` line pointing at this materialization's
    own checkout, never as a security-relevant hash (a wrong value only
    makes the digest fail to match)."""

    store: Path
    checkout_root: Path | None = None


# setuptools' own namespace-package `.pth` shim (the `<dist>-<version>-
# <pyver>-nspkg.pth` shape, e.g. `google_generativeai-0.8.6-py3.13-nspkg.pth`
# for this repo's `google-generativeai` dependency) -- a fixed, well-known
# template with only the namespace tuple substituted, generated identically
# by every setuptools release for every namespace package on PyPI. Its
# bytes name no checkout or store path at all (only `sitedir`, resolved at
# IMPORT time from `sys._getframe`), so unlike an arbitrary `import` line it
# is already checkout/store-independent -- trusted verbatim, never rejected
# as an untrusted `import` hook, PROVIDED it matches this pattern exactly
# (anchored start/end; any deviation falls through to the ordinary
# plain-path check below and is rejected).
_NAMESPACE_PACKAGE_PTH = re.compile(
    r"^import sys, types, os;"
    r"p = os\.path\.join\(sys\._getframe\(1\)\.f_locals\['sitedir'\], \*\((?:'[^']+', ?)+\)\);"
    r"importlib = __import__\('importlib\.util'\);__import__\('importlib\.machinery'\);"
    r"m = sys\.modules\.setdefault\('(?P<name>[^']+)', importlib\.util\.module_from_spec\("
    r"importlib\.machinery\.PathFinder\.find_spec\('(?P=name)', \[os\.path\.dirname\(p\)\]\)\)\);"
    r"m = m or sys\.modules\.setdefault\('(?P=name)', types\.ModuleType\('(?P=name)'\)\);"
    r"mp = \(m or \[\]\) and m\.__dict__\.setdefault\('__path__',\[\]\);\(p not in mp\) and mp\.append\(p\)$"
)


class UntrustedPthError(ValueError):
    """A `.pth` file's content is not a trusted, plain-path shape.

    Raised for any `.pth` line that is not a plain absolute path resolving
    inside the recorded provisioning checkout or the store (an `import`
    hook, a relative path, or a path outside both known roots). Callers
    must treat this as BLOCKED, never as "not provisioned" (there is
    something there; it is untrusted, not absent)."""


def _relative_placeholder(candidate: Path, root: Path | None, label: str) -> str | None:
    """`f"<{label}>/{relative}"` if `candidate` lives inside `root`, trying
    both a lexical (`os.path.normpath`) and a filesystem-resolved
    (`os.path.realpath`) comparison -- else `None`."""
    if root is None:
        return None
    normalized_root = Path(os.path.normpath(str(root)))
    normalized_candidate = Path(os.path.normpath(str(candidate)))
    resolved_root = Path(os.path.realpath(root))
    resolved_candidate = Path(os.path.realpath(candidate))
    for target, base in (
        (normalized_candidate, normalized_root),
        (resolved_candidate, resolved_root),
    ):
        if target == base or target.is_relative_to(base):
            return f"<{label}>/{target.relative_to(base).as_posix()}"
    return None


def _placeholder_for_path(
    candidate: Path, store: Path | None, checkout_root: Path | None
) -> str | None:
    """The normalized placeholder for one `.pth` path-line value: inside
    `store` first, then inside `checkout_root`, else `None` (untrusted --
    it points somewhere neither known root recognizes)."""
    return _relative_placeholder(candidate, store, "STORE") or _relative_placeholder(
        candidate, checkout_root, "CHECKOUT"
    )


def _normalized_line_value(
    line: str, store: Path | None, checkout_root: Path | None
) -> str:
    """One `.pth` line, normalized to a checkout/store-independent
    placeholder -- raises `UntrustedPthError` for anything that is not a
    plain absolute path resolving inside a known root: an `import` hook
    (code Python's site machinery would execute), a relative path, or a
    path outside both the recorded checkout and the store."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    if not Path(stripped).is_absolute():
        raise UntrustedPthError(f"untrusted .pth line: {stripped!r}")
    placeholder = _placeholder_for_path(Path(stripped), store, checkout_root)
    if placeholder is None:
        raise UntrustedPthError(f"untrusted .pth line: {stripped!r}")
    return placeholder


def normalized_pth_digest_line(
    pth_path: Path, store: Path | None, checkout_root: Path | None
) -> bytes:
    """The digest input for one `.pth` file: the setuptools namespace-
    package shim's own bytes verbatim (already checkout/store-independent
    -- see `_NAMESPACE_PACKAGE_PTH`), else every line normalized via
    `_normalized_line_value`, joined -- never an ordinary `.pth`'s raw
    bytes (those embed the checkout-dependent absolute path the
    distribution-set digest must stay independent of)."""
    text = pth_path.read_text(encoding="utf-8", errors="surrogateescape")
    if _NAMESPACE_PACKAGE_PTH.match(text.strip()):
        return text.encode("utf-8", "surrogateescape")
    normalized = "\n".join(
        _normalized_line_value(line, store, checkout_root) for line in text.splitlines()
    )
    return normalized.encode("utf-8", "surrogateescape")
