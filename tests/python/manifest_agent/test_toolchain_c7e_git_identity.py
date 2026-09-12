"""C7e: candidate `.git` identity compares logically, not raw bytes
(Correction 5, phase-3-5-decisions.md).

Evidence this closes (phases-3-5-ledger.md, C7e finding): a provisioned-store
profile run showed test.bats's body running `git status`/`git diff` inside
the candidate, which refreshes `.git/index`'s on-disk stat cache with no
logical change. `runner.execute_check` byte-compared `.git` wholesale, so
that one check reported "candidate identity changed", and because
`_identity_error` re-checks the stored digest for every later check, the
entire 75-check run degraded to BLOCKED. Proven here with real subprocesses,
real git and a real disposable candidate -- not just asserted about the
comparison helpers.
"""

from __future__ import annotations

import subprocess

import pytest

from manifest_agent.checks import candidate_digest, run_profile
from manifest_agent.checks.models import CheckSpec
from tests.python.manifest_agent.test_check_candidate import materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture

_ENV = {"PATH": "/usr/bin:/bin"}


def _demo_tool() -> dict:
    return {
        "executable": "python3",
        "version_argv": ("python3", "-c", "print('demo 1.0.0')"),
        "expected_version": "1.0.0",
        "required_modules": (),
    }


def _demo_check(argv: tuple[str, ...], check_id: str) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        category="test",
        group="test",
        argv=argv,
        cwd=".",
        inputs=("head-only.txt",),
        dependencies=(),
        timeout_seconds=10.0,
        selection="project",
        tool="demo",
        version="1.0.0",
    )


def _registry(checks: tuple[CheckSpec, ...], tool: dict) -> dict:
    ids = tuple(check.id for check in checks)
    return {
        "schema_version": 1,
        "toolchain_lock_document": {},
        "tools": {"demo": tool},
        "checks": checks,
        "candidate_preparations": (),
        "profiles": dict.fromkeys(("quick", "full", "security", "release"), ids),
        "coverage_pending": dict.fromkeys(("quick", "full", "security", "release"), ()),
    }


@pytest.fixture
def candidate(source, tmp_path):
    return materialize(source, tmp_path)


def _one_check_report(candidate, argv, check_id="check.body"):
    check = _demo_check(argv, check_id)
    registry = _registry((check,), _demo_tool())
    return run_profile(registry, "full", None, candidate, dict(_ENV))


class TestGitStatusAndDiffDoNotChangeIdentity:
    def test_git_status_and_diff_in_the_candidate_is_not_identity_changed(
        self, candidate
    ):
        report = _one_check_report(
            candidate,
            (
                "bash",
                "-c",
                "git status >/dev/null && git diff >/dev/null",
            ),
        )
        assert report["status"] == "PASS", report["results"]
        assert "candidate identity changed" not in report["results"][0]["diagnostics"]

    def test_a_later_check_in_the_same_run_is_unaffected(self, candidate):
        """This is the storm from the ledger: one git-status body must not
        BLOCK every check that runs after it in the same profile."""
        git_status_check = _demo_check(
            ("bash", "-c", "git status >/dev/null && git diff >/dev/null"),
            "check.git-status",
        )
        trivial_check = _demo_check(("python3", "-c", "pass"), "check.trivial")
        registry = _registry((git_status_check, trivial_check), _demo_tool())
        report = run_profile(registry, "full", None, candidate, dict(_ENV))
        assert report["status"] == "PASS", report["results"]
        by_id = {result["id"]: result for result in report["results"]}
        assert by_id["check.git-status"]["status"] == "PASS"
        assert by_id["check.trivial"]["status"] == "PASS"
        assert "candidate identity changed" not in by_id["check.trivial"]["diagnostics"]


class TestRealGitMutationsStillBlock:
    def test_git_add_of_a_new_path_blocks_identity(self, candidate):
        report = _one_check_report(
            candidate,
            ("bash", "-c", "echo new > brand-new.txt && git add brand-new.txt"),
            check_id="check.git-add",
        )
        assert report["status"] == "BLOCKED"
        assert "candidate identity changed" in report["results"][0]["diagnostics"]

    def test_git_rm_cached_of_a_tracked_path_blocks_identity(self, candidate):
        report = _one_check_report(
            candidate,
            ("git", "rm", "--cached", "--quiet", "head-only.txt"),
            check_id="check.git-rm-cached",
        )
        assert report["status"] == "BLOCKED"
        assert "candidate identity changed" in report["results"][0]["diagnostics"]

    def test_modifying_a_tracked_files_bytes_blocks_identity(self, candidate):
        report = _one_check_report(
            candidate,
            (
                "python3",
                "-c",
                "open('head-only.txt', 'w').write('mutated')",
            ),
            check_id="check.mutate-tracked",
        )
        assert report["status"] == "BLOCKED"
        assert "candidate identity changed" in report["results"][0]["diagnostics"]

    def test_writing_any_other_file_under_dot_git_blocks_identity(self, candidate):
        report = _one_check_report(
            candidate,
            ("bash", "-c", "echo intrusion > .git/foo"),
            check_id="check.git-foo",
        )
        assert report["status"] == "BLOCKED"
        assert "candidate identity changed" in report["results"][0]["diagnostics"]


class TestCandidateDigestIsStableAcrossAStatCacheRefresh:
    def test_candidate_digest_unchanged_by_git_status_in_the_candidate(self, candidate):
        before = candidate_digest(candidate.root)
        subprocess.run(
            ["git", "status"],
            cwd=candidate.root,
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "diff"],
            cwd=candidate.root,
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            check=True,
        )
        after = candidate_digest(candidate.root)
        assert after == before
