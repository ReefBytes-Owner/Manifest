import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNNER = (
    Path(__file__).resolve().parents[2]
    / "plugins/manifest-workspace/skills/pr-smoke/scripts/run_pr_regression.sh"
)


class RegressionFixture:
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="manifest-check-contract-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.repo = self.root / "repo"
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        self.logs = self.root / "logs"
        for directory in (self.repo, self.bin, self.home, self.logs):
            directory.mkdir()
        self.runner_bash = os.environ.get("PR_SMOKE_TEST_BASH") or shutil.which("bash")
        self.assertIsNotNone(self.runner_bash, "required fixture tool: bash")
        for name in ("git",):
            resolved = shutil.which(name)
            self.assertIsNotNone(resolved, f"required fixture tool: {name}")
            (self.bin / name).symlink_to(resolved)
        self.env = {
            "PATH": str(self.bin),
            "HOME": str(self.home),
            "TMPDIR": str(self.root),
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
            "STUB_LOG_DIR": str(self.logs),
            "PR_SMOKE_BASH": str(self.runner_bash),
        }
        self.git("init", "-q")
        for name in ("shellcheck", "markdownlint-cli2", "bats"):
            self.stub(name)
        self.python_stub()
        self.fixture_file("README.md", "fixture\n")
        self.fixture_file("AGENTS.md", "fixture\n")
        self.fixture_file("CLAUDE.md", "fixture\n")
        self.fixture_file("docs/check.md", "fixture\n")
        self.fixture_file("tests/python/test_example.py", "# fixture\n")
        self.fixture_file("tests/bats/example.bats", "# fixture\n")
        for relative in (
            "tests/lint/check_array_expansion.sh",
            "tests/lint/check_bats_assertions.sh",
            "scripts/generate_commands_doc.py",
            "scripts/a-good.sh",
        ):
            self.fixture_file(relative, "#!/bin/sh\nexit 0\n", executable=True)
        self.git("add", ".")

    def git(self, *args):
        return subprocess.run(
            [str(self.bin / "git"), *args],
            cwd=self.repo,
            env=self.env,
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )

    def fixture_file(self, relative, content, executable=False):
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if executable:
            target.chmod(0o755)
        return target

    def stub(self, name, status=0):
        target = self.bin / name
        target.write_text(
            "#!/bin/sh\n"
            f'printf \'%s|%s\\n\' "$PWD" "$*" >> "$STUB_LOG_DIR/{name}.log"\n'
            f"exit {status}\n",
            encoding="utf-8",
        )
        target.chmod(0o755)

    def python_stub(self):
        target = self.bin / "python3"
        target.write_text(
            "#!/bin/sh\n"
            'printf \'%s|%s\\n\' "$PWD" "$*" >> "$STUB_LOG_DIR/python3.log"\n'
            'if [ "${1:-}" = \'-c\' ]; then exit "${PYTEST_IMPORT_STATUS:-0}"; fi\n'
            'if [ "${1:-}" = \'-m\' ] && [ "${2:-}" = pytest ]; then '
            'exit "${PYTEST_RUN_STATUS:-0}"; fi\n'
            "exit 97\n",
            encoding="utf-8",
        )
        target.chmod(0o755)

    def fail_git_ls_files(self):
        target = self.bin / "git"
        resolved = target.resolve()
        target.unlink()
        target.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = 'ls-files' ]; then exit 71; fi\n"
            f'exec {shlex.quote(str(resolved))} "$@"\n',
            encoding="utf-8",
        )
        target.chmod(0o755)

    def calls(self, name):
        path = self.logs / f"{name}.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def run_runner(self, *args, extra_env=None):
        env = {**self.env, **(extra_env or {})}
        return subprocess.run(
            [str(self.runner_bash), str(RUNNER), *args],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )


class QuickAndPythonContract(RegressionFixture, unittest.TestCase):
    def test_valid_full_subset_passes(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("not full CI verification", result.stdout)

    def test_quick_names_its_actual_scope(self):
        result = self.run_runner("--quick")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("staged + unstaged whitespace", result.stdout)
        self.assertIn("untracked files excluded", result.stdout)

    def test_quick_rejects_unstaged_whitespace_error(self):
        self.fixture_file("README.md", "fixture with trailing space \n")
        result = self.run_runner("--quick")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("unstaged whitespace | FAIL", result.stdout)

    def test_quick_rejects_staged_whitespace_error(self):
        self.fixture_file("README.md", "fixture with trailing space \n")
        self.git("add", "README.md")
        result = self.run_runner("--quick")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("staged whitespace | FAIL", result.stdout)

    def test_quick_explicitly_excludes_untracked_whitespace(self):
        self.fixture_file("UNTRACKED.md", "untracked trailing space \n")
        result = self.run_runner("--quick")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("untracked files excluded", result.stdout)

    def test_pytest_exit_one_is_failure(self):
        result = self.run_runner(extra_env={"PYTEST_RUN_STATUS": "1"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("python tests | FAIL", result.stdout)

    def test_pytest_collection_error_is_failure(self):
        result = self.run_runner(extra_env={"PYTEST_RUN_STATUS": "2"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("python tests | FAIL", result.stdout)

    def test_missing_pytest_module_is_blocked(self):
        result = self.run_runner(extra_env={"PYTEST_IMPORT_STATUS": "1"})
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("python tests | BLOCKED", result.stdout)


class ToolAvailabilityContract(RegressionFixture, unittest.TestCase):
    def test_python_suite_invocation_and_cwd_are_exact(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls("python3")
        self.assertTrue(
            any(line.endswith("|-m pytest tests/python/ -q") for line in calls)
        )
        self.assertTrue(all(line.startswith(f"{self.repo}|") for line in calls))

    def test_missing_required_tool_is_blocked(self):
        (self.bin / "markdownlint-cli2").unlink()
        result = self.run_runner()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("BLOCKED", result.stdout)
        self.assertNotIn("Verdict: PASS", result.stdout)

    def test_unavailable_selected_bash_is_blocked(self):
        result = self.run_runner(extra_env={"PR_SMOKE_BASH": "missing-syntax-bash"})
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(
            "| shell syntax | BLOCKED (missing missing-syntax-bash) |",
            result.stdout,
        )
        self.assertNotIn("Verdict: PASS", result.stdout)

    def test_missing_git_is_blocked(self):
        (self.bin / "git").unlink()
        result = self.run_runner()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("| Git | BLOCKED", result.stdout)
        self.assertNotIn("Verdict: PASS", result.stdout)

    def test_failed_generator_discovery_is_blocked(self):
        self.fail_git_ls_files()
        result = self.run_runner()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("command generator discovery | BLOCKED", result.stdout)
        self.assertNotIn("Verdict: PASS", result.stdout)

    def test_full_discovers_generator_without_head(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("command guide drift | PASS", result.stdout)
        self.assertIn("shell syntax | PASS", result.stdout)


class ShellScopeContract(RegressionFixture, unittest.TestCase):
    def test_explicit_syntax_interpreter_gets_per_file_n_invocations(self):
        self.stub("selected-bash")
        self.fixture_file("scripts/z-second.sh", "#!/bin/sh\nexit 0\n", executable=True)
        result = self.run_runner(
            extra_env={"PR_SMOKE_BASH": str(self.bin / "selected-bash")}
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.calls("selected-bash"),
            [
                f"{self.repo}|-n scripts/a-good.sh",
                f"{self.repo}|-n scripts/z-second.sh",
            ],
        )

    def test_no_shell_scripts_fails_shell_syntax_gate(self):
        (self.repo / "scripts/a-good.sh").unlink()
        result = self.run_runner()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("| shell syntax | FAIL |", result.stdout)

    def test_later_shell_syntax_error_fails(self):
        self.fixture_file("scripts/z-bad.sh", "#!/bin/sh\nif\n")
        result = self.run_runner()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("shell syntax | FAIL", result.stdout)

    def test_space_in_shell_filename_is_preserved(self):
        self.fixture_file("scripts/z bad.sh", "#!/bin/sh\nif\n")
        result = self.run_runner()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_newline_in_shell_filename_is_preserved(self):
        self.fixture_file("scripts/z\nbad.sh", "#!/bin/sh\nif\n")
        result = self.run_runner()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_syntax_check_never_executes_script(self):
        self.fixture_file("scripts/z-do-not-run.sh", "#!/bin/sh\nexit 73\n")
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_check_invocations_have_exact_scope_and_cwd(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        shellcheck = self.calls("shellcheck")
        self.assertEqual(len(shellcheck), 1)
        self.assertTrue(shellcheck[0].startswith(f"{self.repo}|"))
        self.assertIn("-S warning", shellcheck[0])
        self.assertIn("scripts/a-good.sh", shellcheck[0])
        markdown = self.calls("markdownlint-cli2")
        self.assertEqual(len(markdown), 1)
        self.assertTrue(markdown[0].startswith(f"{self.repo}|"))
        for expected in ("AGENTS.md", "CLAUDE.md", "README.md", "docs/check.md"):
            self.assertIn(expected, markdown[0])


class FailureReportingContract(RegressionFixture, unittest.TestCase):
    def test_missing_required_script_is_blocked(self):
        (self.repo / "tests/lint/check_array_expansion.sh").unlink()
        result = self.run_runner()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("empty-array expansion lint | BLOCKED", result.stdout)

    def test_failure_and_blocked_are_both_reported(self):
        (self.bin / "markdownlint-cli2").unlink()
        result = self.run_runner(extra_env={"PYTEST_RUN_STATUS": "1"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("BLOCKED", result.stdout)
        self.assertEqual(
            result.stdout.rstrip().splitlines()[-1],
            "Verdict: FAIL (1 failed, 1 blocked)",
        )

    def test_unknown_flag_is_rejected(self):
        result = self.run_runner("--not-a-mode")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
