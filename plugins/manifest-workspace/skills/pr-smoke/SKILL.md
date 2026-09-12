---
name: pr-smoke
description: Run deterministic repository-local regression gates and report a structured PASS, FAIL, or BLOCKED without bootstrap, deployed-home probes, or live provider calls.
---

# PR Smoke

Run `scripts/run_pr_regression.sh` from the repository under review. The runner
uses only repository-local quality gates it can discover and installed tools on
`PATH`; it never deploys, installs, contacts a provider, or reads an assistant
home.

Its full mode is a portable regression subset, not full CI verification. It
runs discovered Manifest ShellCheck and command-guide checks, repository lint
guards, Markdown lint, shell syntax, Bats, and pytest. Bootstrap validation and
Lite smoke execution remain CI and coordinator gates because a released bundle
cannot depend on either runtime. A required executable, repository-provided
check, or Python module that is unavailable produces `BLOCKED`; a gate that
runs and exits nonzero produces `FAIL`.

This standalone portable subset is distinct from the explicitly configured
project checks (`manifest check`; see the repository's `SHARED_CHECKS.md` for
commands and status vocabulary): `run_pr_regression.sh` discovers what is on
`PATH` in whatever repository it runs against and stays deliberately
dependency-light so it works unmodified outside Manifest's own repo.
`manifest check` runs one *explicitly registered* check closure (the
repository's `project-checks.json` registry) scoped to this repository, is
not portable, and is currently shadow-only (informational, non-blocking)
pending Phase 3 coverage. Do not substitute one for the other: this skill's
script is untouched by that migration and keeps its standalone behavior.

Use `--quick` for tracked staged and unstaged whitespace checks only; untracked
files are explicitly excluded. Exit codes are `0` PASS, `2` FAIL for one or more
executed checks that fail, and `3` BLOCKED when required checks cannot run. When
FAIL and BLOCKED coexist, the runner reports both and exits `2`. Relay the
emitted result table and first failing or blocked gate.

When an independent cross-provider review is required, invoke
`manifest-workspace:parallel-agent` separately and consume its structured output. If the
harness cannot invoke it, perform the review inline and report `DEGRADED`.
