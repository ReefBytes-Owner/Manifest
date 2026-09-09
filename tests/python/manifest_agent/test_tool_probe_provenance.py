"""Distribution-only metadata provenance regressions."""

import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.python.manifest_agent.test_tool_version_security import (
    VERSION_ADAPTER,
    _adapter,
    _provision_python_tool,
    _refresh_record,
)
from tools.project_checks import tool_versions


def test_isolated_adapter_does_not_expose_helper_siblings_to_imports(tmp_path: Path):
    helper = tmp_path / "tools/project_checks"
    helper.mkdir(parents=True)
    for name in (
        "tool_versions.py",
        "tool_probe_process.py",
        "tool_probe_metadata.py",
        "tool_probe_provenance.py",
    ):
        shutil.copy2(VERSION_ADAPTER.with_name(name), helper / name)
    marker = tmp_path / "sibling-imported"
    for name in ("csv.py", "ast.py"):
        (helper / name).write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n",
            encoding="utf-8",
        )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(helper / "tool_versions.py"),
            "command-version",
            "bash",
        ],
        cwd=tmp_path,
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_console_child_drops_candidate_python_injection_environment(tmp_path: Path):
    python, _, path, _, _, _ = _provision_python_tool(
        tmp_path, "fixture-package", "1.2.3", "yamllint", "yamllint 1.38.0"
    )
    marker = tmp_path / "candidate-module-loaded"
    (tmp_path / "fixture_package.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
        "def main(): print('yamllint 9.9.9')\n",
        encoding="utf-8",
    )
    result = _adapter(
        "python-wrapper",
        "--console-command",
        "fixture-package",
        "yamllint",
        "yamllint",
        cwd=tmp_path,
        path=path,
        python=python,
        environment={
            "PYTHONPATH": str(tmp_path),
            "PYTHONSTARTUP": str(tmp_path / "bad.py"),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "command:yamllint=1.38.0" in result.stdout
    assert not marker.exists()


def test_malformed_record_is_typed_block_without_traceback(tmp_path: Path):
    python, _, path, _, metadata, _ = _provision_python_tool(
        tmp_path, "pyyaml-like", "6.0.3", "yamllint", "yamllint 1.38.0"
    )
    (metadata / "RECORD").write_text("pyyaml_like.py,sha256=broken,not-a-size\n")
    result = _adapter(
        "python-wrapper",
        "--distribution",
        "pyyaml-like",
        cwd=tmp_path,
        path=path,
        python=python,
    )
    assert result.returncode == 3
    assert "BLOCKED:" in result.stderr
    assert "Traceback" not in result.stderr


def test_oversized_record_blocks_before_console_execution(tmp_path: Path):
    python, executable, path, module, metadata, purelib = _provision_python_tool(
        tmp_path, "fixture-package", "1.2.3", "yamllint", "yamllint 1.38.0"
    )
    marker = tmp_path / "console-executed"
    module.write_text(
        "from pathlib import Path\n"
        "def main():\n"
        f"    Path({str(marker)!r}).write_text('ran')\n"
        "    print('yamllint 1.38.0')\n"
        "    return 0\n",
        encoding="utf-8",
    )
    _refresh_record(metadata, purelib, executable, module)
    (metadata / "RECORD").write_text('"' + "x" * 140_000, encoding="utf-8")

    result = _adapter(
        "python-wrapper",
        "--console-command",
        "fixture-package",
        "yamllint",
        "yamllint",
        cwd=tmp_path,
        path=path,
        python=python,
    )

    assert result.returncode == 3
    assert "BLOCKED:" in result.stderr
    assert "Traceback" not in result.stderr
    assert not marker.exists()


def test_record_total_size_is_bounded(tmp_path: Path):
    python, _, path, _, metadata, _ = _provision_python_tool(
        tmp_path, "pyyaml-like", "6.0.3", "yamllint", "yamllint 1.38.0"
    )
    (metadata / "RECORD").write_text("untrusted.py,,\n" * 300_000, encoding="utf-8")

    result = _adapter(
        "python-wrapper",
        "--distribution",
        "pyyaml-like",
        cwd=tmp_path,
        path=path,
        python=python,
    )

    assert result.returncode == 3
    assert "provenance file exceeds size limit" in result.stderr
    assert "Traceback" not in result.stderr


def test_distribution_discovery_never_reads_lazy_metadata_apis(
    tmp_path: Path, monkeypatch
):
    _, _, _, _, metadata, purelib = _provision_python_tool(
        tmp_path, "fixture-package", "1.2.3", "yamllint", "yamllint 1.38.0"
    )

    class Candidate:
        _path = metadata

        def __getattr__(self, name):
            if name in {"metadata", "files", "entry_points", "version"}:
                raise AssertionError(f"lazy API accessed: {name}")
            raise AttributeError(name)

    monkeypatch.setattr(
        tool_versions.importlib.metadata,
        "distributions",
        lambda **_kwargs: (Candidate(),),
    )
    monkeypatch.setattr(
        tool_versions, "_trusted_package_roots", lambda: (purelib.resolve(),)
    )

    distribution = tool_versions._trusted_distribution("fixture-package")

    assert distribution.name == "fixture-package"
    assert distribution.version == "1.2.3"


@pytest.mark.parametrize("mutation", ("replace", "grow"))
def test_record_snapshot_survives_replacement_after_descriptor_read(
    tmp_path: Path, monkeypatch, mutation: str
):
    _, _, _, _, metadata, purelib = _provision_python_tool(
        tmp_path, "fixture-package", "1.2.3", "yamllint", "yamllint 1.38.0"
    )
    record = metadata / "RECORD"
    original = record.read_bytes()
    duplicate = next(
        line for line in original.splitlines(keepends=True) if b"/METADATA," in line
    )
    identity = (record.stat().st_dev, record.stat().st_ino)
    raw_distribution = importlib.metadata.PathDistribution(metadata)
    real_read = tool_versions._PROVENANCE.os.read
    mutated = False

    def replace_after_eof(descriptor, count):
        nonlocal mutated
        data = real_read(descriptor, count)
        status = os.fstat(descriptor)
        if not data and not mutated and (status.st_dev, status.st_ino) == identity:
            mutated = True
            if mutation == "replace":
                record.rename(metadata / "RECORD.held")
                record.write_bytes(original + duplicate)
            else:
                with record.open("ab") as stream:
                    stream.write(duplicate)
        return data

    monkeypatch.setattr(tool_versions._PROVENANCE.os, "read", replace_after_eof)
    monkeypatch.setattr(
        tool_versions.importlib.metadata,
        "distributions",
        lambda **_kwargs: (raw_distribution,),
    )
    monkeypatch.setattr(
        tool_versions, "_trusted_package_roots", lambda: (purelib.resolve(),)
    )

    component = tool_versions._distribution_component("fixture-package")

    assert mutated
    assert component == "distribution:fixture-package=1.2.3"


def test_oversized_metadata_blocks_before_console_execution(tmp_path: Path):
    python, executable, path, module, metadata, purelib = _provision_python_tool(
        tmp_path, "fixture-package", "1.2.3", "yamllint", "yamllint 1.38.0"
    )
    marker = tmp_path / "console-executed"
    module.write_text(
        "from pathlib import Path\n"
        "def main():\n"
        f"    Path({str(marker)!r}).write_text('ran')\n"
        "    return 0\n",
        encoding="utf-8",
    )
    _refresh_record(metadata, purelib, executable, module)
    (metadata / "METADATA").write_bytes(
        b"Metadata-Version: 2.1\nName: fixture-package\nVersion: 1.2.3\nX: "
        + b"x" * (4 * 1024 * 1024)
    )

    result = _adapter(
        "python-wrapper",
        "--console-command",
        "fixture-package",
        "yamllint",
        "yamllint",
        cwd=tmp_path,
        path=path,
        python=python,
    )

    assert result.returncode == 3
    assert "provenance file exceeds size limit" in result.stderr
    assert "Traceback" not in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    "mutation",
    ("dist-info-symlink", "metadata-symlink", "record-symlink", "record-tamper"),
)
def test_distribution_only_rejects_metadata_provenance_mutation(
    tmp_path: Path, mutation: str
):
    python, _, path, _, metadata, _ = _provision_python_tool(
        tmp_path, "pyyaml-like", "6.0.3", "yamllint", "yamllint 1.38.0"
    )
    if mutation == "dist-info-symlink":
        held = metadata.with_name(metadata.name + "-held")
        metadata.rename(held)
        metadata.symlink_to(held, target_is_directory=True)
    elif mutation == "record-tamper":
        (metadata / "RECORD").write_text("untrusted.py,,\n", encoding="utf-8")
    else:
        name = "METADATA" if mutation == "metadata-symlink" else "RECORD"
        target = metadata / name
        held = tmp_path / f"held-{name}"
        target.rename(held)
        target.symlink_to(held)

    result = _adapter(
        "python-wrapper",
        "--distribution",
        "pyyaml-like",
        cwd=tmp_path,
        path=path,
        python=python,
    )

    assert result.returncode == 3
    assert "BLOCKED:" in result.stderr
