#!/usr/bin/env python3
"""Which interpreter delegate.py re-execs, and which policy copies it trusts.

Every test here drives the executable trust gate through a real subprocess: the
gate runs at import time, so an in-process call would skip it entirely.
"""

import os
import shutil
import subprocess
from pathlib import Path

from _delegate_runtime_env import (
    _copied_plugin,
    _deployed_home,
    _policyless_launcher,
    _recorder_runtime_home,
    _run_guard,
    _site_packages,
    _trusted_policy_venv,
    _uv_install,
)


def test_reexec_targets_the_symlink_not_its_resolved_interpreter(
    tmp_path: Path,
) -> None:
    home, symlink, recorder = _recorder_runtime_home(tmp_path)

    result = _run_guard(_policyless_launcher(tmp_path), home, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(symlink)
    assert result.stdout.strip() != str(recorder)


def test_reexec_reaches_policy_inside_a_symlinked_venv(tmp_path: Path) -> None:
    trusted = _trusted_policy_venv(tmp_path)
    assert (trusted / "bin/python").is_symlink(), "venv did not symlink its interpreter"
    home = tmp_path / "venv-home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude/.venv").symlink_to(trusted, target_is_directory=True)

    result = _run_guard(_policyless_launcher(tmp_path), home, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Delegate tasks/reviews" in result.stdout
    assert "manifest-model-policy" not in result.stderr


def test_runtime_override_accepts_an_equivalent_spelling(tmp_path: Path) -> None:
    home, symlink, _ = _recorder_runtime_home(tmp_path)
    equivalent = symlink.parent / ".." / "bin" / symlink.name

    result = _run_guard(
        _policyless_launcher(tmp_path),
        home,
        tmp_path,
        MANIFEST_RUNTIME_PYTHON=str(equivalent),
    )

    assert result.returncode == 0, result.stderr
    assert "rejected untrusted MANIFEST_RUNTIME_PYTHON" not in result.stderr
    assert result.stdout.strip() == str(symlink)


def test_deployed_home_editable_policy_is_trusted(tmp_path: Path) -> None:
    """The plugin-cache copy has no repo anchor, so only the home path can vouch."""
    plugin = _copied_plugin(tmp_path)
    home = _deployed_home(tmp_path)

    result = _run_guard(
        _policyless_launcher(tmp_path),
        home,
        tmp_path,
        script=plugin / "scripts/delegate.py",
    )

    assert result.returncode == 0, result.stderr
    assert "Delegate tasks/reviews" in result.stdout
    assert "manifest-model-policy" not in result.stderr


def test_editable_policy_outside_the_trusted_home_is_rejected(tmp_path: Path) -> None:
    """Same editable shape, planted outside ~/.claude/scripts, stays untrusted."""
    plugin = _copied_plugin(tmp_path)
    home = _deployed_home(tmp_path)
    attacker = tmp_path / "attacker-scripts"
    attacker.mkdir()
    shutil.copytree(
        home / ".claude/scripts/manifest_model_policy",
        attacker / "manifest_model_policy",
    )
    _uv_install(
        home / ".claude/.venv/bin/python",
        "--reinstall",
        "--editable",
        str(attacker / "manifest_model_policy"),
    )

    result = _run_guard(
        _policyless_launcher(tmp_path),
        home,
        tmp_path,
        script=plugin / "scripts/delegate.py",
    )

    assert result.returncode == 2
    assert "invalid manifest-model-policy distribution" in result.stderr
    assert "Delegate tasks/reviews" not in result.stdout


def _resolved_import_origin(python: Path) -> subprocess.CompletedProcess[str]:
    """Where `python` would actually import `manifest_model_policy` from.

    `PYTHONPATH` dropped: `test.python`'s own honest env (Correction 9) points
    it at the CANDIDATE's own manifest_model_policy so in-process checks
    import the code under test -- inherited unchanged here, it would shadow
    the throwaway venv's own site-packages ahead of every `.pth` a test just
    wrote, making a "did it import from the trusted tree" precondition pass
    for the wrong reason regardless of which distribution the venv trusts.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.util, pathlib; "
            "print(pathlib.Path("
            "importlib.util.find_spec('manifest_model_policy').origin).resolve())",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_editable_metadata_must_name_the_path_it_imports_from(tmp_path: Path) -> None:
    """A trusted import path cannot launder metadata that points somewhere else.

    The attacker owns the distribution record (editable, from their own tree)
    while an unrelated legitimate `.pth` still puts `~/.claude/scripts` first on
    sys.path, so the module imports from the trusted location. Only comparing
    `direct_url` against that same location rejects this.
    """
    plugin = _copied_plugin(tmp_path)
    home = _deployed_home(tmp_path)
    attacker = tmp_path / "attacker-tree"
    attacker.mkdir()
    shutil.copytree(
        home / ".claude/scripts/manifest_model_policy",
        attacker / "manifest_model_policy",
    )
    python = home / ".claude/.venv/bin/python"
    _uv_install(
        python, "--reinstall", "--editable", str(attacker / "manifest_model_policy")
    )
    # Sorts before the attacker's `_manifest_model_policy.pth`, so the trusted
    # directory reaches sys.path first and wins the import.
    (_site_packages(python) / "_a_trusted_scripts.pth").write_text(
        f"{home / '.claude/scripts'}\n", encoding="utf-8"
    )
    origin = _resolved_import_origin(python)
    assert origin.returncode == 0, origin.stderr
    assert Path(origin.stdout.strip()).is_relative_to(home), (
        "precondition failed: the import did not resolve to the trusted tree, "
        "so this test would pass for the wrong reason"
    )

    result = _run_guard(
        _policyless_launcher(tmp_path),
        home,
        tmp_path,
        script=plugin / "scripts/delegate.py",
    )

    assert result.returncode == 2
    assert "invalid manifest-model-policy distribution" in result.stderr
    assert "Delegate tasks/reviews" not in result.stdout


def test_reexec_that_still_lacks_policy_names_the_runtime_not_tampering(
    tmp_path: Path,
) -> None:
    """The second pass reports a runtime gap, not a shadowed distribution.

    Reusing the tamper wording here sent diagnosis hunting a planted copy that
    was never there, so the two outcomes must stay distinguishable.
    """
    home = tmp_path / "empty-home"
    home.mkdir()

    result = _run_guard(
        _policyless_launcher(tmp_path),
        home,
        tmp_path,
        MANIFEST_DELEGATE_RUNTIME_REEXEC="1",
    )

    assert result.returncode == 2
    assert "did not provide manifest-model-policy" in result.stderr
    assert "invalid manifest-model-policy distribution" not in result.stderr
    assert "Delegate tasks/reviews" not in result.stdout


def test_non_object_editable_metadata_is_untrusted_not_absent(tmp_path: Path) -> None:
    """Unreadable editable metadata must stay a hard failure, not a missing one.

    `direct_url.json` holding valid JSON that is not an object makes the field
    lookups raise. If that escapes to the distribution-level handler the gate
    reports the policy as merely absent and `--help` answers normally, turning a
    tamper signal into a benign one.
    """
    plugin = _copied_plugin(tmp_path)
    home = _deployed_home(tmp_path)
    python = tmp_path / "deployed-venv/bin/python"
    metadata = (
        _site_packages(python) / "manifest_model_policy-0.1.0.dist-info/direct_url.json"
    )
    metadata.write_text("[1, 2]", encoding="utf-8")
    # Without a home runtime to re-exec into, the verdict is decided in this
    # process, so the exit distinguishes "untrusted" from "absent" directly.
    (home / ".claude/.venv").unlink()

    result = _run_guard(python, home, tmp_path, script=plugin / "scripts/delegate.py")

    assert result.returncode == 2
    assert "invalid manifest-model-policy distribution" in result.stderr
    assert "Delegate tasks/reviews" not in result.stdout
