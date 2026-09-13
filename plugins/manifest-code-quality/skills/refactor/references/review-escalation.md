# Review Escalation Contract

Use one capable reviewing agent by default. Run only applicable verification in
check-only mode, treating the checkout as untrusted.

Add independent review when at least one of these conditions is present:

- a trust-boundary change, including authentication, authorization, cryptography,
  secret handling, or validation behavior;
- destructive data or infrastructure behavior;
- a public compatibility or deployment change with broad impact;
- conflicting evidence or unresolved reviewer uncertainty; or
- a codebase-wide investigation with genuinely independent analysis tracks.

File, package, module, language, keyword, and independent-unit counts never
independently escalate review. A multi-language target remains single-agent
unless it has one of the conditions above.

## Verification safety

Run only trusted preinstalled static tools that treat checkout files as data
outside verified isolation. Project-controlled tests, scripts, build steps, or
checkout-controlled executable configuration, plugins, hooks, imports, or
discovery require verified isolation. Source inspection, check-only flags, a
changed home, a temporary directory, and a read-only checkout are insufficient.

Never apply automatic fixes, write formatting changes, install packages or
tools, deploy, or remediate findings. If a check or isolation is unavailable,
skip it and report `unavailable`, never a passing check.

## Report contract

Every explicit report includes:

- `review_mode`: `single-agent` or `escalated`;
- `escalation_reason`: `none` or the concrete condition(s); and
- `checks`: one exact command/result record per applicable check. For an
  unavailable check, include `unavailable_reason` and result `unavailable`.
