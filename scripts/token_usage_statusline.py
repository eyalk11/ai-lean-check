#!/usr/bin/env python3
"""Report the coding agent's cumulative token use. Reporting only -- no cap.

Claude Code invokes this on every status-line update with session JSON on
stdin. It prints a one-line summary and persists the running totals to
.ai-lean-check/token-usage.json so a finished run can be audited from the
uploaded artifact.

Totals are summed from the transcript rather than read from `context_window.*`:
those fields report the most recent API response only, so they undercount a
long session.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

WORK = Path(".ai-lean-check")

FIELDS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_write": "cache_creation_input_tokens",
    "cache_read": "cache_read_input_tokens",
}


def sum_usage(transcript: str) -> dict[str, int]:
    """Cumulative token counts over every usage block in the transcript."""
    totals = {key: 0 for key in FIELDS}
    if not transcript:
        return {**totals, "total": 0}
    try:
        handle = open(transcript, encoding="utf-8", errors="ignore")
    except OSError:
        return {**totals, "total": 0}
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = (event.get("message") or {}).get("usage")
            if not isinstance(usage, dict):
                continue
            for key, source in FIELDS.items():
                value = usage.get(source)
                if isinstance(value, int):
                    totals[key] += value
    totals["total"] = sum(totals.values())
    return totals


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    usage = sum_usage(payload.get("transcript_path") or "")
    # Never let a reporting-only status line break the agent's run.
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK / "token-usage.json").write_text(
            json.dumps(
                {**usage, "session_id": payload.get("session_id", "")}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        print(f"tokens (unwritable: {error})")
        return 0

    print(
        f"tokens {usage['total']} "
        f"(in {usage['input']} out {usage['output']} "
        f"cache {usage['cache_read'] + usage['cache_write']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
