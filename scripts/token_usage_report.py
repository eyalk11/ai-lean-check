#!/usr/bin/env python3
"""Summarise the coding agent's token use after a run. Reporting only.

Reads the base action's execution log and writes
`.ai-lean-check/token-usage.json` so a finished run carries its own cost
record in the uploaded artifact.

This replaces an earlier attempt to report the same numbers from a Claude Code
status line. The status line never fired: it is a feature of the interactive
terminal UI, and the base action runs Claude headlessly, so nothing invoked it
and no usage file was produced. The execution log is written unconditionally,
so reading it afterwards works.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

WORK = Path(".ai-lean-check")

FIELDS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_write": "cache_creation_input_tokens",
    "cache_read": "cache_read_input_tokens",
}


def events(payload: object) -> list:
    """The execution log is a bare list in some versions, wrapped in others."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("messages", "events", "log"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


MODEL_FIELDS = {
    "input": "inputTokens",
    "output": "outputTokens",
    "cache_write": "cacheCreationInputTokens",
    "cache_read": "cacheReadInputTokens",
}


def from_model_usage(result: dict) -> tuple[dict, dict] | None:
    """Per-model totals from the result event's `modelUsage`.

    This is the authoritative source: its per-model `costUSD` values sum to
    `total_cost_usd` exactly. Summing the per-assistant-event `usage` blocks
    instead gets two things wrong -- those blocks carry partial streaming
    output counts (tens of tokens each), and their cache reads repeat across
    several events per turn, so output comes out ~45x low and cache reads
    roughly 2x high.
    """
    model_usage = result.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        return None
    totals = {key: 0 for key in FIELDS}
    per_model = {}
    for name, stats in model_usage.items():
        if not isinstance(stats, dict):
            continue
        entry = {}
        for key, source in MODEL_FIELDS.items():
            value = stats.get(source)
            if isinstance(value, int):
                totals[key] += value
                entry[key] = value
        if isinstance(stats.get("costUSD"), (int, float)):
            entry["cost_usd"] = stats["costUSD"]
        per_model[name] = entry
    return totals, per_model


def summarise(path: Path) -> dict:
    empty = {key: 0 for key in FIELDS}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return {**empty, "total": 0, "turns": 0, "cost_usd": None, "per_model": {}}

    all_events = events(payload)
    results = [
        e for e in all_events if isinstance(e, dict) and e.get("type") == "result"
    ]
    result = results[-1] if results else {}

    turns = result.get("num_turns")
    if not isinstance(turns, int):
        turns = sum(
            1 for e in all_events if isinstance(e, dict) and e.get("type") == "assistant"
        )
    cost = result.get("total_cost_usd")
    if not isinstance(cost, (int, float)):
        cost = None

    per_model: dict = {}
    resolved = from_model_usage(result)
    if resolved is not None:
        totals, per_model = resolved
    elif isinstance(result.get("usage"), dict):
        # Older logs carry cumulative scalars on result.usage but no modelUsage.
        totals = {key: 0 for key in FIELDS}
        for key, source in FIELDS.items():
            value = result["usage"].get(source)
            if isinstance(value, int):
                totals[key] = value
    else:
        # Last resort only; known to misreport, see from_model_usage.
        totals = {key: 0 for key in FIELDS}
        for event in all_events:
            if not isinstance(event, dict):
                continue
            usage = (event.get("message") or {}).get("usage")
            if isinstance(usage, dict):
                for key, source in FIELDS.items():
                    value = usage.get(source)
                    if isinstance(value, int):
                        totals[key] += value

    totals["total"] = sum(totals[key] for key in FIELDS)
    totals["turns"] = turns
    totals["cost_usd"] = cost
    totals["per_model"] = per_model
    return totals


def main() -> int:
    candidates = [
        os.environ.get("AI_LEAN_EXECUTION_FILE", "").strip(),
        str(WORK / "claude-execution.json"),
    ]
    path = next((Path(c) for c in candidates if c and Path(c).is_file()), None)
    if path is None:
        print("no execution log found; skipping token usage report")
        return 0

    usage = summarise(path)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "token-usage.json").write_text(
        json.dumps(usage, indent=2) + "\n", encoding="utf-8"
    )

    cost = "unknown" if usage["cost_usd"] is None else f"${usage['cost_usd']:.2f}"
    print(
        f"tokens {usage['total']:,} "
        f"(in {usage['input']:,} out {usage['output']:,} "
        f"cache {usage['cache_read'] + usage['cache_write']:,}) "
        f"turns {usage['turns']} cost {cost}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
