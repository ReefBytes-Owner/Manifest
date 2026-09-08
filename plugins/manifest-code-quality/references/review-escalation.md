# Review Escalation Contract

Use one capable reviewing agent by default. Run every relevant deterministic
lint, test, or security check that is already available, provided the command is
read-only.

Add independent review when at least one of these conditions is present:

- authentication, authorization, cryptography, secret handling, or another
  trust-boundary change;
- destructive data or infrastructure behavior;
- a public compatibility or deployment change with broad impact;
- conflicting evidence or unresolved reviewer uncertainty;
- a codebase-wide investigation with genuinely independent analysis tracks.

File size, language, a generic keyword, or a count of files, packages, modules,
stacks, scripts, or analysis dimensions alone is not an escalation trigger.

## Check-only verification

Checks may inspect files and execute relevant linters, tests, and security
scanners. They must not apply automatic fixes, write formatting changes, install
packages or tools, deploy, or remediate findings. A missing or unusable tool is
`unavailable`, never a passing check.

## Required report fields

Every report includes:

- `review_mode`: `single-agent` or `escalated`;
- `escalation_reason`: the applicable condition(s), or `none` for the default
  path;
- `checks`: one entry per applicable check, with the exact `command` and
  `result`; an `unavailable` result also includes the `unavailable_reason`.
