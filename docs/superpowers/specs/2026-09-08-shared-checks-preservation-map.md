# Shared check preservation map

Status: proposed design evidence, not implemented enforcement. Read alongside
`2026-09-08-shared-checks-ci-design.md`. Observed base:
`7741d4aa588ed57791af15ea0eedd8862305ff0c` plus reviewed Phase 1 changes.
Identifiers below are proposed stable IDs. No listed control is retired.

## CI verification mapping

Sources: `.github/workflows/ci.yml` and `.github/workflows/manifest-release.yml`.
Paths, arguments and versions are observations; proposed changes are explicit.

| Current control | Proposed ID | Scope / preservation requirement |
|---|---|---|
| ShellCheck scripts | lint.shell.scripts | configs/claude/scripts/*.sh; -S warning |
| ShellCheck bootstrap | lint.shell.bootstrap | bootstrap.sh and bootstrap/lib/*.sh; -S warning |
| Empty-array guard | lint.shell.arrays | tests/lint/check_array_expansion.sh; preserve original discovery |
| Bats assertion guard | lint.bats.assertions | tests/lint/check_bats_assertions.sh |
| Fresh checkout | package.self-contained | tests/lint/check_fresh_checkout.sh; disposable candidate index, not original HEAD |
| yamllint config | lint.yaml.config | configs/claude/config/*.yml; repository .yamllint |
| Key-doc Markdown action | lint.markdown.keydocs | AGENTS.md, CLAUDE.md, README.md, docs/*.md; preserve action engine/config behavior |
| Python YAML parse | syntax.yaml.config | every config/*.yml; malformed/unreadable required file fails |
| Commands documentation | generated.commands-doc | generate_commands_doc.py --check |
| Bundle references | structure.bundle-references | check_bundle_link_references.py; existing baseline preserved |
| Plugin native views | generated.plugin-views | generate_plugin_views.py --check |
| Vendored dependencies | generated.vendor | vendor_bundle_dependencies.py --check |
| Runtime paths | structure.runtime-paths | check_plugin_runtime_paths.py --json |
| Agent frontmatter | structure.agent-frontmatter | check_agent_frontmatter.py |
| Capability inventory | generated.capability-inventory | render_capability_inventory.py --check |
| Capability matrix | generated.capability-matrix | render_plugin_capability_matrix.py --check with pinned inspection fixture |
| Coordinator wheel and checksum | package.coordinator | uv build; checksum all resulting artifacts in isolated output |
| Config lock | dependency.lock.config | configs/claude/uv.lock and complete project graph |
| Config build | package.config | uv build --project configs/claude; isolated output |
| Delegate lock | dependency.lock.delegate | plugins/manifest-delegate/uv.lock and complete graph |
| Changed pre-commit controls | hook.* rows below | preserve exact selectors/exclusions and base semantics; make verification non-mutating |
| All Bats | test.bats | tests/bats/; required helpers at pinned gitlinks |
| Repository pytest | test.python | tests/python/ -v -m "not native"; missing suite becomes BLOCKED |
| Skill-local hook pytest | test.hooks | .apm/skills/ai-hooks-integration/tests/ -v; separate invocation |
| Lite smoke | test.smoke.lite | --app manifest --tier Lite --catalog-dir smoke-catalog; missing catalog becomes BLOCKED |
| Symlinks | structure.symlinks | exact target list extracted unchanged from validate job |
| Case collision | structure.case-collision | complete tracked candidate index; NUL-safe enumeration |
| Shell syntax | syntax.shell.project | configs/claude/scripts/*.sh, bootstrap.sh, bootstrap/lib/*.sh; individual Bash invocations |
| Exact skill count and script presence | structure.inventory | expected_total from skill_policies.yml; require nonempty script set |
| Absolute skill paths | structure.skill-paths | .apm/skills SKILL.md bodies |
| Bundle partition | test.bundle-partition | tests/bats/bundle_partition.bats; provisioned Bats, no npx download |
| Cross-skill references | structure.skill-references | skill_reference_check.py plus existing registry/baseline |
| Cursor generated outputs | generated.cursor | rules/, mcp.json, agents/; detect changed and new files in isolated candidate |
| Release archive build | package.release-archive | tools/build_manifest_release.py, declared output and archive URL metadata |
| Release manifest version parse | package.release-manifest | validate generated manifest and mutually consistent bundle version |

Root lock validation and delegate package build are required additions for the
complete final package graph, not existing CI controls falsely claimed preserved.
Likewise type/SAST/dependency advisory coverage is Phase 3 work, not already
delivered by this inventory.

## Pre-commit hook mapping

Source: `.pre-commit-config.yaml`. Proposed IDs are `hook.<existing-id>`.
For every row, preserve the literal `files`, `types`, `types_or`, `exclude`,
`stages`, `pass_filenames`, arguments, and global exclude from that source in
the reviewed mapping data before implementation. Do not replace complex regexes
with approximations. Hook IDs supply an independently enumerated parity oracle.

| Existing hook IDs | Proposed execution treatment |
|---|---|
| trailing-whitespace, end-of-file-fixer, mixed-line-ending | Check-only equivalent or execute pinned fixer only in disposable comparison copy; detect all byte changes |
| check-yaml, check-json | Preserve parser behavior, including check-yaml --unsafe semantics; do not execute constructors |
| check-added-large-files | Preserve 500 KB policy and original tracked/added applicability |
| check-case-conflict, check-merge-conflict | Preserve selected-path checks; full candidate collision check separately retained |
| check-executables-have-shebangs, check-shebang-scripts-are-executable | Preserve executable bits and .bats/.tmpl exceptions |
| detect-private-key | Preserve existing secret detection; redact diagnostics |
| check-ast, debug-statements | Python/pyi selectors preserved |
| shellcheck | warning severity; existing shell/.bats exclusions |
| shfmt | Preserve Bash, indent 4, -ci/-sr; verification removes -w and reports diffs |
| markdownlint-cli2 | Same config/engine/exclusions; verification removes --fix |
| yamllint | Same repository configuration and exclusions |
| ruff, ruff-format | Preserve selected rule set and vendor exclusions; check-only invocation |
| golangci-lint | Conditional language control; do not install for absent active Go sources |
| eslint | Preserve current scope; actual TS/JS product graph audited separately in Phase 3 |
| terraform_fmt, terraform_validate, terraform_tflint, terraform_trivy | Preserve existing types_or: [terraform] applicability, including matching templates; the current config has no template exclusion. No narrowing is authorized by this inventory. |
| gitleaks | Preserve scan semantics/history scope; version convergence required |
| constitution-check | Existing count ratchet preserved until reviewed identity-based successor; no auto baseline updates |
| validate-bootstrap | bash -n bootstrap.sh |
| check-bats-assertions, check-array-expansion | Same underlying guards as CI; deduplicate when selected with identical scope |
| check-credentials | Preserve shell/Markdown/YAML/JSON coverage; distinguish scanner errors from clean result and redact hits |
| validate-yaml-configs | Same config selector/parser; deduplicate identical CI invocation only |
| check-cursor-rules-drift | Preserve selection trigger and nonzero/drift-output detection; shared generated.cursor body |
| check-stale-repo-paths | Preserve documented root/docs scope and every exclusion |
| cargo-fmt-check, cargo-clippy | Conditional Rust controls; absent active Rust means inactive, not a successful Rust analysis |
| pyright | Existing manual check preserved; Phase 3 defines full graph and reviewed debt policy before required activation |

Current changed-file base behavior: PR compares origin/base-ref to HEAD; push
compares HEAD~1 to HEAD; first commit falls back to all files. Proposed registry
requires explicit immutable base/candidate identity and adds local staged,
unstaged and non-ignored untracked inputs. No diff filter may truncate tests,
types, lock resolution or build dependency graphs.

## Setup versus checks

Checkout, pinned helper initialization, mirror materialization, tool installation,
environment synchronization, and cache restoration are setup, not evidence of
check success. Preserve actions' immutable references until reviewed updates.

Observed pins: shellcheck-py 0.11.0.1; yamllint 1.38.0; pre-commit hooks 6.0.0;
shfmt wrapper 3.13.1-1; Markdown pre-commit engine 0.23.0 versus CI action v24;
Ruff 0.15.20; Bats CI 1.11.1 versus local 1.13.0; Gitleaks hook 8.30.0 versus
CI 8.30.1; release uv 0.12.6 versus local 0.11.14. Ordinary CI installs uv and
pre-commit without explicit version pins. Resolve these discrepancies explicitly;
do not claim matched versions from existing successful narrow checks.

Keep checksum verification for downloaded Gitleaks and install model-policy from
its local reviewed package path before coordinator tests. Build environments
must already contain the selected backends before offline check execution.

Release workflow currently grants contents:write at workflow scope and publishes
on selected main pushes. Proposed verification jobs use contents:read; any
publication job is separate, consumes verified same-candidate artifacts, and
requires the separately approved administrative protection. Release view/tag/
create/upload/publish commands are not part of `manifest check release`.
Preserve the publication validations in that separate job: distinguish an absent
release from lookup/authentication failures; skip already published versions;
verify an existing tag resolves to the exact GITHUB_SHA, peeling annotated tags;
and create drafts with --verify-tag. Separating verification from publication
does not retire these checks or authorize their execution during local verification.

## Review acceptance

The implementation must turn this inventory into exact machine-readable scope
and version mappings, reviewed independently against the observed source. Tests
must reject missing old hooks/steps, altered exclusions, and unexpected required
check removal. This draft has not yet been independently approved as a baseline
and may not itself authorize an exception or removal.

Independent read-only review confirmed coverage of all 37 hook IDs and current
CI verification steps. Three reported corrections (exact shell path, Terraform
applicability, publication validation preservation) are incorporated above.
