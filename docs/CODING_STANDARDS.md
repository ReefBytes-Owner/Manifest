<!-- doc-type: reference -->
# Coding Standards

> Authoritative per-language coding standards for the Manifest repo and how they
> are enforced.

**Last Updated**: 2026-07-29
**Audience**: Contributors, AI coding agents
**Spec**: [specs/366-coding-standards/spec.md](../specs/366-coding-standards/spec.md)

This document is the single source of truth for the rules each language follows
and whether those rules are actively enforced, conditionally enforced, or merely
documented in this repo. Where a rule is mechanically checked, the enforcing layer
and tool are named.

## Enforcement Layers

Standards are enforced in five complementary layers, fastest/earliest to latest:

| Layer | When it runs | Blocks? | Auto-fix? | Mechanism |
|-------|--------------|---------|-----------|-----------|
| Editor | As you type / on save | No | Per editor | `.editorconfig`, LSP |
| Pre-write | Before every agent `Read`/`Write`/`Edit`/`MultiEdit` | **No** (advisory) | **No** | `configs/claude/scripts/constitution_hook.py` (PreToolUse) |
| Edit-time | After every agent `Write`/`Edit` | **No** (advisory) | **No** | `configs/claude/scripts/lint_on_edit_hook.sh` (PostToolUse), which also dispatches `constitution_check.py` |
| Commit | On `git commit` (if `pre-commit install` was run) | Yes | Yes | `.pre-commit-config.yaml`, incl. the ratcheted `constitution-check` hook |
| Gate of record | On every PR/push (CI) | Yes | No | `.github/workflows/ci.yml` runs pre-commit on changed files |

The **pre-write** layer injects the Code Constitution doctrine once per language
per session plus per-file measurements on every call; it never denies a tool call
and always exits 0. The **edit-time** layer is advisory only: it lints the
just-edited file and writes findings to stderr, but never blocks the edit, never
rewrites the file, and fails open when a linter is absent. The **gate of record**
runs the same `.pre-commit-config.yaml` against the files changed in a PR/push, so
standards cannot be bypassed by skipping local hooks. It is scoped to changed files
(not the whole tree) so pre-existing debt does not block unrelated work; debt is
paid down as files are touched.

## Code Constitution

Language-independent doctrine applied *before* code is written, complementing (not
replacing) the per-language rules below: thirteen articles `CON-001`–`CON-013`, of
which eight checks (`C-SIZE`, `C-DUPE`, `C-DATA`, `C-TYPE`, `C-ERR`, `C-TEST`,
`C-STRUCT`, `C-DOC`) enforce the mechanically provable subset; the rest is judgement.

Source of truth: [configs/claude/config/code_constitution.yml](../configs/claude/config/code_constitution.yml).
The prose derives from it — [code-constitution.md](../configs/claude/references/code-constitution.md)
for the universal articles, plus five annexes:
[python](../configs/claude/references/constitution/python.md) ·
[node](../configs/claude/references/constitution/node.md) ·
[go](../configs/claude/references/constitution/go.md) ·
[shell](../configs/claude/references/constitution/shell.md) ·
[terraform](../configs/claude/references/constitution/terraform.md).

**The gate is ratcheted, not retroactive.**
[constitution_baseline.json](../configs/claude/config/constitution_baseline.json)
records each file's violation count per check, and only a *rise* blocks. Fixing a
violation lowers the entry permanently; raising one needs the reason in the commit
message. Baseline changes use `manifest check debt.constitution --propose-baseline`.

```bash
configs/claude/scripts/constitution_check.py FILE               # gate output
configs/claude/scripts/constitution_check.py --show-info FILE   # include info
configs/claude/scripts/constitution_check.py --no-baseline FILE # every violation
```

Exit codes: `0` clean, `1` blocking findings, `2` usage or registry error.

## Scope Verdicts

Each language carries one verdict:

- **Active** — real files exist; rules are enforced now.
- **Conditional** — no real files yet; a guarded hook fires only when sources appear.
- **Document-only** — rules are recorded as a reference; no active hook in this repo.

| Language | Verdict | Primary tools |
|----------|---------|---------------|
| Bash | Active | shellcheck, shfmt, custom array-expansion lint |
| Python | Active | ruff (lint + format), pyright (opt-in) |
| Markdown | Active | markdownlint |
| YAML | Active | yamllint, check-yaml |
| JSON | Active | check-json / `json.load` |
| Bats | Active (tests) | bats (run); shellcheck (edit-time advisory) |
| PowerShell | Document-only | EditorConfig (PSScriptAnalyzer if it becomes load-bearing) |
| Go | Conditional | gofumpt, golangci-lint v2 |
| Rust | Conditional | rustfmt, clippy (guarded cargo hooks) |
| Terraform | Conditional | terraform fmt/validate, tflint, Trivy |

## Languages

### Bash (Active — primary)

**Rules:**

- `set -euo pipefail` at the top of every standalone script. Sourced
  `bootstrap/lib/` files may omit `-e` with a documented rationale.
- Guard empty-array expansion for macOS Bash 3.2 + `set -u`:
  `"${arr[@]+"${arr[@]}"}"`. Enforced by `tests/lint/check_array_expansion.sh`.
- Prefer `[[ ]]` over `[ ]`; quote all expansions; use `command -v`, not `which`.
- Declare function-local variables with `local`; module constants with `readonly`.
- Route error/warning output through `err() { echo "<script-name>: $*" >&2; }`.
  `bootstrap/lib/` keeps its own `print_error()` family as the sole exception.
- Every user-facing entry-point script handles `--help` (usage ≤15 lines, exit 0),
  and the help path must succeed before any config read, state lookup, or
  dependency probe. Detection/save-hook helpers opt out **in the file** with
  `# help-coverage: exempt — <rationale>` under the shebang (`ci_platform.sh`,
  `git_platform.sh`, `version_pin_hook.sh`, `manifest-cli.sh`). Verify the help
  path with an **empty `HOME`**, not your own: a wrapper that forwards to the
  home runtime exits 0 on a configured machine and 1 in CI. See the Python section for why the
  coverage set is enumerated rather than listed.
- Never `eval` or interpolate untrusted input into shell source.
- Inline `# shellcheck disable=SCxxxx` with a reason only; never blanket file-level
  disables.

**Constitution annex:** [shell](../configs/claude/references/constitution/shell.md).

**Enforcement:** shellcheck `--severity=warning` (commit + CI) and
`--severity=info` (edit-time advisory); `shfmt -i 4 -ci -sr -ln bash`; the
array-expansion lint at commit and CI.

### Python (Active — primary)

**Rules:**

- Format with ruff-format; lint with ruff. Configuration lives only in
  `pyproject.toml` (`[tool.ruff]`).
- Be explicit: `import module`, never `from module import *`.
- Use type hints on public signatures; modern syntax (`list`/`dict`/`X | None`).
- Catch specific exceptions; `raise X from err`; never a bare `except:`.
- Prefer `pathlib`, f-strings, and `logging` over `os.path`, `%`/`.format`, and
  `print()` in library code.
- Keep environments isolated (`venv`/`uv`); pin test/runtime deps.
- **Exit codes for NEW CLI entry points**: `0` success, `2` usage or unusable
  input (bad flag, bad argument value, unreadable path, unwritable output).
  Reserve `1` for "ran correctly and found violations" — the `version_pin.sh
  --check` shape — so a caller can distinguish "I invoked it wrong" from "it
  worked and the answer was bad". `2` also matches argparse's own default for
  unrecognised arguments, so the documented contract and the framework agree
  without extra code. Record the mapping in the module docstring
  (`Exit codes: ...`). Existing scripts predate this and use several other
  schemes (`1=failure`, `64=usage`, domain-specific `3`/`4`); do not retrofit
  them — callers may depend on the current codes.
- **Never let an empty result exit 0 as if it were a clean run.** A mistyped
  path or an empty time window must be distinguishable from a genuine zero.
  Equally, an unparseable filter value must be a hard error, not a silently
  ignored one — string-comparing a timestamp bound accepts garbage
  (`"2026-…" > "banana"` is `False`) and silently widens the scan.
- **`--help` entry points**: every directly-invocable Python CLI in
  `configs/claude/scripts/` handles `--help` (exit 0, `usage`/`Usage` in
  output), gated by `tests/bats/help_coverage.bats`.

  **Coverage is enumerated, never listed.** The gate walks every `*.py` and
  `*.sh` in the directory. An inclusion list fails in the direction you cannot
  see — a new script that forgets to join it is silently ungated — and a name
  in the list that cannot satisfy the gate breaks CI, which is exactly how
  `parallel_agent.py` broke the build. Exclusions come from one of three
  places, in order of preference:

  1. **Derived from the code.** A file with no `__main__` block is a library,
     not an entry point (`_manifest_shim.py`). A file importing
     `_manifest_shim` is a `manifest` deprecation shim: it execs the home
     runtime, so it prints *that* runtime's usage when `manifest` is installed
     and a deprecation notice when it isn't. Gating those makes the suite pass
     or fail on whether the runtime happens to be built — green locally, red in
     CI. Deriving it means a future shim is exempt automatically.
  2. **Declared in the file**, directly under the shebang:
     `# help-coverage: exempt — <rationale>`. Used by `budget_broker.py` (an
     interceptor wrapper — its argv IS the wrapped command, so `--help` is
     forwarded to the child) and `reconcile_core.py` (internal read-only engine
     behind `deploy_reconcile.sh`; `add_help=False`, no direct CLI surface).
     The rationale travels with the code, and a bare opt-out is itself a test
     failure.
  3. **Nothing else.** A script that is neither gated nor marked fails the
     `coverage is enumerated, not listed` test.

**Constitution annex:** [python](../configs/claude/references/constitution/python.md).

**Enforcement:** `ruff check` + `ruff format` (commit + CI on changed files);
`ruff check` advisory at edit-time; pyright is available as an opt-in manual hook
(`pre-commit run pyright --hook-stage manual`) until typed coverage grows. The CI
gate is scoped to changed files, so the existing ruff debt in legacy modules is
fixed as those files are touched.

### Markdown (Active)

**Rules:** follow `.markdownlint.jsonc` (120-column lines, `MD033`/`MD041`/`MD060`
off, `MD024` siblings-only). Fenced code blocks declare a language.

**Enforcement:** markdownlint at commit + CI on changed `.md`. Cursor rule files
(`.mdc`) are linted **advisory at edit-time** but are not a blocking gate, because
they are generated by `generate_cursor_rules.sh` (the generator owns their format).

### YAML (Active)

**Rules:** valid YAML; follow `.yamllint` (max line 150, document-start off).

**Enforcement:** `check-yaml --unsafe` + yamllint at commit + CI; yamllint advisory
at edit-time.

### JSON (Active)

**Rules:** syntactically valid JSON; 2-space indent (`.editorconfig`).

**Enforcement:** `check-json` at commit + CI; `json.load` validation advisory at
edit-time. (JSON Schema validation of config files is a future enhancement.)

### Bats (Active — test scripts)

**Rules:** Bash rules apply. Tests live in `tests/bats/` and run in CI.

**Enforcement:** executed by bats in CI; shellcheck advisory at edit-time. A
blocking shellcheck gate for `.bats` is a future enhancement.

### PowerShell (Document-only)

**Rules:** 4-space indent (`.editorconfig`); follow PowerShell community style.

**Enforcement:** EditorConfig only today. Add PSScriptAnalyzer if the `.ps1`
scripts under `.specify/extensions/git/scripts/powershell/` become load-bearing.

### Go (Conditional)

**Rules:** `gofmt`/`gofumpt`; check every `if err != nil`; small single-method
interfaces; avoid goroutine leaks and package-level mutable state; wrap errors with
`%w`; `ctx` as the first argument; run tests with `-race`.

**Constitution annex:** [go](../configs/claude/references/constitution/go.md).

**Enforcement:** a `golangci-lint` (v2) hook is configured but **dormant** (no real
`.go` files); it fires only when Go sources appear.

### Rust (Conditional)

**Rules:** structure data to fit ownership/lifetimes; avoid `unwrap()` — handle
`Result`/`Option`; run `cargo clippy` with `-D warnings`; minimise and document
`unsafe` (`// SAFETY:`); use newtypes/enums to encode domain constraints.

**Enforcement:** guarded local `cargo fmt --check` / `cargo clippy` hooks that run
only when `.rs` files are staged and a `Cargo.toml` exists. The unmaintained
`doublify/pre-commit-rust` is deliberately avoided.

### Terraform (Conditional)

**Rules:** `terraform fmt`; pin provider and module versions (`required_version`,
`~>`); use typed, described variables and locals instead of magic strings; store
state remotely with locking; minimise blast radius by separating environments.

**Constitution annex:** [terraform](../configs/claude/references/constitution/terraform.md).

**Enforcement:** `terraform_fmt`/`terraform_validate`/`terraform_tflint` plus
**Trivy** (`terraform_trivy`) hooks are configured but **dormant** (no real `.tf`
files). The deprecated `tfsec` has been removed (merged into Trivy in 2024).

## Edit-time Advisory Hook

`configs/claude/scripts/lint_on_edit_hook.sh` runs after every `Write`/`Edit` (a
PostToolUse hook in `configs/claude/settings.local.json`). It dispatches by
extension — `.sh`→shellcheck, `.py`→ruff, `.yml`/`.yaml`→yamllint, `.json`→JSON
parse, `.md`/`.mdc`→markdownlint — and:

- never blocks the edit (always exits 0);
- never auto-fixes (no `ruff --fix`, no `shfmt -w`);
- fails open when a linter is not installed;
- is time-bounded and skips generated/vendored/scaffold paths.

## Exceptions

Suppress a rule **inline, with a stated rationale**. Blanket, file-level
disables are not the default practice — scope every suppression as narrowly as
possible and explain why. Per-language inline syntax:

| Language | Inline suppression (with reason) |
|----------|----------------------------------|
| Bash | `# shellcheck disable=SC2086 — reason` (next line) |
| Python | `x = ...  # noqa: F401 — reason` |
| YAML | `# yamllint disable-line rule:line-length` |
| Markdown / MDC | `<!-- markdownlint-disable-next-line MD013 -->` |

Dormant-language hooks (Go/Rust/Terraform) are kept (not deleted) so the repo
signals it can host those languages; they fire only when matching sources appear.

## Tooling Currency

Deprecated tools are not permitted; pinned versions are kept current via
`pre-commit autoupdate`. Notable: `tfsec` → Trivy, golangci-lint v1 → v2. See
[specs/366-coding-standards/research-notes.md](../specs/366-coding-standards/research-notes.md)
for the full currency analysis.

## Related Documents

- [.pre-commit-config.yaml](../.pre-commit-config.yaml) — commit + CI hook suite
- [.editorconfig](../.editorconfig) — editor-level formatting
- [CONTRIBUTING.md](../CONTRIBUTING.md) — development workflow
- [specs/366-coding-standards/](../specs/366-coding-standards/) — spec, plan, research
