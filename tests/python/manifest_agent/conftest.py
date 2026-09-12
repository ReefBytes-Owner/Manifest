"""Session-scoped HOME isolation for the coordinator suite.

Several tests in this package shell out to real harness binaries (`cursor`,
`codex`, `gemini`). Those binaries write config, logs and conversation history
into ``$HOME``, so running the suite against a developer's real home mutated it:
a measured run touched ``~/.cursor/cli-config.json``, ``~/.codex/logs_2.sqlite``
and ``~/.gemini/antigravity-cli/history.jsonl``, and created 480 entries in a
scratch home.

The fixture is session-scoped rather than per-test on purpose. Most of those 480
entries are caches; rebuilding them once per test would cost far more than it
buys, and the suite was measured to behave identically against an empty home, so
one shared empty home per session is sufficient isolation.

Tests that need their own home still set it with the function-scoped
``monkeypatch`` fixture, which takes precedence over this one.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Point HOME at a scratch directory for the whole session."""
    home = tmp_path_factory.mktemp("session-home")
    patch = pytest.MonkeyPatch()
    # Build caches are shared by design and are not the pollution that matters;
    # redirecting them made a measured full run go from 212s to 396s because
    # every wheel was rebuilt. Pin them back to their real locations, resolved
    # before HOME moves, so only configuration and state are isolated.
    # Fall back to the scratch home when the real one is unusable. Pinning the
    # caches to $HOME/.cache is only a speed optimisation, so it must never be
    # the reason a run fails: with HOME unset or pointing somewhere absent, the
    # pin resolved to a path `uv run` could not create ("Read-only file system"),
    # which broke three subprocess-spawning tests that otherwise pass.
    real_home = Path.home()
    shared_caches = real_home.is_dir()
    cache_root = real_home / ".cache" if shared_caches else home / ".cache"
    # Record the decision so a test can assert the sharing contract without
    # having to reconstruct the pre-patch HOME, which it can no longer see.
    patch.setenv("MANIFEST_TEST_CACHES_SHARED", "1" if shared_caches else "0")
    # `UV_PYTHON_INSTALL_DIR` (uv's own downloaded-interpreter cache, under
    # `.local/share`, not `.cache`) needs the same shared-by-design pin: a
    # nested `uv sync`/`uv python` call that only sees this scratch HOME has
    # no cached interpreter to reuse, so it downloads a fresh, unpinned
    # default build (network-dependent, and not the exact interpreter
    # `manifest provision`'s committed lock hash expects) even when this
    # very host already has the right one cached under the real HOME.
    share_root = real_home / ".local/share" if shared_caches else home / ".local/share"
    cache_defaults = {
        "XDG_CACHE_HOME": cache_root,
        "UV_CACHE_DIR": cache_root / "uv",
        "PIP_CACHE_DIR": cache_root / "pip",
        "UV_PYTHON_INSTALL_DIR": share_root / "uv" / "python",
    }
    for name, default in cache_defaults.items():
        patch.setenv(name, os.environ.get(name) or str(default))
    patch.setenv("HOME", str(home))
    # Windows and some libraries consult USERPROFILE instead of HOME.
    patch.setenv("USERPROFILE", str(home))
    try:
        yield home
    finally:
        patch.undo()
