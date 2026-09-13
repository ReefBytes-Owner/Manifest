---
name: security-refute-findings
description: Adversarially verify candidate security findings to cut false positives before reporting, via attacker/victim privilege-boundary analysis and diff-anchoring; emphasizes reframing removed/delegated controls. See security-triage-findings for the broad refutation-gate catalog.
---
# Security Finding Refutation

A second-pass verification stage: given candidate vulnerabilities (from a finder pass or another tool),
try to *disprove* each one. Default to SURVIVES — refute only with cited evidence, never speculation. The goal is
to eliminate plausible-but-wrong findings, not to agree.

1. **Frame attacker vs victim first.** For each candidate, name who controls the malicious input (attacker) and
who is harmed (victim). REFUTE if the only victim is the attacker on their own machine at their own privilege
(input from env var, CLI arg, `$HOME` dotfile, OS-user config, self-gating advisory return). KEEP if the attacker
is a legitimate user/tenant but the impact reaches *other* users, shared infra, or server-side resources.
2. **Never apply the no-privilege-boundary refutation to:** SSRF/outbound-network sinks; LLM-agent capability
gates (PreToolUse/PostToolUse hooks, bash allow/denylists, path jails — the model is the attacker, the user the
victim); data-exposure findings (CWE-200/532, secrets-in-logs — the question is who READS the sink);
project-working-directory config (repo author ≠ repo cloner); cross-process metadata (`/proc/<pid>`,
`psutil.Process` — different owner = different principal).
3. **Diff-anchor each candidate.** If the cited vulnerable code does NOT appear on a `+` line in the diff, it is
pre-existing context the change did not introduce — refute as PRE-EXISTING. For findings whose sink is outside
the diff (`off_diff`), demand stricter evidence: you must name the specific `+`/`-` line that *enables* the sink
(a removed guard, a new caller, a changed argument). If you cannot name that line, refute it.
4. **Read the cited file** and look for a refutation: a sanitizer/validator/authz check on the path; a
non-dangerous sink (typed-schema decoder, hardcoded URL with non-path params, statically numeric/boolean value);
delegated validation (credential forwarded to an upstream that validates); control-moved-to-library (the diff
removes a control but bumps a dep documented to provide it); a config/feature-flag gate with no per-request user
control.
5. **Check for in-file documentation that reframes the finding.** A comment block can establish that a "removed
control" was never functional, or was delegated elsewhere. (Real example: a finding claimed an HTTPS+basic-auth
Docker endpoint was replaced by plaintext `tcp://...:2375`; an in-file comment documented that the client only
ever supported `tcp://`/`unix://` so the HTTPS form was a non-functional placeholder, and the new endpoint rides
a Tailscale/WireGuard tunnel providing encryption + device auth — both "removed controls" were delegated, not
deleted.)
6. **Process candidates in anchor order** (`in_diff` before `off_diff`), and refute any off_diff candidate whose
sink is already covered by a surviving in_diff candidate.
7. **Return two lists:** the indices that SURVIVED (could not be refuted) and `{idx, reason}` records for each
refuted one, each reason citing concrete `file:line` evidence. An empty survived list is a valid, common outcome.

## Sub-agent dispatch

Follow the [finding refutation dispatch rules](references/security-refute-findings-dispatch.md).
Use the pinned `opus` model. For three or more candidate findings, refute one
finding per review unit and aggregate structured verdicts. Below that threshold,
refute inline; if structured output is unavailable, report `DEGRADED`.

When dispatching, invoke `manifest-workspace:parallel-agent` with one candidate
per review unit and aggregate its structured verdicts.
