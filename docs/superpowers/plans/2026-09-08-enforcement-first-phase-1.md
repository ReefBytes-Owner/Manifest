# Enforcement-first phase 1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing portable regression runner report its actual scope and fail honestly on failed or unavailable required checks.

**Architecture:** Preserve the Bash runner and its standalone plugin boundary. Repair status aggregation and shell syntax enumeration; do not expand into the repository-specific CI graph yet. Keep fixtures outside the working repository with an allowlisted environment.

**Tech Stack:** Bash 3.2+, Python standard-library unittest fixtures, existing Bash and Git binaries; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-08-enforcement-first-workflow-design.md`

## Global constraints

1. Preserve Bash 3.2 compatibility and the standalone plugin boundary.
2. No installation, network activity, host writes, or provider calls in ordinary checks.
3. Required findings fail; unavailable or incomplete checks are BLOCKED, never PASS.
4. No agent-approved exception, weakened exclusion, or removed meaningful test to obtain green output.
5. No merge or deployment authorization follows from passing checks.

Planning is authorized; execution is a separate handoff. Do not commit or stage
the user's existing changes. Before execution inspect current status again: the
audit observed active staging by other work. Use an isolated checkout according
to the installed worktree workflow, and carry only explicitly selected changes.
If that cannot preserve relevant in-progress work, stop before changing it.

This phase intentionally does not fix all audit findings. It does not make the
runner CI-equivalent, configure a host, change GitHub settings, install tools,
or rewrite the constitution baseline. Missing graph coverage remains explicit.

## File responsibilities

| File | Responsibility |
|---|---|
| `plugins/manifest-workspace/skills/pr-smoke/scripts/run_pr_regression.sh` | Existing CLI, staged/unstaged whitespace checks, required-tool/result aggregation, individual shell syntax checks |
| `tests/python/test_pr_regression_contract.py` | New standard-library isolated subprocess tests for result semantics and scope |
| `plugins/manifest-workspace/skills/pr-smoke/SKILL.md` | Accurate interface, result and scope description |
| `tests/bats/ci_mirror_drift.bats` | Existing mirror assertions; preserve until stronger parity tests supersede them |
| `tests/bats/workspace_plugin_runtime.bats` | Existing standalone-plugin and empty-repository quick compatibility assertions |

The new test file uses unittest so the narrow regression tests can run without
installing pytest or importing the repository's root conftest. It remains
collectable by the existing pytest suite. All snippets below are planned content;
they have not been installed or executed.

## Task 1: Explicit scope and required-tool result contracts

**Files:** modify the runner and its SKILL.md; create `tests/python/test_pr_regression_contract.py`.

**Interfaces:**

- Consumes existing `--quick` and full-mode invocations, preserving unknown-flag rejection.
- Produces exit 0 PASS, exit 2 FAIL, exit 3 BLOCKED; reports both FAIL and BLOCKED rows when both occur.
- Quick output identifies `tracked staged + unstaged whitespace only; untracked files excluded`; full output identifies `portable regression subset; not full CI verification`.
- `required_gate NAME BINARY COMMAND [ARG ...]` records missing BINARY as BLOCKED; `required_python_module_gate NAME BINARY MODULE COMMAND [ARG ...]` separately probes module availability; `run_gate NAME COMMAND [ARG ...]` records an executed check's nonzero status as FAIL.
- Pytest import failure is BLOCKED. Pytest collection errors, no-tests-collected, and failing assertions are FAIL because the selected checker executed but did not verify the suite.

- [ ] Add this isolated fixture/test module. Resolve trusted Bash/Git/head paths before restricting the child PATH. No provider/authentication environment is inherited.

```python
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNNER = (
    Path(__file__).resolve().parents[2]
    / "plugins/manifest-workspace/skills/pr-smoke/scripts/run_pr_regression.sh"
)


class RegressionContract(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(
            prefix="manifest-check-contract-"
        )
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.repo = self.root / "repo"
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        self.logs = self.root / "logs"
        for directory in (self.repo, self.bin, self.home, self.logs):
            directory.mkdir()
        self.runner_bash = os.environ.get("PR_SMOKE_TEST_BASH") or shutil.which(
            "bash"
        )
        self.assertIsNotNone(self.runner_bash, "required fixture tool: bash")
        for name in ("git", "head"):
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
            [str(self.bin / "git"), *args], cwd=self.repo, env=self.env,
            text=True, capture_output=True, check=True, timeout=10,
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
            f"printf '%s|%s\\n' \"$PWD\" \"$*\" >> \"$STUB_LOG_DIR/{name}.log\"\n"
            f"exit {status}\n",
            encoding="utf-8",
        )
        target.chmod(0o755)

    def python_stub(self):
        target = self.bin / "python3"
        target.write_text(
            "#!/bin/sh\n"
            "printf '%s|%s\\n' \"$PWD\" \"$*\" >> \"$STUB_LOG_DIR/python3.log\"\n"
            "if [ \"${1:-}\" = '-c' ]; then exit \"${PYTEST_IMPORT_STATUS:-0}\"; fi\n"
            "if [ \"${1:-}\" = '-m' ] && [ \"${2:-}\" = pytest ]; then "
            "exit \"${PYTEST_RUN_STATUS:-0}\"; fi\n"
            "exit 97\n",
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
            cwd=self.repo, env=env, text=True, capture_output=True,
            check=False, timeout=15,
        )

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

    def test_unknown_flag_is_rejected(self):
        result = self.run_runner("--not-a-mode")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] In the authorized isolated execution environment, run `python3 -I -B tests/python/test_pr_regression_contract.py -v`. Expected RED: staged whitespace is ignored, missing-tool status is 1 rather than 3, missing pytest is misclassified, and scope messages are absent. Confirm failures arise from these assertions, not missing fixture tools.
- [ ] Implement staged/unstaged whitespace checks and required-tool aggregation in Bash. Replace `WARNINGS` with `BLOCKED`, replace `optional_gate` calls with `required_gate`, and use these blocks:

```bash
required_gate() {
    local name="$1" binary="$2"
    shift 2
    if ! command -v "$binary" > /dev/null 2>&1; then
        printf '| %s | BLOCKED (missing %s) |\n' "$name" "$binary"
        BLOCKED=$((BLOCKED + 1))
        return 0
    fi
    run_gate "$name" "$@"
}

required_path_gate() {
    local name="$1" executable="$2"
    shift 2
    if [[ ! -x "$executable" ]]; then
        printf '| %s | BLOCKED (missing executable %s) |\n' "$name" "$executable"
        BLOCKED=$((BLOCKED + 1))
        return 0
    fi
    run_gate "$name" "$executable" "$@"
}

required_python_module_gate() {
    local name="$1" binary="$2" module="$3"
    shift 3
    if ! command -v "$binary" > /dev/null 2>&1; then
        printf '| %s | BLOCKED (missing %s) |\n' "$name" "$binary"
        BLOCKED=$((BLOCKED + 1))
        return 0
    fi
    if ! "$binary" -c \
        'import importlib.util,sys; sys.exit(importlib.util.find_spec(sys.argv[1]) is None)' \
        "$module"; then
        printf '| %s | BLOCKED (missing Python module %s) |\n' "$name" "$module"
        BLOCKED=$((BLOCKED + 1))
        return 0
    fi
    run_gate "$name" "$@"
}

if [[ "$QUICK" -eq 1 ]]; then
    echo 'Profile: quick (tracked staged + unstaged whitespace only; untracked files excluded)'
else
    echo 'Profile: portable regression subset; not full CI verification'
fi

# After every selected check has been reported:
if [[ "$FAILURES" -gt 0 ]]; then
    printf 'Verdict: FAIL (%s failed, %s blocked)\n' "$FAILURES" "$BLOCKED"
    exit 2
fi
if [[ "$BLOCKED" -gt 0 ]]; then
    printf 'Verdict: BLOCKED (%s required checks unavailable)\n' "$BLOCKED"
    exit 3
fi
echo 'Verdict: PASS (selected profile only)'
```

Replace the single whitespace invocation with two named checks in both profiles:

```bash
run_gate 'unstaged whitespace' git diff --check
run_gate 'staged whitespace' git diff --cached --check
```

Invoke Python tests as:

```bash
required_python_module_gate 'python tests' python3 pytest \
    python3 -m pytest tests/python/ -q
```

Invoke repository-provided checks through `required_path_gate`, including both
`tests/lint/*.sh` checks and a discovered command generator. Absence of a
repository feature that was never selected remains out of scope; once a path is
selected for this profile, its disappearance or loss of execute permission is
BLOCKED rather than an executed finding.

Keep `run_gate` nonzero-command behavior; never infer severity from arbitrary
Unix exit codes. A completed pytest invocation returning 1, 2, 3, 4, or 5 is
FAIL; only failure of the explicit interpreter/module availability probes is
BLOCKED. Clarify help/SKILL.md with the exact profiles and status mapping.
Remove the unsupported claim that this subset checks YAML. No full-CI or
merge-safety assertion is allowed in this phase. Missing directories and scope
selection for authoritative verification are phase-2 requirements, not silently
considered solved by this patch.

- [ ] Rerun the unittest command. Expected GREEN for all task-1 tests. Review the diff, document actual commands/results and retain the changes uncommitted.

## Task 2: Check every shell file, including unusual filenames

**Files:** same runner and new unittest module.

**Interfaces:** consumes the existing `manifest_scripts_dir` discovery. Produces
`check_shell_syntax DIRECTORY`, returning nonzero if any immediate `*.sh` file is
invalid or if the discovered script directory contains no shell files. It must
not execute a script's body. Bash 3.2 glob expansion preserves whitespace and
newlines when every expansion is quoted. `PR_SMOKE_BASH` selects the interpreter
used for syntax checks; `PR_SMOKE_TEST_BASH` selects the interpreter that executes
the runner in the fixture suite.

- [ ] Add these methods to `RegressionContract`:

```python
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
```

- [ ] Run `python3 -I -B tests/python/test_pr_regression_contract.py -v`. Expected RED for later invalid scripts, because the current single Bash invocation only parses its first script argument.
- [ ] Add the helper below and replace only the existing `run_gate 'shell syntax' bash -n ...` call with `run_gate 'shell syntax' check_shell_syntax "$manifest_scripts_dir"`:

```bash
check_shell_syntax() {
    local directory="$1" file failed=0 seen=0
    for file in "$directory"/*.sh; do
        [[ -f "$file" ]] || continue
        seen=$((seen + 1))
        if ! "$BASH_BIN" -n "$file"; then
            failed=1
        fi
    done
    if [[ "$seen" -eq 0 ]]; then
        printf 'shell syntax: no shell files in %s\n' "$directory" >&2
        return 1
    fi
    return "$failed"
}
```

Initialize the explicitly selectable syntax interpreter before the table is
rendered, then validate it as a named row after the table header:

```bash
BASH_BIN="${PR_SMOKE_BASH:-bash}"
if ! command -v "$BASH_BIN" > /dev/null 2>&1; then
    printf '| shell syntax | BLOCKED (missing %s) |\n' "$BASH_BIN"
    BLOCKED=$((BLOCKED + 1))
fi
```

Call `check_shell_syntax` only when the interpreter probe succeeds. A requested
but unavailable interpreter is BLOCKED. The runner itself is tested under the
interpreter selected by `PR_SMOKE_TEST_BASH`; selecting it only for `-n` would
not prove that the runner executes on Bash 3.2.

Do not expand the portable runner into bootstrap paths. Full-repository shell
coverage belongs to the phase-2 project check configuration. Keep the existing
token-based mirror test until behavioral parity replaces it; passing its token
searches is not evidence of correct shell iteration.

- [ ] Rerun the unittest command and `bash -n plugins/manifest-workspace/skills/pr-smoke/scripts/run_pr_regression.sh`. Expected GREEN. On macOS, record `PR_SMOKE_TEST_BASH=/bin/bash /bin/bash --version`, require the output to begin with GNU bash 3.2, then run `PR_SMOKE_TEST_BASH=/bin/bash python3 -I -B tests/python/test_pr_regression_contract.py -v`. If `/bin/bash` is not 3.2, report that compatibility check NOT RUN rather than infer it from a modern Bash run.
- [ ] Inspect the final scoped diff and run the applicable existing plugin compatibility checks in verified isolation. Record failures without modifying unrelated tests or baselines. Do not commit.

## Verification environment and acceptance

Before any execution, inspect all scripts/configuration used by the proposed
check. Use a disposable checkout with no production credentials. The narrow
unittest fixture inherits no ambient environment; this is credential hygiene,
not an OS sandbox. Run it inside the runtime's verified isolation boundary.
Do not mount assistant homes or rely on NO_PROXY as a network boundary.

The narrow command bypasses repository conftest and package imports. Broader
pytest/Bats verification must first review conftest, test helpers and imported
code, then use an allowlisted child environment and existing trusted tools.
Do not install missing packages to run this plan without approval.

| Check | Exact command inside isolated checkout | Expected |
|---|---|---|
| New behavior | `python3 -I -B tests/python/test_pr_regression_contract.py -v` | All valid/invalid fixture expectations pass |
| Shell syntax | `bash -n plugins/manifest-workspace/skills/pr-smoke/scripts/run_pr_regression.sh` | Exit 0 |
| Shell lint | `shellcheck -S warning plugins/manifest-workspace/skills/pr-smoke/scripts/run_pr_regression.sh` | Exit 0; missing tool BLOCKED |
| Python lint | `ruff check --no-fix tests/python/test_pr_regression_contract.py` | Exit 0 |
| Python formatting | `ruff format --check tests/python/test_pr_regression_contract.py` | Exit 0 |

The test module snippets prioritize behavior; apply repository formatting during
implementation and rerun verification on the final bytes. Formatter output must
not be mistaken for a read-only check.

Compatibility commands, after inspecting their dependencies and confirming
isolation: `bats tests/bats/workspace_plugin_runtime.bats` and
`python3 -m pytest tests/python/plugin_runtime/test_workspace_runtime.py -q`.
Some existing assertions concern other dirty-tree changes; preserve those changes
and report unrelated failures separately. The new checks must be collected by
the standard pytest invocation as well as runnable directly.

Because this phase edits a source `SKILL.md`, generate the ignored `.apm/skills`
mirror inside the disposable checkout before running mirror tests. Inspect each
script and its imports first. Use the existing locked environment with network
disabled; if it cannot satisfy a command without resolving or downloading a
package, classify that verification BLOCKED and do not install anything.

```bash
configs/claude/scripts/generate_skill_mirror.sh
bats tests/bats/ci_mirror_drift.bats
bats tests/bats/workspace_plugin_runtime.bats
UV_OFFLINE=1 uv run --no-sync --frozen python tools/generate_plugin_views.py --check
UV_OFFLINE=1 uv run --no-sync --frozen python tools/check_bundle_link_references.py
python3 -m pytest \
  tests/python/plugin_runtime/test_workspace_review_repairs.py::test_pr_smoke_has_no_project_runtime_dependency \
  tests/python/plugin_runtime/test_workspace_runtime.py -q
```

After mirror generation, confirm it changed only ignored/generated paths. The
`generate_plugin_views.py --check` command must leave the tracked tree unchanged.
Capture `git status --short` before and after these commands and classify any new
tracked output as FAIL. The standalone dependency test is mandatory because it
directly rejects coordinator, YAML, and `uv run` dependencies in the shipped
pr-smoke script.

Before final handoff record HEAD, staged/unstaged changes, exact tested-file
digests, tool versions, commands, return codes and skipped/blocked checks. If
inputs changed during verification, invalidate the result and rerun the affected
checks. Do not report this phase as full repository verification.

## Rollback and follow-on gate

Rollback only this phase's reviewed file edits; never reset the shared working
tree or alter the user's staged changes. No host-side rollback is needed because
this phase changes no host configuration. Existing consumers that interpret exit
1 as WARN must be reviewed before publication of the new exit-3 contract.

Phase 2 starts with the portable/project configuration interface and actual
consumer inventory. Its acceptance must cover full CI check-set parity, missing
suite/tool handling, deadlines, non-mutating formatting, dependency version
agreement, and complete graph selection. Runtime adapters, baseline migration,
administrative activation and paid benchmarking remain separate planned phases.

## Planning self-review

- [x] Phase-1 requirements map to tasks 1 and 2; later requirements are explicitly assigned to phases 2–5 in the spec.
- [x] Existing standalone-plugin and Bash constraints are retained.
- [x] Test/helper names and runner interfaces agree across tasks.
- [x] Positive, negative, staged/unstaged, unavailable-module, exact-scope, unusual-path and non-execution cases are specified.
- [x] Generated projection and standalone-plugin dependency checks are explicit.
- [x] Approval and verification boundaries are explicit; no implementation or passing baseline is claimed.
