"""Adversarial provenance and resource-bound tests for version probes."""

from __future__ import annotations

import base64
import errno
import hashlib
import os
import subprocess
import sys
import threading
import time
import venv
from pathlib import Path

import pytest

from manifest_agent.checks.runner import _preflight_error, _preflight_tool

ROOT = Path(__file__).resolve().parents[3]
VERSION_ADAPTER = ROOT / "tools/project_checks/tool_versions.py"


def _adapter(
    *argv: str,
    cwd: Path,
    path: str | None = None,
    environment: dict[str, str] | None = None,
    python: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(cwd),
        "PATH": path or os.environ["PATH"],
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(environment or {})
    return subprocess.run(
        [str(python or sys.executable), str(VERSION_ADAPTER), *argv],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _python_token() -> str:
    return f"python={sys.version_info.major}.{sys.version_info.minor}"


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


def test_file_sha_holds_parent_descriptor_during_harmless_replacement(
    tmp_path: Path, monkeypatch
):
    from tools.project_checks import tool_versions

    parent = tmp_path / "nested"
    parent.mkdir()
    target = parent / "reviewed.py"
    original = b"reviewed bytes\n"
    target.write_bytes(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "reviewed.py").write_bytes(b"replacement bytes\n")
    opened_parent = threading.Event()
    replaced_parent = threading.Event()
    real_open = os.open

    def replace_parent() -> None:
        assert opened_parent.wait(timeout=2)
        parent.rename(tmp_path / "held-parent")
        parent.symlink_to(outside, target_is_directory=True)
        replaced_parent.set()

    def coordinated_open(path, *args, **kwargs):
        if path == target:
            opened_parent.set()
            assert replaced_parent.wait(timeout=2)
            return real_open(path, *args, **kwargs)
        descriptor = real_open(path, *args, **kwargs)
        if path == "nested" and kwargs.get("dir_fd") is not None:
            opened_parent.set()
            assert replaced_parent.wait(timeout=2)
        return descriptor

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tool_versions.os, "open", coordinated_open)
    replacement = threading.Thread(target=replace_parent)
    replacement.start()
    digest = tool_versions._file_digest("nested/reviewed.py")
    replacement.join(timeout=2)

    assert not replacement.is_alive()
    assert digest == hashlib.sha256(original).hexdigest()


def test_console_distribution_ignores_candidate_pythonpath_metadata(tmp_path: Path):
    console = tmp_path / "fixture-tool"
    console.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    console.chmod(0o755)
    metadata = tmp_path / "fixture_package-9.9.9.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fixture-package\nVersion: 9.9.9\n",
        encoding="utf-8",
    )
    (metadata / "entry_points.txt").write_text(
        "[console_scripts]\nfixture-tool = fixture_package:main\n",
        encoding="utf-8",
    )
    (metadata / "RECORD").write_text(
        "fixture-tool,,\nfixture_package-9.9.9.dist-info/METADATA,,\n",
        encoding="utf-8",
    )

    result = _adapter(
        "console-distribution",
        "fixture-package",
        "fixture-tool",
        cwd=tmp_path,
        path=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        environment={"PYTHONPATH": str(tmp_path)},
    )

    assert result.returncode == 3
    assert "distribution is not installed" in result.stderr


def test_command_probe_rejects_contained_unrelated_executable(tmp_path: Path):
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    fake = fake_dir / "bash"
    fake.write_text("#!/bin/sh\necho 'GNU bash, version 5.2.0'\n", encoding="utf-8")
    fake.chmod(0o755)

    result = _adapter(
        "command-version",
        "bash",
        "--executable",
        "fake/bash",
        cwd=tmp_path,
    )

    assert result.returncode == 3
    assert "does not match probe" in result.stderr


def test_probe_output_excess_terminates_noisy_invalid_utf8_family(tmp_path: Path):
    fake = tmp_path / "bash"
    marker = tmp_path / "continued"
    child_pid = tmp_path / "child.pid"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    time.sleep(10)\n"
        "else:\n"
        f"    Path({str(child_pid)!r}).write_text(str(child))\n"
        "    os.write(sys.stdout.fileno(), b'GNU bash, version 5.2.0\\n' + b'x' * 10000 + b'\\xff')\n"
        "    os.write(sys.stderr.fileno(), b'y' * 10000 + b'\\xfe')\n"
        "    time.sleep(1)\n"
        f"    open({str(marker)!r}, 'w').close()\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    started = time.monotonic()
    result = _adapter(
        "command-version",
        "bash",
        cwd=tmp_path,
        path=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
    )

    assert result.returncode == 3
    assert "output exceeded limit" in result.stderr
    assert time.monotonic() - started < 0.8
    assert not marker.exists()
    pid = int(child_pid.read_text())
    deadline = time.monotonic() + 1
    while _pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _pid_exists(pid)


def test_probe_timeout_terminates_and_reaps_process_family(tmp_path: Path):
    from tools.project_checks import tool_versions

    sleeper = tmp_path / "sleeper"
    child_pid = tmp_path / "timeout-child.pid"
    sleeper.write_text(
        f"#!{sys.executable}\n"
        "import os, time\n"
        "from pathlib import Path\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    time.sleep(10)\n"
        "else:\n"
        f"    Path({str(child_pid)!r}).write_text(str(child))\n"
        "    time.sleep(10)\n",
        encoding="utf-8",
    )
    sleeper.chmod(0o755)

    with pytest.raises(tool_versions.ProbeError, match="timed out"):
        tool_versions._run_probe([str(sleeper)], timeout_seconds=1.0)

    pid = int(child_pid.read_text())
    deadline = time.monotonic() + 1
    while _pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _pid_exists(pid)


def _provision_python_tool(
    tmp_path: Path, distribution: str, version: str, console: str, output: str
) -> tuple[Path, Path, str, Path, Path, Path]:
    environment = tmp_path / "environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / "bin/python"
    scripts = environment / "bin"
    purelib = Path(
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    normalized = distribution.replace("-", "_")
    metadata = purelib / f"{normalized}-{version}.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n",
        encoding="utf-8",
    )
    (metadata / "entry_points.txt").write_text(
        f"[console_scripts]\n{console} = {normalized}:main\n",
        encoding="utf-8",
    )
    module = purelib / f"{normalized}.py"
    module.write_text(
        f"def main():\n    print({output!r})\n    return 0\n", encoding="utf-8"
    )
    executable = scripts / console
    executable.write_text(
        f"#!{python}\nfrom {normalized} import main\n"
        "if __name__ == '__main__':\n    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    _refresh_record(metadata, purelib, executable, module)
    return python, executable, str(scripts), module, metadata, purelib


def _refresh_record(
    metadata: Path,
    purelib: Path,
    executable: Path,
    module: Path,
    extra: tuple[Path, ...] = (),
) -> None:
    rows = []
    for path in (
        executable,
        module,
        *extra,
        metadata / "METADATA",
        metadata / "entry_points.txt",
    ):
        content = path.read_bytes()
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        rows.append(
            f"{os.path.relpath(path, purelib)},sha256={digest.decode()},{len(content)}"
        )
    rows.append(f"{metadata.name}/RECORD,,")
    (metadata / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_console_provenance_rejects_symlinked_metadata_and_recorded_file_tamper(
    tmp_path: Path,
):
    python, executable, path, module, metadata, _ = _provision_python_tool(
        tmp_path, "fixture-package", "1.2.3", "yamllint", "yamllint 1.38.0"
    )
    argv = (
        "python-wrapper",
        "--console-command",
        "fixture-package",
        "yamllint",
        "yamllint",
    )
    genuine = _adapter(*argv, cwd=tmp_path, path=path, python=python)
    module.write_text("def main(): return 0\n", encoding="utf-8")
    tampered_module = _adapter(*argv, cwd=tmp_path, path=path, python=python)
    module.write_text(
        "def main():\n    print('yamllint 1.38.0')\n    return 0\n", encoding="utf-8"
    )
    _refresh_record(metadata, Path(module.parent), executable, module)
    executable.write_text(executable.read_text() + "# tampered\n", encoding="utf-8")
    tampered_console = _adapter(*argv, cwd=tmp_path, path=path, python=python)
    _refresh_record(metadata, Path(module.parent), executable, module)
    original_metadata = (metadata / "METADATA").read_bytes()
    outside = tmp_path / "outside-metadata"
    outside.write_bytes(original_metadata)
    (metadata / "METADATA").unlink()
    (metadata / "METADATA").symlink_to(outside)
    symlinked_metadata = _adapter(*argv, cwd=tmp_path, path=path, python=python)

    assert genuine.returncode == 0, genuine.stderr
    assert tampered_module.returncode == 3
    assert tampered_console.returncode == 3
    assert symlinked_metadata.returncode == 3


def test_console_interpreter_must_name_the_exact_venv(tmp_path: Path):
    python, executable, path, module, metadata, purelib = _provision_python_tool(
        tmp_path, "fixture-package", "1.2.3", "yamllint", "yamllint 1.38.0"
    )
    other = tmp_path / "other-environment"
    venv.EnvBuilder(with_pip=False).create(other)
    lines = executable.read_text(encoding="utf-8").splitlines()
    lines[0] = f"#!{other / 'bin/python'}"
    executable.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _refresh_record(metadata, purelib, executable, module)

    result = _adapter(
        "console-distribution",
        "fixture-package",
        "yamllint",
        cwd=tmp_path,
        path=path,
        python=python,
    )

    assert result.returncode == 3
    assert "trusted interpreter" in result.stderr


def test_distribution_only_probe_authenticates_recorded_metadata(tmp_path: Path):
    python, _, path, _, metadata, _ = _provision_python_tool(
        tmp_path, "pyyaml-like", "6.0.3", "yamllint", "yamllint 1.38.0"
    )
    (metadata / "METADATA").write_text(
        (metadata / "METADATA").read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )

    result = _adapter(
        "python-wrapper",
        "--distribution",
        "pyyaml-like",
        cwd=tmp_path,
        path=path,
        python=python,
    )

    assert result.returncode == 3


def test_dotted_entry_point_is_authenticated_without_importing_parent(tmp_path: Path):
    python, executable, path, old_module, metadata, purelib = _provision_python_tool(
        tmp_path, "fixture-package", "1.2.3", "fixture-tool", "fixture 1.2.3"
    )
    marker = tmp_path / "parent-imported"
    package = purelib / "fixture_parent"
    package.mkdir()
    parent = package / "__init__.py"
    parent.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    module = package / "tool.py"
    module.write_text("def main(): return 0\n", encoding="utf-8")
    old_module.unlink()
    (metadata / "entry_points.txt").write_text(
        "[console_scripts]\nfixture-tool = fixture_parent.tool:main\n",
        encoding="utf-8",
    )
    executable.write_text(
        f"#!{python}\nfrom fixture_parent.tool import main\n"
        "if __name__ == '__main__':\n    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    _refresh_record(metadata, purelib, executable, module, (parent,))

    genuine = _adapter(
        "console-distribution",
        "fixture-package",
        "fixture-tool",
        cwd=tmp_path,
        path=path,
        python=python,
    )
    parent.write_text(parent.read_text(encoding="utf-8") + "# tampered\n")
    tampered = _adapter(
        "console-distribution",
        "fixture-package",
        "fixture-tool",
        cwd=tmp_path,
        path=path,
        python=python,
    )

    assert genuine.returncode == 0, genuine.stderr
    assert not marker.exists()
    assert tampered.returncode == 3


@pytest.mark.parametrize(
    "case",
    (
        (
            "shellcheck-py",
            "0.11.0.1",
            "shellcheck",
            "ShellCheck - shell script analysis tool\\nversion: 0.11.0",
            "command:shellcheck=0.11.0",
        ),
        (
            "yamllint",
            "1.38.0",
            "yamllint",
            "yamllint 1.38.0",
            "command:yamllint=1.38.0",
        ),
    ),
)
def test_console_command_probe_blocks_wrong_and_missing_inner_tool(
    tmp_path: Path,
    case: tuple[str, str, str, str, str],
):
    distribution, distribution_version, console, correct_output, token = case
    python, executable, path, module, metadata, purelib = _provision_python_tool(
        tmp_path, distribution, distribution_version, console, f"{console} 0.0.0"
    )
    argv = (
        "python-wrapper",
        "--console-command",
        distribution,
        console,
        console,
    )
    expected_version = (
        f"{_python_token()};distribution:{distribution}={distribution_version};{token}"
    )
    wrong = _adapter(*argv, cwd=tmp_path, path=path, python=python)
    wrong_outcome = _preflight_tool(
        {
            "executable": console,
            "version_argv": (str(python), "-I", str(VERSION_ADAPTER), *argv),
            "expected_version": expected_version,
            "required_modules": (),
        },
        cwd=tmp_path,
        env={"PATH": path},
    )
    console_content = executable.read_text(encoding="utf-8")
    executable.unlink()
    missing = _adapter(*argv, cwd=tmp_path, path=path, python=python)
    executable.write_text(console_content, encoding="utf-8")
    executable.chmod(0o755)
    module.write_text(
        f"def main():\n    print({correct_output!r})\n    return 0\n", encoding="utf-8"
    )
    _refresh_record(metadata, purelib, executable, module)
    genuine = _adapter(*argv, cwd=tmp_path, path=path, python=python)

    assert wrong.returncode in (0, 3)
    assert not wrong_outcome[1]
    assert _preflight_error(wrong_outcome)
    assert missing.returncode == 3
    assert genuine.returncode == 0, genuine.stderr
    assert f"distribution:{distribution}={distribution_version}" in genuine.stdout
    assert token in genuine.stdout
