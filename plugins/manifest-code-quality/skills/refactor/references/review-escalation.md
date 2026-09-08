# Review Escalation Contract

Use one capable reviewing agent by default. Run only applicable verification
that satisfies the execution-safety rules below.

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

Treat the reviewed checkout as untrusted. Outside verified isolation, run only
trusted, preinstalled static tools that treat checkout files as data, with
checkout-controlled executable configuration, plugins, hooks, imports, and
executable discovery disabled. Project-controlled tests, scripts, build steps,
and checks that load project code require enforced execution isolation.

Isolation must protect the original checkout and host files, expose no
credentials or host control sockets, deny unauthorized network access, and
confine writes to disposable storage. Source inspection, check-only flags, a
changed `HOME`, a temporary directory, or a read-only checkout do not establish
isolation. If isolation is unavailable or uncertain, skip that execution and
report `unavailable` with the reason while continuing safe static analysis.

Checks must not apply automatic fixes, write formatting changes, install
packages or tools, deploy, or remediate findings. A missing or unusable tool is
`unavailable`, never a passing check.

## Required report fields

Every report includes:

- `review_mode`: `single-agent` or `escalated`;
- `escalation_reason`: the applicable condition(s), or `none` for the default
  path;
- `checks`: one entry per applicable check, with the exact `command` and
  `result`; an `unavailable` result also includes the `unavailable_reason`.
