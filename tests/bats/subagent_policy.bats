#!/usr/bin/env bats
# Feature 367 — Sub-agent dispatch guidance enforcement.
# Verifies every skill has a `subagents` disposition in tool_policies and that
# SKILL.md prose triggers do not contradict it. Skills are enumerated
# DYNAMICALLY (no hardcoded count), so a new skill without a disposition fails
# here until it is classified. See configs/claude/references/sub-agent-dispatch.md.

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"

# Run a named check implemented in the embedded Python helper; the helper exits
# non-zero and prints offending skills on failure.
run_check() {
    python3 - "$REPO_ROOT" "$1" <<'PY'
import os, re, sys
import yaml

repo, check = sys.argv[1], sys.argv[2]
skills_dir = os.path.join(repo, ".apm/skills")
cfg = os.path.join(repo, "configs/claude/config/command_config.yml")

skills = sorted(
    d for d in os.listdir(skills_dir)
    if os.path.isfile(os.path.join(skills_dir, d, "SKILL.md"))
)
with open(cfg, encoding="utf-8") as fh:
    tp = (yaml.safe_load(fh) or {}).get("tool_policies", {}) or {}
VALID = {"always", "conditional", "never"}
VALID_MODELS = {"haiku", "sonnet", "opus", "charter"}
VALID_SESSION_MODELS = {"opus"}
DISPATCHING = ("always", "conditional")
MARKER = "## Sub-agent dispatch"
SESSION_MARKER = "## Session model"

def body(s):
    with open(os.path.join(skills_dir, s, "SKILL.md"), encoding="utf-8") as fh:
        return fh.read()

def entry(s):
    return tp.get(s) or {}

def dispatch_section(s):
    """The '## Sub-agent dispatch' section body, or '' when absent."""
    out, on = [], False
    for line in body(s).splitlines():
        if line.startswith(MARKER):
            on = True
            continue
        if on and line.startswith("## "):
            break
        if on:
            out.append(line)
    return "\n".join(out)

fail = []

if check == "coverage":          # T1
    for s in skills:
        if "subagents" not in entry(s):
            fail.append(f"{s}: no `subagents` disposition in tool_policies")
elif check == "enum":            # T2
    for s in skills:
        v = entry(s).get("subagents")
        if v is not None and v not in VALID:
            fail.append(f"{s}: invalid subagents value {v!r}")
elif check == "conditional_trigger":   # T3
    for s in skills:
        e = entry(s)
        if e.get("subagents") == "conditional" and not e.get("subagent_trigger"):
            fail.append(f"{s}: conditional but no subagent_trigger")
elif check == "never_rationale":       # T4
    for s in skills:
        e = entry(s)
        if e.get("subagents") == "never":
            if not e.get("subagent_rationale") and "Sub-agents: not used" not in body(s):
                fail.append(f"{s}: never but no rationale (config or SKILL.md)")
elif check == "body_trigger":          # T5
    local_dispatch_ref = re.compile(
        r"\[[^\]]+\]\((references/[A-Za-z0-9_-]+-dispatch\.md)\)"
    )
    shared_dispatch_ref = "sub-agent-dispatch.md"
    for s in skills:
        if entry(s).get("subagents") not in ("always", "conditional"):
            continue
        b = body(s)
        if MARKER not in b:
            fail.append(f"{s}: {entry(s)['subagents']} but no '{MARKER}' section")
            continue
        section = dispatch_section(s)
        match = local_dispatch_ref.search(section)
        if match:
            path = os.path.join(skills_dir, s, match.group(1))
            if not os.path.isfile(path):
                fail.append(
                    f"{s}: local dispatch reference does not resolve: {match.group(1)}"
                )
        elif shared_dispatch_ref not in section:
            fail.append(
                f"{s}: dispatch section does not link a selection-reference contract"
            )
elif check == "no_contradiction":      # T6
    # Two ways a `never` skill can contradict its disposition. The section
    # heading is the obvious one; the DISPATCH ITSELF is the one that actually
    # leaked. plan-manage declared `never` — which exempts it from the T7/T8
    # model-pin gate — while SKILL.md step 4 dispatched
    # `Task(subagent_type: "general-purpose")` with no model. It carried no
    # `## Sub-agent dispatch` heading, so the heading-only check passed it, and
    # the one dispatch it made inherited the session's premium model unchecked.
    # Matching the call syntax closes the exemption for good.
    DISPATCH_RE = re.compile(r"subagent_type\s*[:=]", re.I)
    for s in skills:
        if entry(s).get("subagents") != "never":
            continue
        b = body(s)
        if MARKER in b:
            fail.append(f"{s}: never but body contains a '{MARKER}' section")
        hits = [i + 1 for i, line in enumerate(b.splitlines()) if DISPATCH_RE.search(line)]
        if hits:
            fail.append(
                f"{s}: never but body dispatches a sub-agent at line(s) "
                f"{', '.join(map(str, hits))} (subagent_type=...) — reclassify as "
                f"conditional and pin subagent_model, or remove the dispatch"
            )
elif check == "model_pinned":          # T7
    # A dispatch site that names no model inherits the parent session's model,
    # which bills the premium main-loop tier for fan-out work. Measured
    # 2026-07-25: $845/yr of avoidable premium sub-agent spend, $643 of it from
    # inherited Fable 5 alone. Enumerated from the disposition, not a name list.
    for s in skills:
        e = entry(s)
        if e.get("subagents") not in DISPATCHING:
            continue
        m = e.get("subagent_model")
        if m is None:
            fail.append(f"{s}: {e['subagents']} but no subagent_model (default: sonnet)")
        elif m not in VALID_MODELS:
            fail.append(f"{s}: invalid subagent_model {m!r} (expected one of {sorted(VALID_MODELS)})")
elif check == "model_in_body":         # T8
    # The dispatch prose must state the same model the config pins, so a reader
    # of SKILL.md alone cannot dispatch on the inherited model by accident.
    for s in skills:
        e = entry(s)
        if e.get("subagents") not in DISPATCHING:
            continue
        m = e.get("subagent_model")
        if m not in VALID_MODELS:
            continue                    # already reported by T7
        sec = dispatch_section(s).lower()
        needle = "cddl-role-models.md" if m == "charter" else m
        if needle not in sec:
            fail.append(f"{s}: dispatch section does not name the pinned model ({m!r}; expected {needle!r})")
elif check == "session_model":         # T9
    # The `fable` session tier was retired 2026-08-17; Opus (1M) is now both the
    # top tier and the default, so no skill can name a costlier session model.
    # Any session_model pin must still be a known tier and justify itself.
    # Enumerated from the field's presence, not a name list.
    for s in skills:
        e = entry(s)
        sm = e.get("session_model")
        if sm is None:
            if "session_model_rationale" in e:
                fail.append(f"{s}: session_model_rationale without session_model")
            continue
        if sm not in VALID_SESSION_MODELS:
            fail.append(f"{s}: invalid session_model {sm!r} (expected one of {sorted(VALID_SESSION_MODELS)})")
            continue
        if not e.get("session_model_rationale"):
            fail.append(f"{s}: session_model: {sm} but no session_model_rationale")
else:
    print(f"unknown check: {check}", file=sys.stderr)
    sys.exit(2)

if fail:
    print(f"{len(fail)} violation(s) for check '{check}':", file=sys.stderr)
    for f in fail:
        print("  - " + f, file=sys.stderr)
    sys.exit(1)
print(f"check '{check}' OK ({len(skills)} skills)")
PY
}

@test "every skill has a subagents disposition (dynamic coverage)" {
    run run_check coverage
    [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "subagents values are valid enums" {
    run run_check enum
    [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "conditional skills declare a subagent_trigger" {
    run run_check conditional_trigger
    [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "never skills carry a rationale" {
    run run_check never_rationale
    [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "always/conditional skills have an in-body selection-reference contract" {
    run run_check body_trigger
    [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "never skills do not instruct dispatch (no contradiction)" {
    run run_check no_contradiction
    [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "always/conditional skills pin a sub-agent model (no inherit-by-accident)" {
    run run_check model_pinned
    [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "dispatch prose names the same model the config pins" {
    run run_check model_in_body
    [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "session_model pins are valid and carry a rationale" {
    run run_check session_model
    [ "$status" -eq 0 ] || { echo "$output"; false; }
}
