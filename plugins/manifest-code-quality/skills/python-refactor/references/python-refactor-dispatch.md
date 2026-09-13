# Python Refactor Dispatch Mechanics

This reference defines dispatch mechanics only. The shared
[review escalation contract](../../refactor/references/review-escalation.md)
exclusively determines whether to dispatch.

When escalation is required, use the configured pinned model for the independent
review. A dispatched reviewer completes its assigned review directly and does
not re-dispatch. If the configured dispatch mechanism is unavailable, complete
the same review inline and report the limitation.
