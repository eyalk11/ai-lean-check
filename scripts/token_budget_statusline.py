#!/usr/bin/env python3
"""Status line that meters and caps the coding agent's token use.

Claude Code invokes this on every status-line update with session JSON on
stdin. It reports cumulative token usage, persists it so a run can be audited
from the artifact, warns once the budget is nearly spent, and stops the agent
when the budget is exhausted.

Cumulative totals are summed from the transcript rather than read from
`context_window.*`: those fields report the most recent API response only, so
they undercount a long session.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

WORK = Path(".ai-lean-check")
WARN_FRACTION = 0.8


def read_budget() -> int:
    try:
        return max(0, int((WORK / "token-budget.conf").read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return 0


def sum_usage(transcript: str) -> dict[str, int]:
    """Cumulative token counts over every usage block in the transcript."""
    totals = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    fields = {
        "input": "input_tokens",
        "output": "output_tokens",
        "cache_write": "cache_creation_input_tokens",
        "cache_read": "cache_read_input_tokens",
    }
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
            for key, source in fields.items():
                value = usage.get(source)
                if isinstance(value, int):
                    totals[key] += value
    totals["total"] = sum(totals.values())
    return totals


def agent_pid() -> int | None:
    """The `claude` process this status line is a descendant of.

    Ancestry is the reliable handle: matching on process name alone would also
    match unrelated processes in the container.
    """
    pid = os.getppid()
    for _ in range(10):
        if pid <= 1:
            return None
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "ignore")
            if "claude" in cmdline.lower():
                return pid
            pid = int(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[3])
        except (OSError, IndexError, ValueError):
            return None
    return None


def stop_agent() -> None:
    target = agent_pid()
    if target is None:
        return
    try:
        os.kill(target, signal.SIGTERM)
    except OSError:
        return
    # Escalate out of band so the status line itself never blocks an update.
    try:
        subprocess.Popen(
            ["bash", "-c", f"sleep 10; kill -KILL {target} 2>/dev/null || true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    WORK.mkdir(parents=True, exist_ok=True)

    budget = read_budget()
    usage = sum_usage(payload.get("transcript_path") or "")
    (WORK / "token-usage.json").write_text(
        json.dumps(
            {**usage, "session_id": payload.get("session_id", ""), "max_tokens": budget},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    total, output = usage["total"], usage["output"]
    if not budget:
        print(f"tokens {total} (out {output})")
        return 0

    status = f"tokens {total}/{budget} (out {output})"
    state_path = WORK / "token-budget.state"
    stage = state_path.read_text(encoding="utf-8").strip() if state_path.is_file() else ""

    if total >= budget:
        if stage != "killed":
            state_path.write_text("killed\n", encoding="utf-8")
            print(
                f"::error::token budget exhausted: {total} >= {budget}; "
                "stopping the coding agent",
                file=sys.stderr,
            )
            stop_agent()
        status += "  BUDGET EXHAUSTED - stopping"
    elif total >= int(budget * WARN_FRACTION):
        if stage not in {"warned", "killed"}:
            state_path.write_text("warned\n", encoding="utf-8")
            print(
                f"::warning::token budget nearly spent: {total}/{budget}; "
                "wrap up and finish now",
                file=sys.stderr,
            )
        status += "  OVER 80% - wrap up"

    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
