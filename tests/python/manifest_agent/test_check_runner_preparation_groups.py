"""C7j step 2 (phase-3-5-decisions.md Correction 8 rule 2): preparations must
be provably applied.

Root cause of "`.apm/skills` src=122 mirror=0" for `test.bundle-partition`:
`prepare.skill-mirror`'s `groups` in `config/project-checks.json` was
`["structure", "lint"]` -- it never included `"test"`, so `run_profile`'s
`_prepare_for_checks` (which selects preparations by
`selected_groups.intersection(preparation.groups)`) never ran it for a
`--group test` invocation. The mirror was silently absent for every check in
that group, not merely stale. Fixed by adding `"test"` to the declared
groups.

Two things had to be proven, not just fixed:

1. `TestFailedPreparationBlocksEveryDependentCheck` -- through the real
   runner (`run_profile`, no registry mocking of the block path itself),
   a preparation that exits non-zero BLOCKs every check that shares its
   group, and the check's own diagnostics carry the preparation's stderr --
   never a silent FAIL on empty/stale output. This path already existed in
   `runner._run_check` (`check.group in context.failed_preparations`); this
   test pins it so the group-membership fix above cannot regress it.
2. `TestRealSkillMirrorPreparation` -- the REAL `prepare.skill-mirror`
   preparation from the committed registry (its actual argv, tool entry,
   version probe script and inputs/outputs), run against a disposable
   tmp_path fixture repository carrying its own minimal `plugins/*/skills/*`
   source and a copy of the real mirror script/version-probe script, must
   leave `.apm/skills` populated. This is the exact selection the ledger's
   "src=122 mirror=0" finding was made under; before this file's registry
   fix, `prepare.skill-mirror` never ran here because its `groups` excluded
   `"test"`. The candidate is materialized from a git repo built entirely
   under `tmp_path` -- never from `REPO_ROOT` / this running tree -- per
   phase-3-5-decisions.md Correction 9 rule 3: no test may materialize its
   own tree, since that write lands inside the candidate under test when
   this file itself is executed as `test.python` through `manifest check`.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from manifest_agent.checks import materialize_candidate, run_profile
from manifest_agent.checks.cli import _execution_environment
from manifest_agent.checks.models import CheckSpec, PreparationSpec
from manifest_agent.checks.registry import load_registry
from tests.python.manifest_agent.test_check_candidate import git, materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture

REPO_ROOT = Path(__file__).resolve().parents[3]


def _failing_preparation() -> PreparationSpec:
    return PreparationSpec(
        id="prepare.fails",
        argv=(sys.executable, "fail.py"),
        cwd=".",
        inputs=("fail.py",),
        outputs=(".apm/generated",),
        groups=("test",),
        timeout_seconds=2.0,
        tool="python",
        version="1.0.0",
    )


def _dependent_check() -> CheckSpec:
    return CheckSpec(
        id="check.needs-mirror",
        category="test",
        group="test",
        argv=(sys.executable, "-c", "pass"),
        cwd=".",
        inputs=(".",),
        dependencies=(),
        timeout_seconds=2.0,
        selection="project",
        tool="python",
        version="1.0.0",
    )


class TestFailedPreparationBlocksEveryDependentCheck:
    def test_check_is_blocked_with_the_preparations_own_stderr(self, source, tmp_path):
        """A preparation that exits 1 and writes to stderr must BLOCK every
        check sharing its group, and the check's diagnostics must contain
        the preparation's own stderr text -- never a silent FAIL on
        whatever partial/empty output the failed preparation left behind."""
        root = source[0]
        (root / "fail.py").write_text(
            "import sys\n"
            "sys.stderr.write('distinctive-preparation-failure-marker\\n')\n"
            "raise SystemExit(1)\n"
        )
        candidate = materialize(source, tmp_path)
        tool = {
            "python": {
                "executable": sys.executable,
                "version_argv": (sys.executable, "--version"),
                "expected_version": _python_version(),
                "required_modules": (),
            }
        }
        preparation = _failing_preparation()
        check = _dependent_check()
        registry = {
            "schema_version": 1,
            "tools": tool,
            "checks": (check,),
            "candidate_preparations": (preparation,),
            "profiles": {"full": (check.id,)},
            "coverage_pending": {"full": ()},
        }
        report = run_profile(registry, "full", None, candidate, {"PATH": "bin"})
        assert report["status"] == "BLOCKED"
        result = next(r for r in report["results"] if r["id"] == check.id)
        assert result["status"] == "BLOCKED"
        assert "distinctive-preparation-failure-marker" in result["diagnostics"]


def _python_version() -> str:
    import platform

    return platform.python_version()


_MIRROR_SCRIPT_RELATIVE = Path("configs/claude/scripts/generate_skill_mirror.sh")
_VERSION_PROBE_RELATIVE = Path("tools/project_checks/tool_versions.py")
_SKILL_POLICIES_RELATIVE = Path("configs/claude/config/skill_policies.yml")


def _skill_mirror_fixture_repo(tmp_path: Path) -> tuple[Path, str]:
    """A disposable git repo, built entirely under `tmp_path`, carrying only
    what `prepare.skill-mirror`'s real argv/version-probe need to run for
    real: a copy of the two real scripts (read from this checkout's own
    tree -- content only, never the candidate root itself) plus one minimal
    `plugins/*/skills/*/SKILL.md` source tree for the script to mirror.
    """
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    (root / ".gitignore").write_text(".apm/\n")

    mirror_script = root / _MIRROR_SCRIPT_RELATIVE
    mirror_script.parent.mkdir(parents=True, exist_ok=True)
    mirror_script.write_text((REPO_ROOT / _MIRROR_SCRIPT_RELATIVE).read_text())
    mirror_script.chmod(0o755)

    version_probe = root / _VERSION_PROBE_RELATIVE
    version_probe.parent.mkdir(parents=True, exist_ok=True)
    version_probe.write_text((REPO_ROOT / _VERSION_PROBE_RELATIVE).read_text())

    skill_policies = root / _SKILL_POLICIES_RELATIVE
    skill_policies.parent.mkdir(parents=True, exist_ok=True)
    skill_policies.write_text("bundles:\n  demo-bundle:\n    skills: []\n")

    skill_dir = root / "plugins" / "demo-bundle" / "skills" / "hello-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: hello-skill\ndescription: fixture skill for C7k3\n---\n"
        "Fixture skill body.\n"
    )

    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD").decode().strip()
    return root, base


def _skill_mirror_probe_registry() -> tuple[dict, CheckSpec]:
    """The real `prepare.skill-mirror` preparation + tool entry, pulled
    straight out of `config/project-checks.json`, wired to one trivial
    `group="test"` probe check -- not the whole `test` group, which includes
    the store-gated, multi-minute `test.bats`/`test.python` bodies."""
    real_registry = load_registry(REPO_ROOT / "config" / "project-checks.json")
    skill_mirror = next(
        p
        for p in real_registry["candidate_preparations"]
        if p.id == "prepare.skill-mirror"
    )
    assert "test" in skill_mirror.groups, (
        "prepare.skill-mirror must declare the test group -- "
        "see config/project-checks.json"
    )
    probe = replace(
        _dependent_check(),
        id="check.mirror-probe",
        group="test",
        tool="probe-python",
        version=_python_version(),
    )
    registry = {
        "schema_version": real_registry["schema_version"],
        "tools": {
            "probe-python": {
                "executable": sys.executable,
                "version_argv": (sys.executable, "--version"),
                "expected_version": _python_version(),
                "required_modules": (),
            },
            skill_mirror.tool: real_registry["tools"][skill_mirror.tool],
        },
        "checks": (probe,),
        "candidate_preparations": (skill_mirror,),
        "profiles": {"full": (probe.id,)},
        "coverage_pending": {"full": ()},
    }
    return registry, probe


class TestRealSkillMirrorPreparation:
    def test_real_skill_mirror_populates_apm_skills_for_the_test_group(self, tmp_path):
        """The real `prepare.skill-mirror` preparation, run against a
        candidate materialized from a disposable `tmp_path` fixture repo
        (never this running tree, per phase-3-5-decisions.md Correction 9
        rule 3) through `run_profile` with `group="test"` selected, must
        leave `.apm/skills` populated. This is the exact selection the
        ledger's "src=122 mirror=0" finding was made under; before this
        file's registry fix, `prepare.skill-mirror` never ran here because
        its `groups` excluded `"test"`.
        """
        registry, probe = _skill_mirror_probe_registry()
        fixture_root, base = _skill_mirror_fixture_repo(tmp_path)
        destination = tmp_path / "candidate"
        candidate = materialize_candidate(fixture_root, base, destination)
        report = run_profile(
            registry, "full", "test", candidate, _execution_environment()
        )
        result = next(r for r in report["results"] if r["id"] == probe.id)
        assert result["status"] == "PASS", result
        mirror = candidate.root / ".apm" / "skills"
        entries = [p for p in mirror.iterdir() if p.is_dir()] if mirror.is_dir() else []
        assert len(entries) > 0, "prepare.skill-mirror did not populate .apm/skills"
