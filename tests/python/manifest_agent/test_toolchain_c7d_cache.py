"""C7d: every body/probe child env has its caches redirected outside the
candidate (Correction 4, phase-3-5-decisions.md).

Evidence this closes (phases-3-5-ledger.md, C7d findings): a provisioned-store
profile run showed the overwhelming majority of checks BLOCKED "candidate
identity changed" because CPython wrote `__pycache__` into the candidate for
every body that imported a module from it. Proven here with real subprocesses
and a real disposable candidate, not just asserted about the resolver.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from manifest_agent.checks import run_profile, toolchain
from manifest_agent.checks.models import CheckSpec
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture


@pytest.fixture
def candidate(source, tmp_path):
    (source[0] / "noop.py").write_text("VALUE = 1\n")
    return materialize(source, tmp_path)


def _demo_tool(*, version_argv=None, expected_version: str = "1.0.0") -> dict:
    return {
        "executable": "python3",
        "version_argv": version_argv or ("python3", "-c", "print('demo 1.0.0')"),
        "expected_version": expected_version,
        "required_modules": (),
    }


def _demo_check(
    argv: tuple[str, ...],
    *,
    check_id: str = "check.demo",
    inputs: tuple[str, ...] = ("noop.py",),
) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        category="test",
        group="test",
        argv=argv,
        cwd=".",
        inputs=inputs,
        dependencies=(),
        timeout_seconds=10.0,
        selection="project",
        tool="demo",
        version="1.0.0",
    )


def _registry(check: CheckSpec, tool: dict) -> dict:
    return {
        "schema_version": 1,
        "toolchain_lock_document": {},
        "tools": {"demo": tool},
        "checks": (check,),
        "candidate_preparations": (),
        "profiles": dict.fromkeys(
            ("quick", "full", "security", "release"), (check.id,)
        ),
        "coverage_pending": dict.fromkeys(("quick", "full", "security", "release"), ()),
    }


@contextmanager
def _spying_cache_directory(captured: list):
    """Wraps the real `run_cache_directory` and records the path it yields,
    so a test can assert it was removed after the `with` block exits --
    including via an exception."""
    original = toolchain.toolchain_cache.run_cache_directory
    with original() as run_tmp:
        captured.append(run_tmp)
        yield run_tmp


class TestCachesRedirectedOutsideTheCandidate:
    """Deliverable 2: every cache a body writes must land in run-tmp."""

    def test_a_body_importing_a_candidate_module_leaves_no_pycache_behind(
        self, source, tmp_path
    ):
        (source[0] / "noop.py").write_text("VALUE = 1\n")
        (source[0] / "importee.py").write_text("VALUE = 42\n")
        candidate = materialize(source, tmp_path)
        tool = _demo_tool()
        check = _demo_check(
            (
                "python3",
                "-c",
                "import sys; sys.path.insert(0, '.'); "
                "import importee; print(importee.VALUE)",
            ),
            check_id="check.importer",
            inputs=("noop.py", "importee.py"),
        )
        registry = _registry(check, tool)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
        report = run_profile(registry, "full", None, candidate, env)
        assert report["status"] == "PASS", report["results"]
        assert "42" in report["results"][0]["diagnostics"]
        assert list(candidate.root.rglob("__pycache__")) == []

    def test_a_body_that_still_writes_into_the_candidate_blocks_identity(
        self, candidate, tmp_path
    ):
        """The identity check stays strict: a body that writes a real file
        into the candidate (not a cache artifact the runner redirects) must
        still BLOCK -- this is a real defect of that check, and nothing in
        this chunk may exclude it from the digest."""
        tool = _demo_tool()
        check = _demo_check(
            ("python3", "-c", "open('mutation.txt', 'w').write('unexpected')"),
            check_id="check.writer",
        )
        registry = _registry(check, tool)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
        report = run_profile(registry, "full", None, candidate, env)
        assert report["status"] == "BLOCKED"
        assert "candidate identity changed" in report["results"][0]["diagnostics"]

    def test_run_tmp_is_removed_after_a_normal_run(self, candidate, tmp_path):
        captured: list = []
        tool = _demo_tool()
        check = _demo_check(("python3", "-c", "pass"))
        registry = _registry(check, tool)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
        toolchain.run_cache_directory = lambda: _spying_cache_directory(captured)
        try:
            report = run_profile(registry, "full", None, candidate, env)
        finally:
            toolchain.run_cache_directory = (
                toolchain.toolchain_cache.run_cache_directory
            )
        assert report["status"] == "PASS", report["results"]
        assert len(captured) == 1
        assert not captured[0].exists()

    def test_run_tmp_is_removed_even_when_a_check_raises(self, candidate, tmp_path):
        captured: list = []

        def _boom(*_args, **_kwargs):
            raise RuntimeError("synthetic failure inside the run")

        import manifest_agent.checks.runner as runner_module

        original_prepare = runner_module._prepare_for_checks
        tool = _demo_tool()
        check = _demo_check(("python3", "-c", "pass"))
        registry = _registry(check, tool)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
        toolchain.run_cache_directory = lambda: _spying_cache_directory(captured)
        runner_module._prepare_for_checks = _boom
        try:
            with pytest.raises(RuntimeError, match="synthetic failure"):
                run_profile(registry, "full", None, candidate, env)
        finally:
            toolchain.run_cache_directory = (
                toolchain.toolchain_cache.run_cache_directory
            )
            runner_module._prepare_for_checks = original_prepare
        assert len(captured) == 1
        assert not captured[0].exists()


class TestCacheEnvironmentOverridesCallerValues:
    """The runner's cache-env values always win; the caller's are ignored."""

    def test_caller_env_values_are_overridden_not_honoured(self, tmp_path):
        run_tmp = tmp_path / "run-tmp"
        run_tmp.mkdir()
        caller_env = {
            "PYTHONDONTWRITEBYTECODE": "",
            "XDG_CACHE_HOME": "/inside/the/candidate",
            "PYTEST_ADDOPTS": "-x",
        }
        result = toolchain.cache_environment(caller_env, run_tmp)
        assert result["PYTHONDONTWRITEBYTECODE"] == "1"
        assert result["XDG_CACHE_HOME"] == str(run_tmp / "xdg")
        assert result["PYTEST_ADDOPTS"] == "-x -p no:cacheprovider"
        assert result["RUFF_CACHE_DIR"] == str(run_tmp / "ruff")
        assert result["UV_CACHE_DIR"] == str(run_tmp / "uv")
        assert result["npm_config_cache"] == str(run_tmp / "npm")
        assert result["PYTHONPYCACHEPREFIX"] == str(run_tmp / "pycache")
        assert result["UV_PROJECT_ENVIRONMENT"] == str(run_tmp / "uv-env")
        assert result["UV_NO_SYNC"] == "1"


class TestUvRunGuard:
    """Correction 12 (C7k step 5b) rule 2: an escaped `uv run --project .`
    inside a check body must not be able to create `.venv` inside the
    candidate. Exercised through the real runner with the real `uv` binary,
    not a fake -- the guard is only meaningful if it holds against the tool
    it is meant to contain."""

    def test_uv_run_against_the_candidate_leaves_it_byte_identical(
        self, candidate, tmp_path
    ):
        tool = _demo_tool()
        check = _demo_check(
            ("uv", "run", "--project", ".", "python3", "-c", "pass"),
            check_id="check.uv-escape",
        )
        registry = _registry(check, tool)
        env = {
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
            "HOME": str(tmp_path),
        }

        def _snapshot() -> set:
            # `.git/preparation.lock` is the runner's own bookkeeping
            # (created and removed around every run) -- not something the
            # check body wrote. Everything else under the candidate is fair
            # game, most of all a `.venv` the guard exists to prevent.
            return {
                p.relative_to(candidate.root)
                for p in candidate.root.rglob("*")
                if p.name != "preparation.lock"
            }

        before = _snapshot()
        run_profile(registry, "full", None, candidate, env)
        after = _snapshot()
        # Whatever the check's own exit status, the candidate must be
        # untouched: no `.venv` materialized, no new files at all.
        assert before == after
        assert not (candidate.root / ".venv").exists()
