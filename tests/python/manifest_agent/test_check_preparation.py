"""Candidate-local preparation receipts, failure outcomes, and process cleanup."""

import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from manifest_agent.checks import PreparationSpec
from tests.python.manifest_agent.test_check_candidate import git, materialize
from tests.python.manifest_agent.test_check_candidate import source as source_fixture

source = source_fixture


def preparation(root, body=None):
    script = root / "generate.py"
    script.write_text(
        body
        or (
            "from pathlib import Path\n"
            "p = Path('.apm/skills'); p.mkdir(parents=True, exist_ok=True)\n"
            "count = p / 'count'; count.write_text(str(int(count.read_text()) + 1) if count.exists() else '1')\n"
            "(p / 'SKILL.md').write_text(Path('head-only.txt').read_text())\n"
        )
    )
    return PreparationSpec(
        "mirror",
        (sys.executable, "generate.py"),
        ".",
        ("generate.py", "head-only.txt"),
        (".apm/skills",),
        ("structure",),
        2.0,
        "python",
        "fixture",
    )


def test_preparation_generates_once_and_records_identities(source, tmp_path):
    from manifest_agent.checks import prepare_candidate

    spec = preparation(source[0])
    before = (source[0] / ".git/index").read_bytes()
    candidate = materialize(source, tmp_path)
    env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: prepare_candidate(candidate, (spec,), env), range(4))
        )
    assert [result[0].status for result in results] == ["PASS"] * 4
    assert (candidate.root / ".apm/skills/count").read_text() == "1"
    assert not (source[0] / ".apm").exists()
    assert (source[0] / ".git/index").read_bytes() == before
    receipt = json.loads(candidate.preparation_receipt.read_text())
    assert receipt["schema_version"] == 1
    assert receipt["tree_sha"] == candidate.tree_sha
    assert receipt["source_digest"] == candidate.source_digest
    entry = receipt["preparations"]["mirror"]
    assert entry["spec_digest"] and entry["inputs"]["head-only.txt"]["sha256"]
    assert entry["outputs"][".apm/skills/SKILL.md"]["sha256"]
    assert entry["outputs"][".apm/skills/SKILL.md"]["mode"] == 0o644


@pytest.mark.parametrize(
    "body,diagnostic",
    [
        ("raise SystemExit(9)\n", "exit"),
        ("import time; time.sleep(10)\n", "timeout"),
        ("print('no outputs')\n", "missing"),
        ("from pathlib import Path; Path('staged').write_text('changed')\n", "changed"),
        (
            "from pathlib import Path; Path('cache').mkdir(); Path('cache/extra').write_text('x')\n",
            "undeclared",
        ),
    ],
)
def test_preparation_failure_is_blocked(source, tmp_path, body, diagnostic):
    from manifest_agent.checks import prepare_candidate

    spec = replace(preparation(source[0], body), timeout_seconds=0.2)
    candidate = materialize(source, tmp_path)
    (result,) = prepare_candidate(candidate, (spec,), {"PATH": os.defpath})
    assert result.status == "BLOCKED"
    assert diagnostic in result.diagnostics.lower()
    assert not candidate.preparation_receipt.exists()


def test_preparation_requires_ignored_outputs_and_executable(source, tmp_path):
    from manifest_agent.checks import prepare_candidate

    spec = preparation(source[0])
    candidate = materialize(source, tmp_path)
    (result,) = prepare_candidate(candidate, (replace(spec, outputs=("visible",)),), {})
    assert result.status == "BLOCKED" and "ignored" in result.diagnostics
    (result,) = prepare_candidate(
        candidate, (replace(spec, argv=("/missing/executable",)),), {}
    )
    assert result.status == "BLOCKED"


def test_output_change_invalidates_receipt_and_spec_change_reruns(source, tmp_path):
    from manifest_agent.checks import prepare_candidate

    spec = preparation(source[0])
    candidate = materialize(source, tmp_path)
    assert prepare_candidate(candidate, (spec,), {})[0].status == "PASS"
    (candidate.root / ".apm/skills/SKILL.md").write_text("tampered")
    assert prepare_candidate(candidate, (spec,), {})[0].status == "PASS"
    assert (candidate.root / ".apm/skills/count").read_text() == "2"
    assert (
        prepare_candidate(candidate, (replace(spec, version="changed"),), {})[0].status
        == "PASS"
    )
    assert (candidate.root / ".apm/skills/count").read_text() == "3"


def test_process_capture_redacts_bounds_and_times_out(tmp_path):
    from manifest_agent.checks.process import run_argv

    script = tmp_path / "output.py"
    script.write_text(
        "import sys\nprint('password=fixture-value')\nsys.stdout.write('x'*100000)\nsys.stderr.write('y'*100000)\n"
    )
    # subprocess-env: exempt -- synthetic tmp_path script, no manifest_agent/tools import.
    result = run_argv(
        (sys.executable, str(script)), cwd=tmp_path, env={}, timeout_seconds=2
    )
    assert result.returncode == 0 and not result.timed_out
    assert "fixture-value" not in result.stdout
    assert len(result.stdout.encode()) <= 65536 and len(result.stderr.encode()) <= 65536
    script.write_text("import time; time.sleep(10)\n")
    # subprocess-env: exempt -- synthetic tmp_path script, no manifest_agent/tools import.
    result = run_argv(
        (sys.executable, str(script)), cwd=tmp_path, env={}, timeout_seconds=0.1
    )
    assert result.timed_out and result.duration_seconds < 2


def test_missing_declared_preparation_input_blocks(source, tmp_path):
    from manifest_agent.checks import prepare_candidate

    spec = replace(preparation(source[0]), inputs=("absent-input",))
    candidate = materialize(source, tmp_path)
    (result,) = prepare_candidate(candidate, (spec,), {})
    assert result.status == "BLOCKED" and "input" in result.diagnostics


def test_preparation_git_metadata_mutation_blocks(source, tmp_path):
    from manifest_agent.checks import prepare_candidate

    body = "from pathlib import Path\nPath('.git/config').write_text('[core]\\nrepositoryformatversion = 0\\n')\nPath('.apm/skills').mkdir(parents=True)\n"
    spec = preparation(source[0], body)
    candidate = materialize(source, tmp_path)
    (result,) = prepare_candidate(candidate, (spec,), {})
    assert result.status == "BLOCKED"


def test_preparation_receipt_cannot_cross_candidates(source, tmp_path):
    from manifest_agent.checks import materialize_candidate, prepare_candidate

    spec = preparation(source[0])
    first = materialize(source, tmp_path)
    assert prepare_candidate(first, (spec,), {})[0].status == "PASS"
    second = materialize_candidate(*source, tmp_path / "second")
    second.preparation_receipt.write_bytes(first.preparation_receipt.read_bytes())
    assert prepare_candidate(second, (spec,), {})[0].status == "PASS"
    assert (second.root / ".apm/skills/count").read_text() == "1"


@pytest.mark.parametrize("receipt", ["[]", '{"schema_version":1}', "not JSON"])
def test_invalid_receipt_is_blocked_without_exception(source, tmp_path, receipt):
    from manifest_agent.checks import prepare_candidate

    spec = preparation(source[0])
    candidate = materialize(source, tmp_path)
    candidate.preparation_receipt.write_text(receipt)
    (result,) = prepare_candidate(candidate, (spec,), {})
    assert result.status == "BLOCKED"


def test_preparation_ignored_inputs_and_output_modes_invalidate_receipt(
    source, tmp_path
):
    from manifest_agent.checks import prepare_candidate

    spec = replace(preparation(source[0]), inputs=("generate.py", "cache/input"))
    candidate = materialize(source, tmp_path)
    (candidate.root / "cache").mkdir()
    (candidate.root / "cache/input").write_text("one")
    assert prepare_candidate(candidate, (spec,), {})[0].status == "PASS"
    (candidate.root / "cache/input").write_text("two")
    assert prepare_candidate(candidate, (spec,), {})[0].status == "PASS"
    (candidate.root / ".apm/skills/SKILL.md").chmod(0o600)
    assert prepare_candidate(candidate, (spec,), {})[0].status == "PASS"
    assert (candidate.root / ".apm/skills/count").read_text() == "3"


def test_clean_checkout_preparation_only_generates_inside_candidate(source, tmp_path):
    from manifest_agent.checks import prepare_candidate

    spec = preparation(source[0])
    git(source[0], "add", "-A")
    git(source[0], "commit", "-qm", "clean fixture")
    before = (source[0] / ".git/index").read_bytes()
    assert not git(source[0], "status", "--porcelain=v1")
    candidate = materialize(source, tmp_path)
    assert prepare_candidate(candidate, (spec,), {})[0].status == "PASS"
    assert (candidate.root / ".apm/skills/SKILL.md").is_file()
    assert not (source[0] / ".apm").exists()
    assert (source[0] / ".git/index").read_bytes() == before


def test_preparation_cannot_change_output_ancestor_mode(source, tmp_path):
    from manifest_agent.checks import prepare_candidate

    spec = preparation(
        source[0],
        "from pathlib import Path\nPath('.apm/skills').mkdir()\nPath('.apm').chmod(0o777)\n",
    )
    candidate = materialize(source, tmp_path)
    (candidate.root / ".apm").mkdir(mode=0o700)
    (result,) = prepare_candidate(candidate, (spec,), {})
    assert result.status == "BLOCKED" and "undeclared" in result.diagnostics


def test_process_timeout_kills_descendants_and_ignoring_leader(tmp_path):
    from manifest_agent.checks.process import run_argv

    script = tmp_path / "fork.py"
    script.write_text(
        "import os,signal,time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "if os.fork() == 0:\n time.sleep(.5); Path('survived').write_text('bad')\n"
        "else:\n time.sleep(10)\n"
    )
    # subprocess-env: exempt -- synthetic tmp_path script, no manifest_agent/tools import.
    result = run_argv(
        (sys.executable, str(script)), cwd=tmp_path, env={}, timeout_seconds=0.15
    )
    assert result.timed_out
    assert result.returncode is not None and result.duration_seconds < 2
    time.sleep(0.6)
    assert not (tmp_path / "survived").exists()


def test_process_signal_denial_is_reported_as_error(tmp_path, monkeypatch):
    import manifest_agent.checks.process as implementation

    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(.3)\n")
    original = implementation.os.killpg

    def deny_signal(pid, sig):
        monkeypatch.setattr(implementation.os, "killpg", original)
        raise PermissionError("fixture signal permission denied")

    monkeypatch.setattr(implementation.os, "killpg", deny_signal)
    # subprocess-env: exempt -- synthetic tmp_path script, no manifest_agent/tools import.
    result = implementation.run_argv(
        (sys.executable, str(script)), cwd=tmp_path, env={}, timeout_seconds=0.05
    )
    assert result.error and "permission" in result.error
    assert result.returncode is not None


def test_preparation_receipt_symlink_blocks_without_external_write(source, tmp_path):
    from manifest_agent.checks import prepare_candidate

    spec = preparation(source[0])
    candidate = materialize(source, tmp_path)
    external = tmp_path / "external.json"
    external.write_text("untouched")
    candidate.preparation_receipt.symlink_to(external)
    assert prepare_candidate(candidate, (spec,), {})[0].status == "BLOCKED"
    assert external.read_text() == "untouched"


def test_failed_rerun_cannot_reuse_old_completed_receipt(source, tmp_path):
    from manifest_agent.checks import prepare_candidate

    body = (
        "from pathlib import Path\n"
        "p = Path('.apm/skills'); p.mkdir(parents=True, exist_ok=True)\n"
        "count = p / 'count'; existed = count.exists(); count.write_text('1')\n"
        "(p / 'SKILL.md').write_text(Path('head-only.txt').read_text())\n"
        "raise SystemExit(9 if existed else 0)\n"
    )
    spec = preparation(source[0], body)
    candidate = materialize(source, tmp_path)
    assert prepare_candidate(candidate, (spec,), {})[0].status == "PASS"
    (candidate.root / ".apm/skills/count").write_text("tampered")
    assert prepare_candidate(candidate, (spec,), {})[0].status == "BLOCKED"
    assert prepare_candidate(candidate, (spec,), {})[0].status == "BLOCKED"


def test_preparation_nested_git_metadata_write_is_blocked(source, tmp_path):
    from manifest_agent.checks import prepare_candidate

    spec = preparation(
        source[0],
        "from pathlib import Path\nPath('.git/.git').mkdir()\nPath('.git/.git/payload').write_text('hidden')\nPath('.apm/skills').mkdir(parents=True)\n",
    )
    candidate = materialize(source, tmp_path)
    (result,) = prepare_candidate(candidate, (spec,), {})
    assert result.status == "BLOCKED"
    assert not candidate.preparation_receipt.exists()


def test_process_group_eperm_after_leader_exit_is_cleanup_failure(monkeypatch):
    import manifest_agent.checks.process as implementation

    class ExitedLeader:
        pid = 12345

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            return None

    def deny_group(pid, sig):
        raise PermissionError("group permission denied; descendant remains")

    monkeypatch.setattr(implementation.os, "killpg", deny_group)
    assert "permission denied" in implementation._cleanup(ExitedLeader())
    with pytest.raises(PermissionError):
        implementation._kill_group(ExitedLeader(), signal.SIGTERM)
