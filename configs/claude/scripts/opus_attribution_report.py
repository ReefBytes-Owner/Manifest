#!/usr/bin/env python3
"""opus_attribution_report.py - classify API requests by task class x model.

Attributes deduplicated API requests across Claude Code transcripts to a task
class AND the model that served them, so a model-routing proposal can be costed
against real spend -- and so a LANDED routing change can be verified against
behaviour rather than against the config file that declares it.

The class x model matrix is the verification query: `--since <change-point>
--models all` answers "did subagent x fable go to zero?" in one command.

Usage:
  opus_attribution_report.py [--root DIR] [--until ISO8601] [--since ISO8601]
                             [--models SPEC] [--json PATH] [--top-projects N]
  --models  comma-separated substrings of the model id (default: opus),
            or `all` for every model in the window.
Exit codes: 0 ok, 2 usage / unusable input (bad flag, bad window, bad root).
"""

import argparse
import collections
import json
import os
import statistics
import sys
from datetime import UTC, datetime

try:
    import model_pricing
except ModuleNotFoundError:  # partial deploy — --help must still answer
    model_pricing = None

DEFAULT_ROOT = "~/.claude/projects"
DEFAULT_MODELS = "opus"

# Tools whose use is mechanical / read-mostly, versus tools that mutate files.
MECHANICAL_TOOLS = {
    "Bash",
    "BashOutput",
    "Glob",
    "Grep",
    "LS",
    "NotebookRead",
    "Read",
    "TodoWrite",
}
EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit", "Write"}


# Anthropic relative price multipliers, expressed against fresh input = 1.0.
# Sourced from model_pricing so the weighting here and the dollar figures in
# token_cost_report.py cannot drift apart.
# Resolved lazily: `--help` must succeed before any dependency lookup, including
# the sibling price table (docs/CODING_STANDARDS.md). Absent, every priced path
# fails loudly rather than silently reporting $0.
def _pricing():
    if model_pricing is None:
        die(
            2,
            "model_pricing.py must sit beside this script "
            "(deploy the whole configs/claude/scripts/ directory)",
        )
    return model_pricing


def die(code, msg):
    print(f"opus-attribution-report: {msg}", file=sys.stderr)
    sys.exit(code)


def parse_ts(raw):
    """Parse an ISO8601 transcript timestamp or --since/--until bound.

    Returns None when absent or unparseable. Bounds MUST be compared as
    datetimes, never as strings: a plain string compare silently accepts
    garbage (``"2026-..." > "banana"`` is False), which would leave the
    window unbounded and make a "reproducible" snapshot drift.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def parse_args(argv):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--root", default=DEFAULT_ROOT)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--json")
    p.add_argument("--models", default=DEFAULT_MODELS)
    p.add_argument("--top-projects", type=int, default=10)
    return p.parse_args(argv)


def parse_models(spec):
    """Turn a --models spec into a match predicate.

    ``all`` selects everything; otherwise each comma-separated term is a
    case-insensitive substring of the model id. Substrings (not exact ids) so
    ``opus`` keeps selecting every Opus generation, which is what the default
    has always meant.
    """
    terms = [t.strip().lower() for t in spec.split(",") if t.strip()]
    if not terms:
        return None
    if "all" in terms:
        return lambda _model: True
    return lambda model: any(t in (model or "").lower() for t in terms)


def collect(root, since, until):
    """Fold assistant JSONL lines into one record per API request.

    A single API response is written to the transcript as one line per content
    block, and EVERY one of those sibling lines repeats a usage object. Summing
    per line therefore multiply-counts one request (~2.2x on real corpora).
    Dedup by requestId, and note the two fields behave differently:
      - input/cache_read/cache_creation are per-request constants -> take first
      - output_tokens is CUMULATIVE across the streamed blocks   -> take max
    """
    by_request = {}
    files = lines = skipped_no_timestamp = 0
    for dirpath, _, names in os.walk(root):
        project = os.path.basename(dirpath)
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            files += 1
            try:
                with open(os.path.join(dirpath, name), errors="replace") as handle:
                    kept, skipped = fold_file(handle, project, by_request, since, until)
            except OSError:
                continue
            lines += kept
            skipped_no_timestamp += skipped
    return by_request, files, lines, skipped_no_timestamp


def fold_file(handle, project, by_request, since, until):
    """Fold one transcript file into `by_request`; return (kept, skipped) counts."""
    kept = skipped_no_timestamp = 0
    for line in handle:
        if '"assistant"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant":
            continue
        message = rec.get("message") or {}
        usage = message.get("usage") or {}
        if not usage:
            continue
        if since or until:
            stamp = parse_ts(rec.get("timestamp"))
            if stamp is None:
                skipped_no_timestamp += 1
                continue
            if since and stamp < since:
                continue
            if until and stamp > until:
                continue
        kept += 1
        key = rec.get("requestId") or message.get("id")
        entry = by_request.get(key)
        if entry is None:
            entry = by_request[key] = {
                "model": message.get("model", "?"),
                "project": project,
                "sidechain": bool(rec.get("isSidechain")),
                "input": usage.get("input_tokens") or 0,
                "cache_read": usage.get("cache_read_input_tokens") or 0,
                "cache_creation": usage.get("cache_creation_input_tokens") or 0,
                "output": 0,
                "types": set(),
                "tools": set(),
                "lines": 0,
            }
        entry["lines"] += 1
        entry["output"] = max(entry["output"], usage.get("output_tokens") or 0)
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind:
                entry["types"].add(kind)
            if kind == "tool_use" and block.get("name"):
                entry["tools"].add(block["name"])
    return kept, skipped_no_timestamp


def classify(entry):
    """Assign one task class, most specific first."""
    if entry["sidechain"]:
        return "subagent"
    # "fallback" marks a harness model switch, not work the model did.
    kinds = entry["types"] - {"fallback"}
    if kinds and kinds <= {"tool_use"}:
        if entry["tools"] & EDIT_TOOLS:
            return "tool_edit"
        if entry["tools"] and entry["tools"] <= MECHANICAL_TOOLS:
            return "tool_mechanical"
        return "other"
    if "thinking" in kinds:
        return "reasoning"
    if kinds == {"text"}:
        return "text_response"
    if "text" in kinds and "tool_use" in kinds:
        return "mixed"
    return "other"


def build_matrix(cells):
    """Cost every (class, model) cell; report unpriced models rather than $0.

    Returns (matrix, unpriced) where matrix is {class: {model: usage+cost}},
    emitted in sorted order so a committed --json snapshot is byte-stable, and
    unpriced counts requests whose model has no entry in the price table.
    """
    matrix = {}
    unpriced = collections.Counter()
    for (name, model), cell in sorted(cells.items()):
        cost = _pricing().cost_usd(
            model,
            input_tokens=cell["input"],
            output_tokens=cell["output"],
            cache_read=cell["cache_read"],
            cache_creation=cell["cache_creation"],
        )
        if cost is None:
            unpriced[model] += cell["requests"]
        matrix.setdefault(name, {})[model] = {
            "requests": cell["requests"],
            "input": cell["input"],
            "output": cell["output"],
            "cache_read": cell["cache_read"],
            "cache_creation": cell["cache_creation"],
            "weighted_input_units": round(
                _pricing().weighted_input_units(
                    cell["input"], cell["cache_read"], cell["cache_creation"]
                ),
                1,
            ),
            "cost_usd": None if cost is None else round(cost, 2),
        }
    return matrix, unpriced


def print_matrix(matrix, unpriced):
    """Print every populated cell -- no top-N cap.

    A truncated matrix reads as "nothing else was there", which is the exact
    failure mode this report exists to close, so every row is printed.
    """
    rows = [
        (name, model, v)
        for name, per_model in matrix.items()
        for model, v in per_model.items()
    ]
    # Unpriced cells sort last (cost None), then by cost descending.
    rows.sort(key=lambda r: (r[2]["cost_usd"] is None, -(r[2]["cost_usd"] or 0)))
    print()
    print(f"{'class':<16}{'model':<26}{'reqs':>8}{'cost':>12}{'output':>13}")
    for name, model, v in rows:
        cost = "unpriced" if v["cost_usd"] is None else f"${v['cost_usd']:,.2f}"
        print(f"{name:<16}{model:<26}{v['requests']:>8,}{cost:>12}{v['output']:>13,}")
    if unpriced:
        detail = ", ".join(f"{m} ({n:,} reqs)" for m, n in unpriced.most_common())
        print(f"\nUNPRICED (excluded from every cost figure above): {detail}")


def main(argv):
    if argv and argv[0] in ("--help", "-h"):
        print(__doc__.strip())
        return 0
    args = parse_args(argv)
    root = os.path.expanduser(args.root)
    if not os.path.isdir(root):
        die(2, f"transcript root not found: {root}")

    # Validate bounds up front: an unparseable --until must be a hard error,
    # not a silently-unbounded scan that still exits 0.
    bounds = {}
    for flag in ("since", "until"):
        raw = getattr(args, flag)
        if raw is None:
            bounds[flag] = None
            continue
        parsed = parse_ts(raw)
        if parsed is None:
            die(2, f"invalid --{flag} timestamp: {raw!r} (expected ISO8601)")
        bounds[flag] = parsed
    if bounds["since"] and bounds["until"] and bounds["since"] > bounds["until"]:
        die(2, f"--since {args.since!r} is after --until {args.until!r}")

    selects = parse_models(args.models)
    if selects is None:
        die(2, f"--models {args.models!r} selects nothing (try `all`)")

    by_request, files, lines, skipped = collect(root, bounds["since"], bounds["until"])

    per_class = collections.defaultdict(collections.Counter)
    outputs = collections.defaultdict(list)
    per_project = collections.defaultdict(collections.Counter)
    models = collections.Counter()
    selected = collections.Counter()
    # (class, model) -> usage. This cell is the unit a routing lever moves:
    # lever 1 is "subagent x <premium model> goes to zero", which no
    # class-only or model-only aggregate can express.
    cells = collections.defaultdict(collections.Counter)

    for entry in by_request.values():
        models[entry["model"]] += 1
        if not selects(entry["model"]):
            continue
        name = classify(entry)
        bucket = per_class[name]
        cell = cells[(name, entry["model"])]
        bucket["requests"] += 1
        cell["requests"] += 1
        for field in ("input", "output", "cache_read", "cache_creation"):
            bucket[field] += entry[field]
            cell[field] += entry[field]
            selected[field] += entry[field]
        selected["requests"] += 1
        outputs[name].append(entry["output"])
        per_project[entry["project"]]["requests"] += 1
        per_project[entry["project"]][name] += 1

    if not selected["requests"]:
        die(2, f"no requests matching --models {args.models!r} in the scanned window")

    matrix, unpriced = build_matrix(cells)

    classes = {}
    for name, bucket in per_class.items():
        weighted = (
            bucket["input"]
            + bucket["cache_read"] * _pricing().CACHE_READ_MULTIPLIER
            + bucket["cache_creation"] * _pricing().CACHE_WRITE_MULTIPLIER
        )
        ordered = sorted(outputs[name])
        # None (not 0.0) when ANY model in the class is unpriced: a partial sum
        # presented as the class total would understate spend invisibly.
        per_model = matrix.get(name, {})
        costs = [v["cost_usd"] for v in per_model.values()]
        classes[name] = {
            "requests": bucket["requests"],
            "pct_requests": round(100 * bucket["requests"] / selected["requests"], 2),
            "input": bucket["input"],
            "output": bucket["output"],
            "cache_read": bucket["cache_read"],
            "cache_creation": bucket["cache_creation"],
            "weighted_input_units": round(weighted, 1),
            "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 2),
            "median_output": statistics.median(ordered),
            "p90_output": ordered[int(0.9 * len(ordered))],
        }

    classified = selected["requests"] - classes.get("other", {}).get("requests", 0)
    report = {
        "scan": {
            "root": root,
            "since": args.since,
            "until": args.until,
            "models_filter": args.models,
            # NOTE: the raw files-walked count is deliberately NOT recorded
            # here. It grows with the corpus regardless of --until, so a
            # committed snapshot would show a spurious diff on every
            # regeneration. Only window-gated counts belong in the snapshot.
            "assistant_lines_with_usage": lines,
            "api_requests": len(by_request),
            "overcount_factor": round(lines / len(by_request), 4) if by_request else 0,
            "skipped_no_timestamp": skipped,
        },
        "models": dict(models.most_common()),
        # Renamed from "opus_totals": the selection is no longer Opus-only.
        "selected_totals": dict(selected),
        "classified_pct": round(100 * classified / selected["requests"], 2),
        "classes": classes,
        "class_model_matrix": matrix,
        "unpriced_models": dict(unpriced.most_common()),
        "pricing": {
            "cache_read_multiplier": _pricing().CACHE_READ_MULTIPLIER,
            "cache_write_multiplier": _pricing().CACHE_WRITE_MULTIPLIER,
            "rates_per_mtok": {
                m: list(_pricing().rates(m))
                for m in sorted(models)
                if selects(m) and _pricing().rates(m) is not None
            },
            "note": _pricing().INTRO_PRICING_NOTE,
        },
        "top_projects": {
            name: dict(counts)
            for name, counts in sorted(
                per_project.items(), key=lambda kv: -kv[1]["requests"]
            )[: args.top_projects]
        },
    }

    print(
        f"files={files}  assistant_lines={lines:,}  "
        f"api_requests={len(by_request):,}  overcount={report['scan']['overcount_factor']}x"
    )
    print(
        f"models={args.models!r}  selected requests={selected['requests']:,}  "
        f"classified={report['classified_pct']}%"
    )
    print()
    header = f"{'class':<16}{'reqs':>8}{'%':>7}{'output':>13}{'wtd_input':>16}{'med':>7}{'p90':>8}"
    print(header)
    for name, v in sorted(classes.items(), key=lambda kv: -kv[1]["requests"]):
        print(
            f"{name:<16}{v['requests']:>8,}{v['pct_requests']:>7.2f}"
            f"{v['output']:>13,}{v['weighted_input_units']:>16,.0f}"
            f"{v['median_output']:>7.0f}{v['p90_output']:>8,}"
        )
    print_matrix(matrix, unpriced)

    if args.json:
        try:
            with open(args.json, "w") as fh:
                json.dump(report, fh, indent=1)
                fh.write("\n")
        except OSError as exc:
            die(2, f"cannot write {args.json}: {exc}")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
