# Code Audit Dispatch Selection

Use one capable reviewer by default. Add an independent reviewer only when the
review crosses a trust boundary, covers destructive behavior, has broad
compatibility or deployment impact, contains conflicting evidence or unresolved
uncertainty, or requires genuinely independent codebase-wide analysis tracks.

Counts of files, packages, modules, languages, keywords, or units never
independently cause escalation. Record `review_mode`, `escalation_reason`, and
each exact check command/result; unavailable checks include an
`unavailable_reason` and do not pass.

This reference defines dispatch selection only. A dispatched reviewer completes
its assigned review directly and does not re-dispatch.
