---
name: refactor
description: Inspect a target file or codebase, detect language, and route to the matching refactoring engine.
---

# Unified Codebase Refactor Dispatcher

Inspect target files or directory, detect language, and route execution to the
matching language-specific refactoring engine.

## Routing Rules

| Target Pattern / Ecosystem | Specialized Engine |
|-----------------------------|--------------------|
| `.py`, `pyproject.toml`, `requirements.txt` | `/manifest-code-quality:python-refactor` |
| `.go`, `go.mod` | `/manifest-code-quality:go-refactor` |
| `.ts`, `.tsx`, `.js`, `.jsx`, `package.json` | `/manifest-code-quality:node-refactor` |
| `.sh`, `.bash`, `.zsh` | `/manifest-code-quality:shell-refactor` |
| `.tf`, `.hcl`, `versions.tf` | `/manifest-code-quality:terraform-refactor` |

## Review routing

Use the [review escalation contract](references/review-escalation.md). For
every detected ecosystem, invoke every matching engine sequentially with one
capable reviewer by default, then aggregate the engine reports into one
prioritized cross-stack roadmap. Escalate to independent review only when at
least one of that contract's five risk conditions is present; counts and size
thresholds never escalate review by themselves. A target covering Python, Go,
and Shell therefore remains single-agent while still running all three engines
and aggregating their results unless a risk condition is present.

## Sub-agent dispatch

Follow the [dispatch mechanics](references/refactor-dispatch.md) as well as the
[review escalation contract](references/review-escalation.md). When any one of
the five conditions warrants escalation, obtain an independent review using the
pinned `sonnet` model. Partition work among multiple reviewers only when the
investigation has genuinely independent analysis tracks; otherwise the second
review examines the same target independently. Dispatched reviewers do not
re-dispatch.
