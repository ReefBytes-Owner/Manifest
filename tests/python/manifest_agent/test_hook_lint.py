"""`hook.shellcheck`/`hook.yamllint` must never forward a non-shell/non-YAML
path to their engine.

Regression: before this module existed, both hooks were direct
`shellcheck`/`yamllint` invocations with an empty registry `types_or`
(frozen by config/check-preservation.json to the historically observed
`.pre-commit-config.yaml`, which relies on the wrapper's own implicit type
default). `manifest check` forwarded every "changed" path unfiltered,
so a plain non-shell, non-YAML file (`.gitleaks.toml`) reached `shellcheck`
directly and FAILed trying to parse it as shell.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.project_checks import hook_lint


def _git_init(root: Path) -> None:
    subprocess.run(
        ["git", "init", "-q"],
        cwd=root,
        check=True,
        env={"PATH": __import__("os").defpath},
    )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git_init(root)
    return root


@pytest.mark.parametrize(
    ("check_id", "rejected", "accepted"),
    [
        (
            "hook.shellcheck",
            ("config/check-preservation.json", ".gitleaks.toml", "docs/README.md"),
            ("scripts/build.sh", "bootstrap.bash"),
        ),
        (
            "hook.yamllint",
            (".gitleaks.toml", "notes.md", "run.sh"),
            ("config/values.yaml", "settings.yml"),
        ),
    ],
)
def test_selector_rejects_off_type_paths_and_keeps_matching_ones(
    check_id, rejected, accepted
):
    shape, _, _ = hook_lint._TYPE_FILTERS[check_id]
    for name in rejected:
        assert not shape.search(name), name
    for name in accepted:
        assert shape.search(name), name


def test_select_drops_non_matching_changed_paths(repo):
    (repo / "notes.toml").write_text("[extend]\nuseDefault = true\n")
    (repo / "build.sh").write_text("#!/usr/bin/env bash\necho hi\n")
    selected, blocked = hook_lint._select(
        repo, "hook.shellcheck", ["notes.toml", "build.sh"]
    )
    assert [path.name for path in selected] == ["build.sh"]
    assert blocked == []


def test_select_drops_deliberately_invalid_equivalence_fixtures(repo):
    # tests/fixtures/equivalence/shellcheck/invalid.sh (C2) exists to fail
    # shellcheck under direct invocation -- it must never surface as a
    # finding in this repo's own real changed-file sweep.
    fixture_dir = repo / "tests/fixtures/equivalence/shellcheck"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "invalid.sh").write_text("#!/usr/bin/env bash\nunused=1\n")
    (repo / "build.sh").write_text("#!/usr/bin/env bash\necho hi\n")
    selected, blocked = hook_lint._select(
        repo,
        "hook.shellcheck",
        ["tests/fixtures/equivalence/shellcheck/invalid.sh", "build.sh"],
    )
    assert [path.name for path in selected] == ["build.sh"]
    assert blocked == []


def test_run_returns_pass_when_selection_is_empty_never_invokes_engine(repo):
    # A "changed" set that is entirely non-shell (e.g. this repo's own
    # .gitleaks.toml edit) must PASS -- there is nothing for shellcheck to
    # look at, not a FAIL from feeding it the wrong file.
    assert hook_lint._run("hook.shellcheck", repo, []) == hook_lint.PASS


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not on PATH")
def test_main_end_to_end_ignores_a_toml_file_mixed_with_a_clean_shell_file(repo):
    (repo / ".gitleaks.toml").write_text("[extend]\nuseDefault = true\n")
    (repo / "build.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\necho hi\n")
    status = hook_lint.main(
        ["hook.shellcheck", "--root", str(repo), "--", ".gitleaks.toml", "build.sh"]
    )
    assert status == hook_lint.PASS


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not on PATH")
def test_main_end_to_end_still_fails_on_a_real_shell_violation(repo):
    (repo / ".gitleaks.toml").write_text("[extend]\nuseDefault = true\n")
    (repo / "build.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nunused_var=1\necho hi\n"
    )
    status = hook_lint.main(
        ["hook.shellcheck", "--root", str(repo), "--", ".gitleaks.toml", "build.sh"]
    )
    assert status == hook_lint.FAIL


def test_yamllint_selection_alone_ignores_a_toml_file_mixed_with_yaml(repo):
    # Mirrors the two shellcheck end-to-end cases above without depending on
    # a working local `yamllint` install (environment-fragile: this host's
    # system yamllint script is unrelated to the PyYAML the running test
    # interpreter has) -- `_select` is the exact code path that produced the
    # regression, so proving it alone is sufficient.
    (repo / ".gitleaks.toml").write_text("[extend]\nuseDefault = true\n")
    (repo / "config.yaml").write_text("---\nname: example\n")
    selected, blocked = hook_lint._select(
        repo, "hook.yamllint", [".gitleaks.toml", "config.yaml"]
    )
    assert [path.name for path in selected] == ["config.yaml"]
    assert blocked == []
