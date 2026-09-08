# Plugin Capability Matrix

Generated from portable contracts and verified adapter inspection evidence; do not edit by hand.
`READY` requires a native adapter inspection with a non-empty version.
Matching installed plugin, component, and capability evidence is also required.

| Capability | Evidence | Claude | Codex | Gemini | Cursor | Antigravity | Devin |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `manifest-code-quality:skill:ai-code-audit` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:antipattern-detect` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:api-optimize-bulk` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:cli-audit-help` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:code-audit-constitution` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:data-design-ingestion` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:data-validate-live` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:data-wire-field` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:false-green-check-audit` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:go-refactor` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:llm-invoke-stdin` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:node-refactor` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:project-scaffold` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:project-verify` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:python-refactor` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:refactor` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:shell-audit` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:shell-audit-errexit` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:shell-audit-pipefail` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:shell-refactor` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:smoke-manage` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:terraform-refactor` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:test-pin-bug` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:skill:test-vary-fixtures` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:runtime:code-audit-references` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:runtime:constitution-config` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:runtime:constitution-references` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:runtime:constitution-scripts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:runtime:review-escalation-reference` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:runtime:scaffold-templates` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:runtime:smoke-scripts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:runtime:smoke-vendor` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:executable:git` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:executable:python3` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-code-quality:executable:browser-use` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-code-quality:executable:playwright` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-code-quality:executable:semgrep` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-docs:skill:docs-all` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-docs:skill:docs-generate-diagrams` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-docs:skill:docs-improve` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-docs:skill:docs-improve-readme` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-docs:runtime:docs-lint` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-docs:runtime:docs-references` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-docs:executable:git` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-docs:executable:python3` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:branch-clean` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:git-commit` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:git-find-artifact` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:issue-dev-auto` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:issue-manage` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:issue-prep-auto` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:issue-prioritize` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:issue-sync-commit` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:issue-sync-pr` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:issue-triage` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:lifecycle-run` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:pr-address-comments` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:pr-clean-base` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:pr-manage` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:pr-merge-stacked` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:pr-monitor` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:pr-reset-reapply` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:pr-review` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:pr-triage-bots` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:skill:repo-clean` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-forge:runtime:forge-bin` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-forge:runtime:forge-config` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-forge:runtime:forge-python` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-forge:runtime:forge-references` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-forge:mcp:atlassian` | contract optional mcp | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-forge:mcp:github` | contract optional mcp | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-forge:mcp:linear` | contract optional mcp | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-forge:executable:bash` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-forge:executable:git` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-forge:executable:python3` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-forge:executable:curl` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-forge:executable:flock` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-forge:executable:gh` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-forge:executable:glab` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-forge:executable:gtimeout` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-forge:executable:jq` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-forge:executable:timeout` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-ops:skill:cache-warm-oob` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:ci-diagnose-drift` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:ci-reproduce-failure` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:ci-setup` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:config-debug-substitution` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:config-validate-native` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:deploy-diagnose-drift` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:deploy-retire-component` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:docker-compose-commandments` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:docker-probe-internal` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:process-diagnose-stall` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:skill:version-pin` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-ops:hook:compose-commandments` | contract hook | READY | DEGRADED(Codex exposes the on-demand docker-compose-commandments skill but has no native file-save hook surface.) | DEGRADED(Gemini exposes the on-demand docker-compose-commandments skill but cannot install this advisory plugin hook natively.) | READY | DEGRADED(Antigravity exposes the on-demand docker-compose-commandments skill but has no native file-save hook surface.) | DEGRADED(Devin exposes the on-demand docker-compose-commandments skill but has no native file-save hook surface.) |
| `manifest-ops:hook:version-pin` | contract hook | READY | DEGRADED(Codex exposes the on-demand version-pin skill but has no native file-save hook surface.) | DEGRADED(Gemini exposes the on-demand version-pin skill but cannot install this advisory plugin hook natively.) | READY | DEGRADED(Antigravity exposes the on-demand version-pin skill but has no native file-save hook surface.) | DEGRADED(Devin exposes the on-demand version-pin skill but has no native file-save hook surface.) |
| `manifest-ops:runtime:ci-setup-templates` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-ops:runtime:ops-bin` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-ops:runtime:ops-config` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-ops:runtime:ops-references` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-ops:mcp:sentry` | contract optional mcp | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-ops:executable:bash` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-ops:executable:git` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-ops:executable:python3` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-ops:executable:docker` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-ops:executable:shasum` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-ops:executable:sha256sum` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-ops:executable:terraform` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-ops:executable:tflint` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-ops:executable:tofu` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-security:skill:ci-audit-triggers` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-security:skill:ci-harden-workflow` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-security:skill:code-audit` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-security:skill:docker-audit-firewall` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-security:skill:llm-audit-traversal` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-security:skill:mcp-audit` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-security:skill:security-harden-proxy` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-security:skill:security-refute-findings` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-security:skill:security-review-diff` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-security:skill:security-triage-findings` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-security:runtime:security-bin` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-security:runtime:security-references` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-security:executable:bash` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-security:executable:git` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-security:executable:python3` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-security:executable:semgrep` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-spec-planning:skill:design-validate` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:skill:plan-manage` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:skill:premise-verify` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:skill:spec-audit-tasks` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:skill:spec-decide-tradeoffs` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:skill:spec-implement-loop` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:skill:spec-review` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:runtime:cddl-runtime` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:runtime:plan-store` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:runtime:planning-config` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:runtime:planning-prompts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:runtime:planning-references` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:runtime:spec-review` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:executable:bash` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:executable:git` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:executable:python3` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-spec-planning:executable:agy` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-spec-planning:executable:devin` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-spec-planning:executable:shasum` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-workspace:skill:ai-hooks-integration` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:automation-rework-breakeven` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:config-audit` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:deploy-reconcile` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:env-check` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:help` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:learning-capture` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:memory-compress` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:metrics-report` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:parallel-agent` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:pass-cli` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:pr-smoke` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:prompt-optimize` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:session-checkpoint` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:skill-evolve` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:test-isolate-ambient` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:token-benchmark` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:skill:token-conserve` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:compatibility-translator` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:context-chronicler` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:dependency-guardian` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:devpanel-chaos-engineer` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:devpanel-debugger` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:devpanel-developer` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:devpanel-performance-auditor` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:devpanel-spec-guard` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:devpanel-tester` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:executor` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:explore` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:mech-executor` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:scout` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:security-executor` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:agent:verifier` | contract agent | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:hook:codex-permission-request` | contract hook | N/A(This component records the Codex PermissionRequest surface only.) | READY | N/A(This component records the Codex PermissionRequest surface only.) | N/A(This component records the Codex PermissionRequest surface only.) | N/A(This component records the Codex PermissionRequest surface only.) | N/A(This component records the Codex PermissionRequest surface only.) |
| `manifest-workspace:hook:codex-session-start` | contract hook | N/A(This component records the Codex SessionStart surface only.) | READY | N/A(This component records the Codex SessionStart surface only.) | N/A(This component records the Codex SessionStart surface only.) | N/A(This component records the Codex SessionStart surface only.) | N/A(This component records the Codex SessionStart surface only.) |
| `manifest-workspace:hook:codex-stop` | contract hook | N/A(This component records the Codex Stop surface only.) | READY | N/A(This component records the Codex Stop surface only.) | N/A(This component records the Codex Stop surface only.) | N/A(This component records the Codex Stop surface only.) | N/A(This component records the Codex Stop surface only.) |
| `manifest-workspace:runtime:command-catalog` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:deploy-reconcile-scripts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:env-check-scripts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:help-scripts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:hook-integration-scripts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:learning-capture-scripts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:parallel-agent-config` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:parallel-agent-prompts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:parallel-agent-references` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:parallel-agent-scripts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:pr-smoke-scripts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:runtime:skill-evolve-scripts` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:guidance:workspace-orchestration` | contract guidance | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:guidance:workspace-token-economy` | contract guidance | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:mcp:context7` | contract default mcp | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:executable:bash` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:executable:git` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:executable:python3` | contract required executable | READY | READY | READY | READY | READY | READY |
| `manifest-workspace:executable:pass-cli` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-workspace:executable:shellcheck` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-workspace:executable:markdownlint-cli2` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-workspace:executable:pytest` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-workspace:executable:bats` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `stitch-design:skill:a11y-audit` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:code-to-design` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:design-loop` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:design-md` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:enhance-prompt` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:extract-design-md` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:extract-static-html` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:generate-design` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:loop-scaffold` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:manage-design-system` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:performance-check` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:react-components` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:react-native` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:react-vite-dashboard` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:remotion` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:render-verify` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:review-round` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:screen-prompts` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:shadcn-ui` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:spec-amend` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:stitch-loop` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:taste-design` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:upload-to-stitch` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:skill:ux-review` | contract skill | READY | READY | READY | READY | READY | READY |
| `stitch-design:agent:design-lens-reviewer` | contract agent | READY | READY | READY | READY | READY | READY |
| `stitch-design:agent:skeptic-verifier` | contract agent | READY | READY | READY | READY | READY | READY |
| `stitch-design:runtime:react-native-validator` | contract runtime | READY | READY | READY | READY | READY | READY |
| `stitch-design:runtime:react-validator` | contract runtime | READY | READY | READY | READY | READY | READY |
| `stitch-design:runtime:static-html-sources` | contract runtime | READY | READY | READY | READY | READY | READY |
| `stitch-design:runtime:stitch-build` | contract runtime | READY | READY | READY | READY | READY | READY |
| `stitch-design:runtime:stitch-build-lock` | contract runtime | READY | READY | READY | READY | READY | READY |
| `stitch-design:runtime:stitch-build-package` | contract runtime | READY | READY | READY | READY | READY | READY |
| `stitch-design:runtime:stitch-dist` | contract runtime | READY | READY | READY | READY | READY | READY |
| `stitch-design:mcp:stitch` | contract optional mcp | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `stitch-design:executable:bash` | contract required executable | READY | READY | READY | READY | READY | READY |
| `stitch-design:executable:git` | contract required executable | READY | READY | READY | READY | READY | READY |
| `stitch-design:executable:python3` | contract required executable | READY | READY | READY | READY | READY | READY |
| `stitch-design:executable:node` | contract default executable | READY | READY | READY | READY | READY | READY |
| `stitch-design:executable:chromium` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `stitch-design:executable:curl` | contract optional executable | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) | N/A(contract optional; not selected) |
| `manifest-i-have-adhd:skill:i-have-adhd` | contract skill | READY | READY | READY | READY | READY | READY |
| `manifest-i-have-adhd:hook:adhd-session-start` | contract hook | READY | READY | READY | READY | READY | READY |
| `manifest-i-have-adhd:runtime:adhd-hook-runtime` | contract runtime | READY | READY | READY | READY | READY | READY |
| `manifest-i-have-adhd:guidance:adhd-always-on-guidance` | contract guidance | READY | READY | READY | READY | READY | READY |
| `manifest-i-have-adhd:executable:python3` | contract required executable | READY | READY | READY | READY | READY | READY |
