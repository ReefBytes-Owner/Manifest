"""Guard the "zero checks resolved" shape shared by `check` and `check --list`.

`resolve_checks(registry, profile, group)` can legitimately return an empty
tuple when a profile's checks simply don't intersect the requested group
(verified pre-C6b for `full` x `security`: zero checks, zero coverage_pending
obligations owned by an empty selection). Both `_report`/`run_profile` and
`_list_report` would otherwise read that as an honest PASS -- an empty
receipt about nothing. Both call sites gate on this explicitly instead of
silently proceeding to a synthesize-from-nothing status.
"""

from __future__ import annotations

from .models import CheckSpec


class EmptyGroupError(ValueError):
    """A profile resolves to zero checks in the requested group."""


def guard_nonempty_group(
    profile: str, group: str | None, checks: tuple[CheckSpec, ...]
) -> None:
    if group is not None and not checks:
        raise EmptyGroupError(f"profile {profile} selects no checks in group {group}")
