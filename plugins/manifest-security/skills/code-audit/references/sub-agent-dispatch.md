# Code Audit Dispatch Selection

Use one capable reviewer by default. Add an independent reviewer only when at
least one of these conditions is present:

- authentication, authorization, cryptography, secret handling, or another
  trust-boundary change;
- destructive data or infrastructure behavior;
- a public compatibility or deployment change with broad impact;
- conflicting evidence or unresolved reviewer uncertainty; or
- a codebase-wide investigation with genuinely independent analysis tracks.

File count, line count, language, generic keywords, and independent-unit count
do not trigger dispatch by themselves. Pin dispatched reviewers to the
configured Sonnet tier. Give each reviewer one bounded lens and prohibit
re-dispatch.
