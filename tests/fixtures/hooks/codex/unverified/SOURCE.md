# Codex CLI hook fixtures — source: "unverified" (no documented substrate)

`configs/codex/AGENTS.md` states: "Codex and Antigravity have no event-hook
substrate." No Codex hook contract exists anywhere in this repository — there
is nothing to vendor a real protocol shape from. This directory holds one
generic payload shape only to exercise the adapter's bounded/structural
stdin handling; it is not a claim that Codex sends this shape.

Every event this adapter receives reports `{"coverage": "unsupported"}`.
