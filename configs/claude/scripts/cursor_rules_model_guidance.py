#!/usr/bin/env python3
"""Batch model-guidance computation for `generate_cursor_rules.sh` (C7h /
Correction 6 step 5).

The generator used to launch up to two `python3` processes PER skill (a
`python3 -c 'import yaml'` capability probe, then a heredoc script) inside
a ~123-skill loop. Under the runner's cache env
(`PYTHONDONTWRITEBYTECODE` + `PYTHONPYCACHEPREFIX` + `XDG_CACHE_HOME`
together -- no `.pyc` cache to reuse between launches) each `python3`
startup got slow enough that the whole loop crossed the 20s check
timeout, even though the same script finished in well under a second
ambient. This script does the SAME per-skill work in exactly ONE `python3`
process: the shell script probes python3/pyyaml availability once, runs
this script once, and reads its output into an associative array instead
of invoking python3 again for every skill directory.

Output: for every skill directory with a `SKILL.md` AND a non-empty
`cursor` model chain, one file `<output-dir>/<skill_name>` holding the
exact guidance text the old per-skill heredoc printed (no file at all if
the skill declares no `cursor` chain -- the caller's lookup is then just
"does this file exist"). One file per skill, not a single delimited
stream, so the shell caller (bash 3.2 -- no associative arrays; see the
`shfmt -ln bash` convention already used elsewhere in this repo) never has
to parse or escape guidance text, which can itself contain backticks and
quotes. A malformed `models:` block is reported on stderr and that
skill's guidance file is simply not written, exactly like the old
per-skill try/except -- one bad SKILL.md must not abort generation for
every other skill.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from manifest_model_policy import (
    ModelFallbackMode,
    ModelPolicyError,
    parse_skill_model_policy,
)


def _guidance_for(skill_file: Path, skill_name: str) -> str:
    try:
        policy = parse_skill_model_policy(skill_file)
    except ModelPolicyError as error:
        print(f"{skill_name}: {error}", file=sys.stderr)
        return ""
    tiers = policy.chains.get("cursor")
    if not tiers:
        return ""
    mode = (policy.fallback_mode or ModelFallbackMode.CONFIRM).value
    return (
        "Model-aware invocation: `manifest skill-run "
        f"{skill_name} --harness cursor "
        f"--model-chain {','.join(tiers)} --model-fallback {mode}`."
    )


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: cursor_rules_model_guidance.py <skills-dir> <output-dir>",
            file=sys.stderr,
        )
        return 2
    skills_dir, output_dir = Path(argv[1]), Path(argv[2])
    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue
        guidance = _guidance_for(skill_file, skill_dir.name)
        if guidance:
            (output_dir / skill_dir.name).write_text(guidance, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
