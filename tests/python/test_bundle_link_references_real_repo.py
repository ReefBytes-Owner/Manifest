"""Real-repo regression for check_bundle_link_references.py: the known true
positives stay caught, and the documented non-defects stay unflagged, when
the checker runs against this actual repository rather than a synthetic
fixture tree.

Split out of test_bundle_link_references.py (synthetic-fixture cases) to
keep both files under the C-SIZE/CON-002 file-line ceiling; see
_bundle_link_references_harness.py.
"""

from __future__ import annotations

from tests.python._bundle_link_references_harness import real_repo_violation_tuples


def test_real_repo_catches_sub_agent_dispatch_true_positives() -> None:
    # 25 skills / 6 bundles per the spec's blunt string count cite
    # sub-agent-dispatch.md; 4 of those 25 (manifest-spec-planning's own
    # skills) resolve correctly via a relative path and must NOT appear here
    # (see test_real_repo_does_not_flag_the_documented_non_defects).
    found = real_repo_violation_tuples()
    assert (
        "plugins/manifest-code-quality/skills/ai-code-audit/SKILL.md",
        "missing-bundled-reference",
        "sub-agent-dispatch.md",
    ) in found
    assert (
        "plugins/stitch-design/skills/ux-review/SKILL.md",
        "missing-bundled-reference",
        "sub-agent-dispatch.md",
    ) in found


def test_real_repo_catches_bare_command_config_yml_true_positives() -> None:
    # code-audit now owns its dispatch reference.
    found = real_repo_violation_tuples()
    assert (
        "plugins/manifest-forge/skills/pr-review/SKILL.md",
        "missing-bundled-reference",
        "command_config.yml",
    ) in found
    assert (
        "plugins/stitch-design/skills/a11y-audit/SKILL.md",
        "missing-bundled-reference",
        "command_config.yml",
    ) in found


def test_real_repo_catches_manifest_delegate_harness_routing_true_positive() -> None:
    found = real_repo_violation_tuples()
    assert (
        "plugins/manifest-delegate/skills/delegate/SKILL.md",
        "cross-bundle-path",
        "configs/claude/references/harness-routing.md",
    ) in found


def test_real_repo_catches_token_benchmark_true_positives() -> None:
    found = real_repo_violation_tuples()
    assert (
        "plugins/manifest-workspace/skills/token-benchmark/SKILL.md",
        "cross-bundle-path",
        "tests/token_benchmark/harness.py",
    ) in found
    assert (
        "plugins/manifest-workspace/skills/token-benchmark/SKILL.md",
        "cross-bundle-path",
        "docs/TOKEN_BENCHMARK.md",
    ) in found


def test_real_repo_does_not_flag_the_documented_non_defects() -> None:
    found = real_repo_violation_tuples()
    flagged_paths = {path for path, _kind, _value in found}

    # manifest-spec-planning owns sub-agent-dispatch.md; its own skills cite
    # it via a relative path that resolves inside the same bundle.
    for skill in (
        "design-validate",
        "plan-manage",
        "spec-audit-tasks",
        "spec-implement-loop",
    ):
        assert (
            f"plugins/manifest-spec-planning/skills/{skill}/SKILL.md"
            not in flagged_paths
        )

    # manifest-security/skills/code-audit resolves its ../../runtime/
    # references/{code-constitution,antipatterns}.md citations correctly --
    # neither may appear as a violation value for this file. Its dispatch
    # reference is now skill-local, so this file is expected to remain clean.
    for value in ("code-constitution.md", "antipatterns.md"):
        assert ("plugins/manifest-security/skills/code-audit/SKILL.md", value) not in {
            (path, val) for path, _kind, val in found
        }


def test_real_repo_accepts_pr883_skill_local_dispatch_links() -> None:
    found = real_repo_violation_tuples()
    flagged_paths = {path for path, _kind, _value in found}

    assert not flagged_paths & {
        "plugins/manifest-code-quality/skills/refactor/SKILL.md",
        "plugins/manifest-security/skills/code-audit/SKILL.md",
        "plugins/manifest-security/skills/ci-audit-triggers/SKILL.md",
        "plugins/manifest-security/skills/security-refute-findings/SKILL.md",
        "plugins/manifest-security/skills/security-triage-findings/SKILL.md",
    }


def test_real_repo_excludes_generated_data_files_from_scanning() -> None:
    """Neither ``_GENERATED_DATA_FILES`` entry's path-shaped JSON values may
    appear as a violation's own path -- they are ratchet/catalog data, not
    this file's own citation."""
    flagged_paths = {path for path, _kind, _value in real_repo_violation_tuples()}
    assert not flagged_paths & {
        "plugins/manifest-code-quality/skills/code-audit-constitution/config/"
        "constitution_baseline.json",
        "plugins/manifest-workspace/skills/help/catalog/commands.json",
    }


def test_real_repo_does_not_truncate_stitch_design_template_citations() -> None:
    """screen-prompts/spec-amend cite a real ``*.md.template`` file; neither
    may be flagged under the truncated ``*.md`` value a regex bug produced."""
    found = real_repo_violation_tuples()
    assert not {
        v
        for v in found
        if v[1] == "missing-bundle-local-target" and v[2].endswith(".md")
    }
