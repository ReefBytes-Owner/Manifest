# constitution: exempt C-SIZE — brief requires one shared cross-family golden contract suite.
"""Golden behavioral fixtures for the extracted project-check bodies."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import venv
import zipfile
import zlib
from pathlib import Path

import pytest

# Exact source bytes inspected from pre-commit-hooks v6.0.0, commit
# 3e8a8703264a2f4a69428a0aa4dcb512790b2c8c.  Each digest is verified before use.
_PINNED_FIXER_SOURCES = {
    "trailing_whitespace_fixer": (
        "21b314ca2e622ec948a0c5b47b38ba5e1d0682163ba91b2f8b9eebd87cde0747",
        "eNqlV21v2zYQ/q5fcckwUOpsdelHFRowYOm3DcGSAQUcQ6Ctc6xGIlWSjp21/e87kiIlO0lfsACJk3vjvT532SjZQVVtdmansKqg6XqpDHAhpOGmkUInSaCpu54rjeFvqZON1V7LtsW1k835ah1MXOPHHYo1JklS4waqTXOg7xbTBIYv+5fgHRagjZpFcqOrjqv7Wu5FASsp25G13pIDRHw0qOEz/CUFzpIM5r85ucLJ7RuzBdmjSIP9GXSyxpKpFcuAa/du1Su5Rq2xLqL1thFktjzh5wp57Vhp5kQF7oPkIohVlpLaH7Op/zPvcQYbqZx5aIR/ZulMNZvR2lnpOaM/L0ey/0YkLruTJ8MjxxKhBpNY96oxPo4siiqk3hBwo3boaNhqLE657zhRQ6mPknKU3qF2/7fYllSEDFq7i/mbYgllCSt2q24FG/1D2UIkH/kCPuGLYv5mOQQWrV1Ea8/aetHSxfKZFAU1r/QT9Ao1qgcEo3hDindg9nKue75GVzUhxXzVcnE/dCQVMOTI1UuHyCfJo4mtIaWhdTp5o525NMscw9FQ1Nq2VLpiACx7UsKYjFzRPDZ9OvTuL+AU6JMCSU4UnshaGd8GHW9ESqDxUEQsWJDwcqglpcR+uII2wnh3HMAoYgWwyX9Xd7sOhblynGEEvVjO67riA3/sMzafCzkPiZlbL1c0w/dzPBg2thd3kFUybSQhn6HunjC32PZl9OH6n6urvy+vrz3/+1z47vd5TxNeTxiUO75rTblYjrQODX/gqmSvPl++v1nM6Mcsz/Plqcvp0YCzP0N30NMotIVoSKnDXmVgJNhucdWD6CO4ttE56R5bGpwq4Od0+DXTo0j2I5lxrfINx2+2CBppx2wcDFCuUGnrs/fX7R1DMpQ5K+Pm5KnTf3hPnSJvW9hvCd38nI1W8x8IgwUgJv9BEFlTRehXFwJ7F5j2PVp3zBuyYtTRgz33YS1qNxtZEobZknIh40RXsSgVVW+c1l411pOvNDkVdg+1JD+owlvClzM2PNO5WLSfL50//1RwiDGHPF5l8r6PA5WSKn2x0QkjPu4aAjrgDs+w680jhESGzLTtCGAlUConLw5gKaTqeNv8i5Mefktt2jZ0kRhgM0p/K/eo1lzTfrRoRxhKFb6Alha3RVc2lJgMkw23t8d+zC2wHXJnIs3y1sMZkf3SPliP6JH8gyQ0G1zLcvd+SnQfyDIZvFX4gU4hqpFc8VWLcI6cnD6Pp45d2jEOunvu7fCNbvqVv6Dtk4QVbnPpk2J9H8tgO0Y8pmvLJI7VcfKOoFj++raYYvyTyj29BNiK1/BiPaPXJ0PmVT8R+0x9gXRLAebwGm5vocimezLKngOkQ34eY17ewk7jy7BZEt6x7Hx2ZM2n3qHJsEviGHlioz3VbuMJnRbhmq6oYZX4XVZZCln5NeY9VozS6XTj6I9JrWaTtJR0ENN0m61vDmLEsy1013hTdbWdwWn7+1IPtsaCT+s9ntCTe7AON+ZJrR1KbAiTDra3PgWNLyw7kjsO/2K63ics2uj2/cpaoH8S6DJiVWX3e1UN5xFdMpTk60dtsLs80Gy47U8R/wdNfLUr",
    ),
    "end_of_file_fixer": (
        "ab6eef7888529efb9de45b72267077710c79f073e9d83e092a59b6e7a7b8c73c",
        "eNqlVk1v2zgQvetXTJGDpDYRkh4NqMBi6y2KAsmiaU9tIVDSKGYjkV6Simy0/e+dob4sJdk9rA+WTM48vnnzSLoyuoEsq1rXGswykM1eGwdCKe2Ek1rZIBjHzN1eGIvjb22DirMLXddY+NhE5MUIcYv/tKgK7IPccS/V3Tj3/iYIghIrqOQhq2SNEX9lOv++obkv+dGh/RbDxRuQym0CoM8ZfELroNIGFHa1VAjCAaoSNMPUOERtm707+gELnaxrcDujO0LdGkO5OzR9pDPHHpg/4+qJRbyPLq7Oqbjkdrv9kG2v38Y+DA8F7h3c3HqcOdUgKafg0g/Uwrqs2AkjCocG0hnYoCijq3gguYrzPHOEMPT1CQU4VeEzZLVOofaQNvAjD7+q8BzoYcJflFmuA1+kNBnOfM/gGrG0JIu0wOX6JTupSt1ZqtuRQp20OCr2jEaXT0i0iOqMdBh5evFarKvAj3Q7Cl7zfVTUKfW3KGri6nZEH0mxO6kU24oswAOTXoNmExmHdR3FkKZwNcP1kO8r6DB8QOImih2WzyN7dSXJbkHU9QqGDZkTxD0ri4owoaA+NuIee6k9gm/rInOlavz0pDOtKgTpuZxfytkTeachF8U9uI5eeB952mw/EDDJ/JzzX89t/fPzx3m1/zb24Oyb1kDRGoPKwV5byceCV8x5HYf9Okn6vaU9nSNZkOU9gmh0S5m6GuCGrW4T6BtFWYSAB2eEQt3aKeC8l51Lpzmqn6t2RjY83iQebuKTrp0R9Go2Qvqmr8vr53mf2OFQY5tGJzad/RpvTh14gplOyZunmng5DWJ9mphYJ4yzbPpoBIg3/+KhqcpXUKOac/6PtSaO/anN1CK6Dh420yn/xTrzDX7CtaaDOfWP5fntrw52zniNJH+Yu7Yhn/ztZ4b1+7BElGUmhvkoZLJKNGhJZEXDNg1f0usO630a/jVOgtN8nwzHDYfRagOefzCi9bzjqawHirkMpv6OK3F/OTqZlj49hj763WQhl8TmCFaP291vM951F/nxgp9Tkj+09J4aMiKeQ2jyV2HMQNPtt+5BRqT8Bek9uborlx3zppkTllBeWkPNiCoS7MCG/DES+RU+av0D/EwXaAsbcAA5gRbMMgagvw3k7jDL2BdZNlw2RvAtcnu0DpvtQbrIu4Zs+Buif5OW",
    ),
    "mixed_line_ending": (
        "534695fa5bcb0c2636e622e2508c5dca1262ac00aaacf7455a6a8e135cd1b56c",
        "eNqNVttu20YQfedXDOIHko5MNK8CaCBI7MKA4AZ2Hgo4ArEih9a25C67u7RlpPn3zF54s+S0AmyRszNn5sxtVSvZQlHUvekVFgXwtpPKABNCGma4FDqKBpl67JjSOLyXsmmw9Dq1hZkJMrYrB6x7/KdHUWIURZ/uNteQwy7+pr6JOBpe6PHTXZDH0Rl8UVijAjo2EpyN+17BrjdQoSEnXrzDWiokxejjZlNc3X6+uf39noASq2w1VnSWRtc3fxZf/yg2N7dXQYl0vselitcOlp6a2j1bi9g9b65/UMAV1lDU/JDUvEHBWlyDNmpFVIVBYfQadi8G9QpQVFw8htcULi7hVgpcR0Afgc/FYOBYxtlfkovEHdpPwwVmioB5l4TcpPA+YAJRdBrAxeg3013DjZXq5KvqMXVY/v8zN3uQHYoxaOL0vCNIpqFej17r7Flxg8k8vDRwJsrFYP2aO525J8eSC7N+26s69jpLRJ0pZFVCPv1B7xJa8dI8hKwS+pYU541F0bG+MVYroeNg/L9yNKNO6iG7ZDDrnUnFfnjtS0Oq2vJLvE261JqCf/DnW3ifw4cjnR2x/duHewb3skVgrTUDWUPLD1h5Bh5DOz0vzkH3bbKTskkOqYv94Hlan9kTa3pimKZwSU6jEDcVCfIcYiFjIINkeGe9IQkTFdCAe/wZHYW0BoQXH0E500m3ZYci5DC3EziXu6ST+LdRekZRVKhcxnXYDIy475HmV/flHsyeGWhqYmGn0X2dqFdhXjr8ZdHOgDVaTi5W0Gvr9jK3W6TmRJ3ZIL3Dzq0aTS8IDdPmdf0XhXXOtxZpJHncCou8zOxOKg6JOuEmGg0WC2jaPauZq/R1CX37YaNxitDQBkczBXe8Fh/I03ZWM5ctyoJxZWL05wN2w34urfh80bDe7MbEGjqpNd816I1JIOSiwW3b26z7qGCRotDaneySRdArt1Unsi6CYgCk1WpnxA7L69mYTIjNwmpZwDdTvQjjKNsLxLBCW0Yrnqye1uMN+EBbcwv/OhIUreOyWKLuflV0NNy12Uf12LcUwhd3kqQztYxVVcHC+XSZxBd1TKv34oKoxKsppXvJS9R54ud45VaDvWVM3zWYHPdCOtmGnZsH01G+x6bL4zvsGlbivLi+QWx1dYclrzlWGXz2KHb+31mgdwHpbU7xUAlN8QoS6zw+p0fv93o49HN9iD2QVaMMBjz3ZRG1q0W4LqhsT+N6sstlcGQ3i1XORs/reeecvBZXg8UhPbo+hpNhFx+vi452oknq+PsA92N94jaI04Xhcq5/BVU7qP8EDAn5EM2a2sqol4lFUVg0+nVoWRSF7eyiCFwU4xrh/kUbbK8O3CSu72ngfgJ5ix1w",
    ),
}


def _run(main, root: Path, check_id: str, output: Path, *paths: str) -> int:
    argv = [check_id, "--root", str(root), "--output-dir", str(output)]
    return main([*argv, "--", *paths] if paths else argv)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ("git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args),
        check=True,
        capture_output=True,
        env={"PATH": os.defpath, "LC_ALL": "C"},
    )


def _init_git(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "core.ignorecase", "false")


def _tree_bytes(root: Path) -> dict[str, tuple[str, bytes | str, int]]:
    snapshot = {}
    for path in root.rglob("*"):
        if ".git" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path), path.lstat().st_mode)
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes(), path.stat().st_mode)
        elif path.is_dir():
            snapshot[relative] = ("directory", b"", path.stat().st_mode)
        else:
            snapshot[relative] = ("other", b"", path.lstat().st_mode)
    return snapshot


def _index_tree(root: Path) -> bytes:
    return subprocess.run(
        ("git", "-C", str(root), "write-tree"),
        check=True,
        capture_output=True,
        env={"PATH": os.defpath, "LC_ALL": "C"},
    ).stdout


def test_every_retained_id_has_one_encodable_task7_disposition() -> None:
    from tools.project_checks import generated, hooks, packages, structure

    oracle = json.loads(Path("config/check-preservation.json").read_text())
    expected = {
        check_id
        for control in oracle["controls"]
        if control["disposition"] == "retained"
        for check_id in control["check_ids"]
    }
    dispositions = {}
    for module in (structure, generated, hooks, packages):
        for check_id, disposition in module.TASK7_DISPOSITIONS.items():
            assert check_id not in dispositions
            dispositions[check_id] = disposition
    assert len(expected) == 71
    assert dispositions.keys() == expected
    for check_id, (argv, selection) in dispositions.items():
        assert argv and all(isinstance(argument, str) and argument for argument in argv)
        assert not any("PATHS" in argument or "*" in argument for argument in argv)
        assert selection in {"changed", "project"}
        if selection == "changed":
            assert check_id.startswith("hook.")

    project_hooks = {
        "hook.check-credentials",
        "hook.check-cursor-rules-drift",
        "hook.cargo-fmt-check",
        "hook.cargo-clippy",
        "hook.pyright",
        "hook.gitleaks",
    }
    assert all(dispositions[check_id][1] == "project" for check_id in project_hooks)


@pytest.mark.parametrize(
    ("module_name", "check_id", "pin"),
    [
        ("hooks", "hook.golangci-lint", "v2.12.2"),
        ("hooks", "hook.terraform_validate", "v1.108.0"),
        ("structure", "lint.markdown.keydocs", "21c1be1b"),
        ("structure", "test.bundle-partition", "bats"),
    ],
)
def test_unresolved_dispositions_are_executable_blocked_bodies(
    tmp_path: Path, module_name: str, check_id: str, pin: str, capsys
) -> None:
    from tools.project_checks import hooks, structure

    root = tmp_path / "root"
    root.mkdir()
    module = {"hooks": hooks, "structure": structure}[module_name]
    assert _run(module.main, root, check_id, tmp_path / "out") == 3
    assert pin in capsys.readouterr().err


def _fixture_pre_commit_hooks(tmp_path: Path) -> Path:
    environment = tmp_path / "pre-commit-env"
    venv.EnvBuilder(with_pip=False).create(environment)
    binary = environment / "bin"
    python = binary / "python"
    site = Path(
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import sysconfig;print(sysconfig.get_paths()['purelib'])",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    package = site / "pre_commit_hooks"
    metadata = site / "pre_commit_hooks-6.0.0.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    (package / "__init__.py").write_text("")
    entries = []
    records = []
    for module, (digest, payload) in _PINNED_FIXER_SOURCES.items():
        source = zlib.decompress(base64.b64decode(payload))
        assert hashlib.sha256(source).hexdigest() == digest
        (package / f"{module}.py").write_bytes(source)
        executable = module.replace("_", "-")
        entries.append(f"{executable} = pre_commit_hooks.{module}:main")
        wrapper = binary / executable
        wrapper.write_text(
            f"#!{python}\nfrom pre_commit_hooks.{module} import main\n"
            "raise SystemExit(main())\n"
        )
        wrapper.chmod(0o755)
        records.append(f"pre_commit_hooks/{module}.py,,")
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: pre-commit-hooks\nVersion: 6.0.0\n"
    )
    (metadata / "entry_points.txt").write_text(
        "[console_scripts]\n" + "\n".join(entries) + "\n"
    )
    (metadata / "RECORD").write_text("\n".join(records) + "\n")
    return binary


_SYMLINKS = {
    "configs/claude/skills": "../../.apm/skills",
    "configs/cursor/scripts": "../claude/scripts",
    "configs/cursor/config": "../claude/config",
    "configs/cursor/prompts": "../claude/prompts",
    "configs/cursor/.plans": "../claude/.plans",
    "configs/gemini/scripts": "../claude/scripts",
    "configs/gemini/config": "../claude/config",
    "configs/gemini/prompts": "../claude/prompts",
    "configs/gemini/.plans": "../claude/.plans",
    "configs/codex/AGENTS.md": "../../AGENTS.md",
    "configs/codex/scripts": "../claude/scripts",
    "configs/codex/config": "../claude/config",
    "configs/codex/prompts": "../claude/prompts",
    "configs/codex/.plans": "../claude/.plans",
    "configs/antigravity/config": "../claude/config",
    "configs/antigravity/skills": "../claude/skills",
    "configs/antigravity/.plans": "../claude/.plans",
}


def _symlink_fixture(root: Path) -> None:
    for name, target in _SYMLINKS.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)


def test_structure_symlinks_accept_exact_map_and_reject_missing_and_wrong_target(
    tmp_path: Path,
) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    root.mkdir()
    output = tmp_path / "output"
    _symlink_fixture(root)
    assert _run(structure.main, root, "structure.symlinks", output) == 0

    (root / "configs/cursor/scripts").unlink()
    assert _run(structure.main, root, "structure.symlinks", output) == 2
    (root / "configs/cursor/scripts").symlink_to("../wrong/scripts")
    assert _run(structure.main, root, "structure.symlinks", output) == 2


def test_structure_yaml_rejects_parse_error_and_accepts_valid_yaml(
    tmp_path: Path,
) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    config = root / "configs/claude/config"
    config.mkdir(parents=True)
    output = tmp_path / "output"
    (config / "valid.yml").write_text("value: fixture\n")
    assert _run(structure.main, root, "syntax.yaml.config", output) == 0
    (config / "broken.yml").write_text("value: [unterminated\n")
    assert _run(structure.main, root, "syntax.yaml.config", output) == 2


def test_structure_case_collision_uses_complete_nul_safe_git_index(
    tmp_path: Path,
) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    root.mkdir()
    _init_git(root)
    (root / "Name.txt").write_text("one\n")
    _git(root, "add", "--", "Name.txt")
    blob = subprocess.run(
        ("git", "-C", str(root), "rev-parse", ":Name.txt"),
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.defpath, "LC_ALL": "C"},
    ).stdout.strip()
    _git(root, "update-index", "--add", "--cacheinfo", f"100644,{blob},name.TXT")
    assert _run(structure.main, root, "structure.case-collision", tmp_path / "out") == 2


def test_structure_inventory_rejects_wrong_count_and_missing_shell(
    tmp_path: Path,
) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    skills = root / "configs/claude/skills/one"
    config = root / "configs/claude/config"
    scripts = root / "configs/claude/scripts"
    skills.mkdir(parents=True)
    config.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# one\n")
    (config / "skill_policies.yml").write_text("expected_total: 2\n")
    shell = scripts / "present.sh"
    shell.write_text("#!/bin/bash\nexit 0\n")
    output = tmp_path / "out"
    assert _run(structure.main, root, "structure.inventory", output) == 2

    (config / "skill_policies.yml").write_text("expected_total: 1\n")
    assert _run(structure.main, root, "structure.inventory", output) == 0
    shell.unlink()
    assert _run(structure.main, root, "structure.inventory", output) == 2


def test_structure_inventory_expected_total_matches_anchored_legacy_extraction(
    tmp_path: Path,
) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    (root / "configs/claude/skills/one").mkdir(parents=True)
    (root / "configs/claude/skills/one/SKILL.md").write_text("# one\n")
    (root / "configs/claude/scripts").mkdir()
    (root / "configs/claude/scripts/one.sh").write_text("#!/bin/bash\n")
    policy = root / "configs/claude/config/skill_policies.yml"
    policy.parent.mkdir()
    policy.write_text("section:\n  expected_total: 1\n")
    assert _run(structure.main, root, "structure.inventory", tmp_path / "out") == 2
    policy.write_text("expected_total: 1 # observed inline comment\n")
    assert _run(structure.main, root, "structure.inventory", tmp_path / "out") == 0


def test_structure_shell_syntax_and_skill_paths_have_real_negative_fixtures(
    tmp_path: Path,
) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    script_dir = root / "configs/claude/scripts"
    bootstrap_lib = root / "bootstrap/lib"
    skill_dir = root / ".apm/skills/example"
    for directory in (script_dir, bootstrap_lib, skill_dir):
        directory.mkdir(parents=True)
    (root / "bootstrap.sh").write_text("#!/bin/bash\ntrue\n")
    (script_dir / "valid.sh").write_text("#!/bin/bash\ntrue\n")
    (bootstrap_lib / "valid.sh").write_text("#!/bin/bash\ntrue\n")
    (skill_dir / "SKILL.md").write_text("use a relative references/file.md path\n")
    output = tmp_path / "out"
    assert _run(structure.main, root, "syntax.shell.project", output) == 0
    assert _run(structure.main, root, "structure.skill-paths", output) == 0

    (script_dir / "broken.sh").write_text("if then\n")
    assert _run(structure.main, root, "syntax.shell.project", output) == 2
    (skill_dir / "SKILL.md").write_text("bad .claude/skills/absolute/path\n")
    assert _run(structure.main, root, "structure.skill-paths", output) == 2


def test_shell_syntax_reports_empty_required_sets_and_keeps_findings(
    tmp_path: Path, capsys
) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    (root / "configs/claude/scripts").mkdir(parents=True)
    (root / "bootstrap/lib").mkdir(parents=True)
    (root / "bootstrap.sh").write_text("#!/bin/bash\ntrue\n")
    assert _run(structure.main, root, "syntax.shell.project", tmp_path / "out") == 3
    assert "configs/claude/scripts/*.sh" in capsys.readouterr().err

    (root / "configs/claude/scripts/broken.sh").write_text("if then\n")
    (root / "bootstrap.sh").write_text("#!/bin/bash\nif then\n")
    assert _run(structure.main, root, "syntax.shell.project", tmp_path / "out") == 2
    diagnostic = capsys.readouterr().err
    assert "FAIL:" in diagnostic
    assert "bootstrap/lib/*.sh" in diagnostic


def test_shell_syntax_reports_nonfile_matches_and_keeps_findings(
    tmp_path: Path, capsys
) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    scripts = root / "configs/claude/scripts"
    libraries = root / "bootstrap/lib"
    scripts.mkdir(parents=True)
    libraries.mkdir(parents=True)
    (root / "bootstrap.sh").write_text("#!/bin/bash\ntrue\n")
    (scripts / "valid.sh").write_text("#!/bin/bash\nif then\n")
    (libraries / "valid.sh").write_text("#!/bin/bash\ntrue\n")
    (scripts / "directory.sh").mkdir()
    (libraries / "dangling.sh").symlink_to("missing")

    assert _run(structure.main, root, "syntax.shell.project", tmp_path / "out") == 2
    diagnostic = capsys.readouterr().err
    assert "FAIL:" in diagnostic
    assert "directory.sh" in diagnostic
    assert "dangling.sh" in diagnostic

    (scripts / "valid.sh").write_text("#!/bin/bash\ntrue\n")
    assert _run(structure.main, root, "syntax.shell.project", tmp_path / "out") == 3


def test_structure_rejects_output_inside_source_as_blocked(tmp_path: Path) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    root.mkdir()
    assert _run(structure.main, root, "structure.symlinks", root / "output") == 3


def _write_generator(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env python3\n" + body)


def _cursor_generator_fixture(root: Path, body: str) -> Path:
    scripts = root / "configs/claude/scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    script = scripts / "generate_cursor_rules.sh"
    script.write_text(
        "#!/bin/bash\nset -eu\n"
        '[ "$#" -eq 1 ] && [ "$1" = --dry-run ] || exit 2\n' + body
    )
    for name in (
        "generate_commands_doc.py",
        "generate_cursor_mcp.py",
        "generate_cursor_agents.py",
    ):
        (scripts / name).write_text("# fixture input\n")
    (root / "configs/claude/config").mkdir(parents=True, exist_ok=True)
    (root / "configs/claude/config/mcp_servers.yml").write_text("servers: {}\n")
    (root / "configs/claude/skills/one").mkdir(parents=True, exist_ok=True)
    (root / "configs/claude/skills/one/SKILL.md").write_text(
        "---\ndescription: one\n---\n"
    )
    (root / "configs/claude/agents").mkdir(parents=True, exist_ok=True)
    (root / "configs/claude/agents/one.md").write_text("---\nname: one\n---\nbody\n")
    (root / "configs/cursor/rules").mkdir(parents=True, exist_ok=True)
    return script


def test_generated_native_check_reports_drift_without_mutating_source(
    tmp_path: Path,
) -> None:
    from tools.project_checks import generated

    root = tmp_path / "root"
    script = root / "configs/claude/scripts/generate_commands_doc.py"
    _write_generator(
        script,
        "import pathlib, sys\n"
        "if sys.argv[1:] != ['--check']: raise SystemExit(2)\n"
        "root = pathlib.Path(__file__).parents[3]\n"
        "raise SystemExit(0 if (root/'source').read_bytes() == (root/'docs/COMMANDS.md').read_bytes() else 1)\n",
    )
    (script.parent / "command_catalog.py").write_text("# required generator module\n")
    (root / "docs").mkdir()
    (root / "source").write_bytes(b"expected\n")
    target = root / "docs/COMMANDS.md"
    target.write_bytes(b"expected\n")
    output = tmp_path / "output"
    assert _run(generated.main, root, "generated.commands-doc", output) == 0

    target.write_bytes(b"drifted\n")
    before = target.read_bytes()
    assert _run(generated.main, root, "generated.commands-doc", output) == 2
    assert target.read_bytes() == before


def test_generated_real_missing_import_blocks_and_malformed_vendor_fails(
    tmp_path: Path,
) -> None:
    from tools.project_checks import generated

    source_root = Path(__file__).resolve().parents[3]
    missing_import_root = tmp_path / "missing-import"
    script = missing_import_root / "tools/generate_plugin_views.py"
    script.parent.mkdir(parents=True)
    shutil.copy2(source_root / "tools/generate_plugin_views.py", script)
    # A script-directory shadow wins Python's import search deterministically,
    # while the adapter's root-level preflight can still verify its environment.
    (script.parent / "yaml.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'yaml'\")\n"
    )
    assert (
        _run(
            generated.main,
            missing_import_root,
            "generated.plugin-views",
            tmp_path / "out-a",
        )
        == 3
    )

    malformed_root = tmp_path / "malformed"
    vendor = malformed_root / "tools/vendor_bundle_dependencies.py"
    vendor.parent.mkdir(parents=True)
    shutil.copy2(source_root / "tools/vendor_bundle_dependencies.py", vendor)
    (malformed_root / "uv.lock").write_text("[[broken\n")
    metadata = (
        malformed_root
        / "plugins/manifest-code-quality/skills/smoke-manage/vendor/VENDOR.json"
    )
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}\n")
    assert (
        _run(
            generated.main,
            malformed_root,
            "generated.vendor",
            tmp_path / "out-b",
        )
        == 2
    )


def test_cursor_verifier_preflights_dependencies_and_reads_stderr(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import generated, hooks

    root = tmp_path / "root"
    _cursor_generator_fixture(
        root,
        "echo 'Cursor rules: 0 created, 0 updated, 1 unchanged, 0 removed'\n"
        "echo 'Cursor mcp.json: unchanged (1 servers)'\n"
        "echo 'Cursor agents: 0 created, 0 updated, 1 unchanged, 0 removed'\n",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text("#!/bin/sh\nexit 1\n")
    fake_python.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/bin:/usr/bin")
    assert _run(generated.main, root, "generated.cursor", tmp_path / "out") == 3
    assert (
        _run(hooks.main, root, "hook.check-cursor-rules-drift", tmp_path / "out") == 3
    )

    fake_python.write_text(
        '#!/bin/sh\nif [ "${1:-}" = -c ]; then exit 0; fi\necho compact-index\n'
    )
    _cursor_generator_fixture(
        root,
        "echo 'Cursor rules: 0 created, 0 updated, 1 unchanged, 0 removed'\n"
        "echo 'Cursor mcp.json: unchanged (1 servers)'\n"
        "echo 'Cursor agents: 0 created, 0 updated, 1 unchanged, 0 removed'\n"
        "echo '[DRY-RUN] Would update: hidden' >&2\n",
    )
    assert _run(generated.main, root, "generated.cursor", tmp_path / "out") == 2
    assert (
        _run(hooks.main, root, "hook.check-cursor-rules-drift", tmp_path / "out") == 2
    )


def test_generator_new_file_is_detected_and_candidate_index_is_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import generated

    root = tmp_path / "root"
    root.mkdir()
    _init_git(root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "python3").write_text(
        '#!/bin/sh\nif [ "${1:-}" = -c ]; then exit 0; fi\necho compact-index\n'
    )
    (fake_bin / "python3").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/bin:/usr/bin")
    _cursor_generator_fixture(
        root,
        "if [ -f trigger-new ]; then\n"
        "    echo '[DRY-RUN] Would create: rules/new.mdc'\n"
        "fi\n"
        "echo 'Cursor rules: 0 created, 0 updated, 1 unchanged, 0 removed'\n"
        "echo 'Cursor mcp.json: unchanged (1 servers)'\n"
        "echo 'Cursor agents: 0 created, 0 updated, 1 unchanged, 0 removed'\n",
    )
    original = root / "configs/cursor/rules/existing.mdc"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"original\n")
    _git(root, "add", ".")
    index_before = subprocess.run(
        ("git", "-C", str(root), "write-tree"),
        check=True,
        capture_output=True,
        env={"PATH": os.defpath, "LC_ALL": "C"},
    ).stdout
    output = tmp_path / "output"
    assert _run(generated.main, root, "generated.cursor", output) == 0

    (root / "trigger-new").write_text("yes\n")
    source_before = _tree_bytes(root)
    assert _run(generated.main, root, "generated.cursor", output) == 2
    index_after = subprocess.run(
        ("git", "-C", str(root), "write-tree"),
        check=True,
        capture_output=True,
        env={"PATH": os.defpath, "LC_ALL": "C"},
    ).stdout
    assert _tree_bytes(root) == source_before
    assert index_after == index_before


def test_generated_missing_verifier_is_blocked(tmp_path: Path) -> None:
    from tools.project_checks import generated

    root = tmp_path / "root"
    root.mkdir()
    assert _run(generated.main, root, "generated.plugin-views", tmp_path / "out") == 3


def test_hook_fixers_use_pinned_v6_semantics_without_rewriting_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import hooks

    provisioned = _fixture_pre_commit_hooks(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    _init_git(root)
    output = tmp_path / "output"
    bad = root / "odd name.txt"
    bad.write_bytes(b"value\r\n")
    _git(root, "add", ".")
    monkeypatch.setenv("PATH", f"{provisioned}:{os.defpath}")
    before = _tree_bytes(root)
    index_before = _index_tree(root)
    assert _run(hooks.main, root, "hook.mixed-line-ending", output, bad.name) == 2
    assert _tree_bytes(root) == before
    assert _index_tree(root) == index_before

    option_like = root / "--help"
    option_like.write_bytes(b"value \n")
    before = _tree_bytes(root)
    assert (
        _run(
            hooks.main,
            root,
            "hook.trailing-whitespace",
            output,
            option_like.name,
        )
        == 2
    )
    assert _tree_bytes(root) == before

    bad.write_bytes(b"value \r\n")
    before = _tree_bytes(root)
    assert _run(hooks.main, root, "hook.trailing-whitespace", output, bad.name) == 2
    assert _tree_bytes(root) == before
    excluded = root / "semantic.patch"
    excluded.write_bytes(b"value \n")
    assert (
        _run(hooks.main, root, "hook.trailing-whitespace", output, excluded.name) == 0
    )

    bad.write_bytes(b"\n")
    before = _tree_bytes(root)
    assert _run(hooks.main, root, "hook.end-of-file-fixer", output, bad.name) == 2
    assert _tree_bytes(root) == before

    bad.write_bytes(b"value\r\n\r\n")
    before = _tree_bytes(root)
    assert _run(hooks.main, root, "hook.end-of-file-fixer", output, bad.name) == 2
    assert _tree_bytes(root) == before

    bad.write_bytes(b"value\r")
    before = _tree_bytes(root)
    assert _run(hooks.main, root, "hook.end-of-file-fixer", output, bad.name) == 0
    assert _tree_bytes(root) == before


def test_hook_fixers_block_when_pinned_v6_is_not_provisioned(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import hooks

    root = tmp_path / "root"
    root.mkdir()
    (root / "value.txt").write_text("value\n")
    monkeypatch.setenv("PATH", "/nonexistent")
    assert (
        _run(
            hooks.main,
            root,
            "hook.trailing-whitespace",
            tmp_path / "out",
            "value.txt",
        )
        == 3
    )


def test_hook_fixer_rejects_shadowed_entry_point_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import hooks

    binary = _fixture_pre_commit_hooks(tmp_path)
    site = Path(
        subprocess.run(
            [
                str(binary / "python"),
                "-I",
                "-c",
                "import sysconfig;print(sysconfig.get_paths()['purelib'])",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    entries = site / "pre_commit_hooks-6.0.0.dist-info/entry_points.txt"
    entries.write_text(
        "[console_scripts]\n"
        "trailing-whitespace-fixer = shadowed.module:main\n"
        "end-of-file-fixer = pre_commit_hooks.end_of_file_fixer:main\n"
        "mixed-line-ending = pre_commit_hooks.mixed_line_ending:main\n"
    )
    root = tmp_path / "root"
    root.mkdir()
    (root / "value.txt").write_text("value\n")
    monkeypatch.setenv("PATH", f"{binary}:{os.defpath}")
    assert (
        _run(
            hooks.main,
            root,
            "hook.trailing-whitespace",
            tmp_path / "out",
            "value.txt",
        )
        == 3
    )


def test_hook_path_errors_do_not_hide_valid_input_findings(
    tmp_path: Path, capsys
) -> None:
    from tools.project_checks import hooks

    root = tmp_path / "root"
    root.mkdir()
    (root / "broken.yml").write_text("value: [broken\n")
    assert (
        _run(
            hooks.main,
            root,
            "hook.validate-yaml-configs",
            tmp_path / "out",
            "broken.yml",
            "missing.yml",
        )
        == 2
    )
    diagnostic = capsys.readouterr().err
    assert "FAIL:" in diagnostic
    assert "BLOCKED:" in diagnostic


def test_fixer_copy_failure_does_not_hide_readable_violation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from tools.project_checks import hooks

    binary = _fixture_pre_commit_hooks(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    (root / "bad.txt").write_bytes(b"bad \n")
    (root / "unreadable.txt").write_bytes(b"ok\n")
    original_copy = hooks.shutil.copy2

    def selective_copy(source, destination):
        if Path(source).name == "unreadable.txt":
            raise PermissionError("fixture copy denied")
        return original_copy(source, destination)

    monkeypatch.setattr(hooks.shutil, "copy2", selective_copy)
    monkeypatch.setenv("PATH", f"{binary}:{os.defpath}")
    assert (
        _run(
            hooks.main,
            root,
            "hook.trailing-whitespace",
            tmp_path / "out",
            "bad.txt",
            "unreadable.txt",
        )
        == 2
    )
    diagnostic = capsys.readouterr().err
    assert "FAIL:" in diagnostic
    assert "BLOCKED:" in diagnostic


def test_cursor_drift_takes_precedence_over_incomplete_output(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from tools.project_checks import generated

    root = tmp_path / "root"
    _cursor_generator_fixture(
        root,
        "echo '[DRY-RUN] Would update: missing.mdc'\n"
        "echo 'Cursor rules: 0 created, 1 updated, 0 unchanged, 0 removed'\n",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        '#!/bin/sh\nif [ "${1:-}" = -c ]; then exit 0; fi\necho compact-index\n'
    )
    fake_python.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/bin:/usr/bin")
    assert _run(generated.main, root, "generated.cursor", tmp_path / "out") == 2
    captured = capsys.readouterr()
    assert "FAIL:" in captured.err
    assert "BLOCKED:" in captured.err


def test_hook_valid_fixtures_pass_and_missing_external_formatter_blocks(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import hooks

    root = tmp_path / "root"
    root.mkdir()
    good = root / "good.py"
    good.write_text("value = 1\n")
    output = tmp_path / "output"
    provisioned = _fixture_pre_commit_hooks(tmp_path)
    monkeypatch.setenv("PATH", f"{provisioned}:{os.defpath}")
    for check_id in (
        "hook.trailing-whitespace",
        "hook.end-of-file-fixer",
        "hook.mixed-line-ending",
    ):
        assert _run(hooks.main, root, check_id, output, good.name) == 0
    monkeypatch.setenv("PATH", "/nonexistent")
    assert _run(hooks.main, root, "hook.shfmt", output, good.name) == 3


def test_hook_custom_scanners_preserve_scope_and_redact_credentials(
    tmp_path: Path, capsys
) -> None:
    from tools.project_checks import hooks

    root = tmp_path / "root"
    docs = root / "docs"
    docs.mkdir(parents=True)
    output = tmp_path / "output"
    credential = docs / "credential.md"
    secret = "sk-" + "A" * 32
    credential.write_text(f"token: {secret}\n")
    assert _run(hooks.main, root, "hook.check-credentials", output) == 2
    assert secret not in capsys.readouterr().err

    stale = docs / "stale.md"
    stale.write_text("run .claude/scripts/tool.sh\n")
    assert (
        _run(hooks.main, root, "hook.check-stale-repo-paths", output, "docs/stale.md")
        == 2
    )
    stale.write_text("run ~/.claude/scripts/tool.sh\n")
    assert (
        _run(hooks.main, root, "hook.check-stale-repo-paths", output, "docs/stale.md")
        == 0
    )


def test_credentials_skip_leaf_symlinks_without_following_outside(
    tmp_path: Path, capsys
) -> None:
    from tools.project_checks import hooks

    root = tmp_path / "root"
    root.mkdir()
    (root / "AGENTS.md").write_text("safe\n")
    codex = root / "configs/codex"
    codex.mkdir(parents=True)
    (codex / "AGENTS.md").symlink_to("../../AGENTS.md")
    outside = tmp_path / "outside-secret.md"
    secret = "sk-" + "Q" * 32
    outside.write_text(secret)
    docs = root / "docs"
    docs.mkdir()
    (docs / "outside.md").symlink_to(outside)

    assert _run(hooks.main, root, "hook.check-credentials", tmp_path / "out") == 0
    diagnostic = capsys.readouterr().err
    assert secret not in diagnostic
    assert str(outside) not in diagnostic


def test_hook_yaml_config_and_cursor_drift_alias_are_non_mutating(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import hooks

    root = tmp_path / "root"
    root.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "python3").write_text(
        '#!/bin/sh\nif [ "${1:-}" = -c ]; then exit 0; fi\necho compact-index\n'
    )
    (fake_bin / "python3").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/bin:/usr/bin")
    output = tmp_path / "output"
    config = root / "configs/claude/config/value.yml"
    config.parent.mkdir(parents=True)
    config.write_text("value: ok\n")
    assert (
        _run(
            hooks.main,
            root,
            "hook.validate-yaml-configs",
            output,
            "configs/claude/config/value.yml",
        )
        == 0
    )
    config.write_text("value: [broken\n")
    before = config.read_bytes()
    assert (
        _run(
            hooks.main,
            root,
            "hook.validate-yaml-configs",
            output,
            "configs/claude/config/value.yml",
        )
        == 2
    )
    assert config.read_bytes() == before

    _cursor_generator_fixture(
        root,
        "echo '[DRY-RUN] Would create: rules/new.mdc'\n"
        "echo 'Cursor rules: 1 created, 0 updated, 0 unchanged, 0 removed'\n"
        "echo 'Cursor mcp.json: unchanged (1 servers)'\n"
        "echo 'Cursor agents: 0 created, 0 updated, 1 unchanged, 0 removed'\n",
    )
    assert _run(hooks.main, root, "hook.check-cursor-rules-drift", output) == 2


def _fake_uv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, shutil, sys\n"
        "args = sys.argv[1:]\n"
        "project = pathlib.Path(os.environ['FAKE_PROJECT'])\n"
        "log = os.environ.get('FAKE_LOG')\n"
        "if log: pathlib.Path(log).write_text('\\0'.join(args))\n"
        "if args and args[0] == 'lock':\n"
        "    expected = ['lock', '--check', '--offline', '--no-python-downloads', '--project', str(project)]\n"
        "    if args != expected: raise SystemExit(97)\n"
        "    pyproject = (project / 'pyproject.toml').read_text()\n"
        "    lock = (project / 'uv.lock').read_text()\n"
        '    version = pyproject.split("version=\'", 1)[1].split("\'", 1)[0]\n'
        "    if f'project-version = {version}' not in lock:\n"
        "        print('lockfile needs to be updated', file=sys.stderr); raise SystemExit(1)\n"
        "    raise SystemExit(0)\n"
        "expected_prefix = ['build', '--offline', '--no-python-downloads', '--no-build-isolation', '--no-create-gitignore', '--out-dir']\n"
        "if args[:6] != expected_prefix or len(args) != 8 or args[7] != str(project): raise SystemExit(98)\n"
        "out = pathlib.Path(args[6]); out.mkdir(parents=True, exist_ok=True)\n"
        "shutil.copyfile(os.environ['FAKE_WHEEL'], out / 'manifest_agent-0.1-py3-none-any.whl')\n"
    )
    path.chmod(0o755)


def test_package_lock_check_passes_and_mismatch_fails_without_rewriting_lock(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import packages

    root = tmp_path / "root"
    project = root / "configs/claude"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
    lock = project / "uv.lock"
    lock.write_bytes(b"project-version = 1\n")
    fake_uv = tmp_path / "bin/uv"
    _fake_uv(fake_uv)
    monkeypatch.setenv("PATH", str(fake_uv.parent))
    monkeypatch.setenv("FAKE_PROJECT", str(project))
    before = lock.read_bytes()
    output = tmp_path / "output"
    assert _run(packages.main, root, "dependency.lock.config", output) == 0
    (project / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='2'\n")
    assert _run(packages.main, root, "dependency.lock.config", output) == 2
    assert lock.read_bytes() == before


def test_package_missing_offline_tool_is_blocked(tmp_path: Path, monkeypatch) -> None:
    from tools.project_checks import packages

    root = tmp_path / "root"
    project = root / "plugins/manifest-delegate"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
    (project / "uv.lock").write_text("project-version = 1\n")
    monkeypatch.setenv("PATH", "/nonexistent")
    assert _run(packages.main, root, "dependency.lock.delegate", tmp_path / "out") == 3


def test_package_rejects_project_and_output_child_symlink_escape(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import packages

    root = tmp_path / "root"
    root.mkdir()
    escaped = tmp_path / "escaped-project"
    escaped.mkdir()
    (escaped / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
    (escaped / "uv.lock").write_text("project-version = 1\n")
    (root / "configs").mkdir()
    (root / "configs/claude").symlink_to(escaped, target_is_directory=True)
    fake_uv = tmp_path / "bin/uv"
    _fake_uv(fake_uv)
    log = tmp_path / "uv.log"
    monkeypatch.setenv("PATH", str(fake_uv.parent))
    monkeypatch.setenv("FAKE_PROJECT", str(escaped))
    monkeypatch.setenv("FAKE_LOG", str(log))
    assert _run(packages.main, root, "dependency.lock.config", tmp_path / "out") == 3
    assert not log.exists()

    project = root / "project"
    project.mkdir()
    (root / "pyproject.toml").write_text(
        "[build-system]\nrequires=['hatchling']\nbuild-backend='hatchling.build'\n"
    )
    backend = tmp_path / "backend/hatchling"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("")
    (backend / "build.py").write_text("")
    monkeypatch.syspath_prepend(str(backend.parent))
    output = tmp_path / "build-out"
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (output / "package-coordinator").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("FAKE_PROJECT", str(root))
    assert _run(packages.main, root, "package.coordinator", output) == 3
    assert not log.exists()


def test_package_missing_preprovisioned_build_backend_is_blocked(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import packages

    root = tmp_path / "root"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[build-system]\nrequires=['fixture_missing_backend']\n"
        "build-backend='fixture_missing_backend.build'\n"
    )
    fake_uv = tmp_path / "bin/uv"
    _fake_uv(fake_uv)
    monkeypatch.setenv("PATH", str(fake_uv.parent))
    monkeypatch.setenv("FAKE_PROJECT", str(root))
    assert _run(packages.main, root, "package.coordinator", tmp_path / "out") == 3


def test_package_coordinator_rejects_wheel_missing_required_schema(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import packages

    root = tmp_path / "root"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[build-system]\nrequires=['hatchling']\nbuild-backend='hatchling.build'\n"
    )
    backend = tmp_path / "backend/hatchling"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("")
    (backend / "build.py").write_text("")
    monkeypatch.syspath_prepend(str(backend.parent))
    wheel = tmp_path / "source.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("manifest_agent/__init__.py", "")
        archive.writestr("manifest_agent/data/project-checks.schema.json", "{}")
    fake_uv = tmp_path / "bin/uv"
    _fake_uv(fake_uv)
    monkeypatch.setenv("PATH", str(fake_uv.parent))
    monkeypatch.setenv("FAKE_WHEEL", str(wheel))
    monkeypatch.setenv("FAKE_PROJECT", str(root))
    assert _run(packages.main, root, "package.coordinator", tmp_path / "valid-out") == 0

    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("manifest_agent/__init__.py", "")
    assert _run(packages.main, root, "package.coordinator", tmp_path / "bad-out") == 2


def test_release_manifest_requires_declared_base_and_rejects_url_mismatch(
    tmp_path: Path,
) -> None:
    from tools.project_checks import packages

    root = tmp_path / "root"
    root.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    (output / "manifest-release.json").write_text(
        '{"version":"1.0.0","archive_url":"https://example.invalid/1.0.0/a.tar.gz",'
        '"bundles":{"manifest-docs":{"version":"1.0.0"}}}\n'
    )
    base = "https://example.invalid/releases/download"
    argv = [
        "package.release-manifest",
        "--root",
        str(root),
        "--output-dir",
        str(output),
        "--archive-base-url",
        base,
    ]
    assert packages.main(argv) == 2
    (output / "manifest-release.json").write_text(
        '{"version":"1.0.0","archive_url":"https://example.invalid/releases/download/1.0.0/a.tar.gz",'
        '"bundles":{"manifest-docs":{"version":"1.0.0"}}}\n'
    )
    assert packages.main(argv) == 0
    (output / "manifest-release.json").write_text(
        '{"version":"1.0.0","archive_url":"https://example.invalid/releases/download/1.0.0/a.tar.gz",'
        '"bundles":{"manifest-docs":{"version":"2.0.0"}}}\n'
    )
    assert packages.main(argv) == 2
    assert _run(packages.main, root, "package.release-manifest", output) == 3


@pytest.mark.parametrize(
    "base",
    [
        "://missing-scheme",
        "http://example.invalid/releases",
        "https://user@example.invalid/releases",
        "https://example.invalid/releases?channel=stable",
        "https://example.invalid/releases#fragment",
        "https://example.invalid:not-a-port/releases",
        "https://example.invalid/releases/../download",
        "https://example.invalid/releases/%2e%2e/download",
    ],
)
def test_release_archive_base_rejects_noncanonical_urls_without_crashing(
    tmp_path: Path, base: str, capsys
) -> None:
    from tools.project_checks import packages

    root = tmp_path / "root"
    root.mkdir()
    status = packages.main(
        [
            "package.release-manifest",
            "--root",
            str(root),
            "--output-dir",
            str(tmp_path / "out"),
            "--archive-base-url",
            base,
        ]
    )
    assert status == 3
    assert "archive base URL" in capsys.readouterr().err


def test_release_archive_passes_explicit_base_url_literally(tmp_path: Path) -> None:
    from tools.project_checks import packages

    root = tmp_path / "root"
    script = root / "tools/build_manifest_release.py"
    script.parent.mkdir(parents=True)
    observed = tmp_path / "observed"
    base = "https://downloads.example.invalid/manifest"
    script.write_text(
        "import pathlib, sys\n"
        f"expected = ['--repo-root', {str(root)!r}, '--output-dir', {str(tmp_path / 'out')!r}, "
        f"'--archive-base-url', {base!r}]\n"
        f"pathlib.Path({str(observed)!r}).write_text('\\0'.join(sys.argv[1:]))\n"
        "raise SystemExit(0 if sys.argv[1:] == expected else 2)\n"
    )
    assert (
        packages.main(
            [
                "package.release-archive",
                "--root",
                str(root),
                "--output-dir",
                str(tmp_path / "out"),
                "--archive-base-url",
                base,
            ]
        )
        == 0
    )
    assert observed.read_text().split("\0")[-1] == base


def test_generated_nonvendor_malformed_source_traceback_is_a_finding(
    tmp_path: Path,
) -> None:
    from tools.project_checks import generated

    source_root = Path(__file__).resolve().parents[3]
    root = tmp_path / "inventory"
    script = root / "tools/render_capability_inventory.py"
    script.parent.mkdir(parents=True)
    shutil.copy2(source_root / "tools/render_capability_inventory.py", script)
    package = root / "src/manifest_agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "migration.py").write_text(
        "from pathlib import Path\nimport yaml\n"
        "def load_legacy_inventory():\n"
        " document=yaml.safe_load(Path(__file__).with_name('data').joinpath('legacy_inventory.yml').read_text())\n"
        " if not isinstance(document, dict): raise ValueError('legacy ownership inventory must be a mapping')\n"
    )
    data = package / "data"
    data.mkdir()
    (data / "legacy_inventory.yml").write_text("[]\n")
    assert (
        _run(
            generated.main,
            root,
            "generated.capability-inventory",
            tmp_path / "out",
        )
        == 2
    )


def test_cursor_drift_takes_precedence_over_runtime_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from tools.project_checks import generated

    root = tmp_path / "root"
    _cursor_generator_fixture(
        root,
        "echo '[DRY-RUN] Would update: missing.mdc'\n"
        "echo 'Cursor rules: 0 created, 1 updated, 0 unchanged, 0 removed'\n"
        "echo 'Cursor mcp.json: unchanged (1 servers)'\n"
        "echo 'Cursor agents: 0 created, 0 updated, 1 unchanged, 0 removed'\n"
        "echo \"Traceback: ModuleNotFoundError: No module named 'yaml'\" >&2\n"
        "exit 1\n",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        '#!/bin/sh\nif [ "${1:-}" = -c ]; then exit 0; fi\necho compact-index\n'
    )
    fake_python.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/bin:/usr/bin")
    assert _run(generated.main, root, "generated.cursor", tmp_path / "out") == 2
    diagnostic = capsys.readouterr().err
    assert "FAIL:" in diagnostic
    assert "BLOCKED:" in diagnostic


@pytest.mark.parametrize("attack", ["candidate", "pythonpath"])
def test_hook_fixer_cannot_import_shadowed_pinned_module(
    tmp_path: Path, monkeypatch, attack: str
) -> None:
    from tools.project_checks import hooks

    binary = _fixture_pre_commit_hooks(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    bad = root / "bad.txt"
    bad.write_bytes(b"missing newline")
    marker = tmp_path / "hostile-ran"
    hostile = (
        root / "pre_commit_hooks"
        if attack == "candidate"
        else tmp_path / "hostile/pre_commit_hooks"
    )
    hostile.mkdir(parents=True)
    (hostile / "__init__.py").write_text("")
    (hostile / "end_of_file_fixer.py").write_text(
        "import os, pathlib\n"
        "pathlib.Path(os.environ['HOSTILE_MARKER']).write_text('ran')\n"
    )
    monkeypatch.setenv("HOSTILE_MARKER", str(marker))
    monkeypatch.setenv("PATH", f"{binary}:{os.defpath}")
    selected = [bad.name]
    if attack == "candidate":
        selected.extend(
            [
                "pre_commit_hooks/__init__.py",
                "pre_commit_hooks/end_of_file_fixer.py",
            ]
        )
    else:
        monkeypatch.setenv("PYTHONPATH", str(hostile.parent))
    assert (
        _run(
            hooks.main,
            root,
            "hook.end-of-file-fixer",
            tmp_path / "out",
            *selected,
        )
        == 2
    )
    assert not marker.exists()


def test_mapped_release_checks_use_distinct_internal_handoffs(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.project_checks import packages

    root = tmp_path / "root"
    root.mkdir()
    _init_git(root)
    script = root / "tools/build_manifest_release.py"
    script.parent.mkdir()
    log = tmp_path / "outputs.log"
    script.write_text(
        "import argparse, json, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--repo-root', required=True); "
        "p.add_argument('--output-dir', required=True); p.add_argument('--archive-base-url', required=True); a=p.parse_args()\n"
        f"log=pathlib.Path({str(log)!r}); log.write_text((log.read_text() if log.exists() else '')+a.output_dir+'\\n')\n"
        "out=pathlib.Path(a.output_dir); out.mkdir(parents=True, exist_ok=True)\n"
        "(out/'manifest-release.json').write_text(json.dumps({'version':'1.0.0','archive_url':a.archive_base_url+'/1.0.0/a.tar.gz','bundles':{'manifest-docs':{'version':'1.0.0'}}}))\n"
    )
    _git(root, "add", ".")
    before = _tree_bytes(root)
    index_before = _index_tree(root)
    monkeypatch.chdir(root)
    for check_id in ("package.release-archive", "package.release-manifest"):
        argv, selection = packages.TASK7_DISPOSITIONS[check_id]
        assert selection == "project"
        assert packages.main(list(argv[2:])) == 0
    outputs = log.read_text().splitlines()
    assert len(outputs) == 2 and len(set(outputs)) == 2
    assert all(not Path(output).exists() for output in outputs)
    assert _tree_bytes(root) == before
    assert _index_tree(root) == index_before


def test_shell_wrapper_keeps_finding_when_later_group_times_out(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    (root / "bootstrap/lib").mkdir(parents=True)
    (root / "bootstrap.sh").write_text("#!/bin/bash\n")
    (root / "bootstrap/lib/value.sh").write_text("#!/bin/bash\n")
    monkeypatch.setattr(structure.shutil, "which", lambda _: "/fixture/shellcheck")
    calls = 0

    def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(args[0], 1, "", "first finding\n")
        raise subprocess.TimeoutExpired(args[0], 300)

    monkeypatch.setattr(structure.subprocess, "run", run)
    assert _run(structure.main, root, "lint.shell.bootstrap", tmp_path / "out") == 2
    diagnostic = capsys.readouterr().err
    assert "first finding" in diagnostic
    assert "BLOCKED:" in diagnostic


@pytest.mark.parametrize(("returncode", "expected"), [(1, 2), (2, 3)])
def test_yaml_wrapper_distinguishes_findings_from_unavailable_execution(
    tmp_path: Path, monkeypatch, returncode: int, expected: int
) -> None:
    from tools.project_checks import structure

    root = tmp_path / "root"
    config = root / "configs/claude/config"
    config.mkdir(parents=True)
    (config / "value.yml").write_text("value: ok\n")
    monkeypatch.setattr(structure.shutil, "which", lambda _: "/fixture/yamllint")
    monkeypatch.setattr(
        structure.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], returncode, "", "diagnostic\n"
        ),
    )
    assert _run(structure.main, root, "lint.yaml.config", tmp_path / "out") == expected
