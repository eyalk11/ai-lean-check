#!/usr/bin/env python3
"""Ask the coding agent once whether a failed result is worth publishing."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re


PROMPT_PATH = Path(".ai-lean-generate/publish-verdict-prompt.md")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def build_prompt() -> int:
    generated = [
        line.strip()
        for line in env("AI_LEAN_GENERATED_FILES").splitlines()
        if line.strip()
    ]
    listing = "\n".join(f"- `{name}`" for name in generated)
    diagnostics_path = Path(".ai-lean-generate/diagnostics.txt")
    diagnostics = (
        diagnostics_path.read_text(encoding="utf-8", errors="ignore")[-6000:]
        if diagnostics_path.is_file()
        else "(no diagnostics were recorded)"
    )
    PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_PATH.write_text(
        f"""# Publish verdict

Independent verification of the generated Lean files FAILED. The generated
files are:

{listing}

Verification diagnostics (tail):

```
{diagnostics}
```

Question: is this partial result still worth opening a pull request for human
review -- for example, correct statements whose proofs broke, a compiling
skeleton with real coverage, or a failure a human can finish from here? Answer
NO when a reviewer's time would be wasted: junk or empty files, content
unrelated to the task, or a failure that guts the mathematical substance.

You may read the repository files to decide. Reply with exactly one word as
your final message: YES or NO.
""",
        encoding="utf-8",
    )
    return 0


def verdict_text(execution_file: Path) -> str:
    """Final answer text from a claude-code execution log, tolerantly."""
    try:
        events = json.loads(execution_file.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(events, list):
        return ""
    texts: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            return event["result"]
        if event.get("type") == "assistant":
            content = event.get("message", {}).get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts.extend(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
    return texts[-1] if texts else ""


def decide(text: str) -> bool:
    """Interpret the reply, defaulting to no on anything unclear.

    The final word wins: even a model told to answer with one word may
    narrate first, and the answer comes last. Publishing a failed result is
    the exceptional path, so every ambiguity resolves to no.
    """
    words = re.findall(r"[A-Za-z]+", text)
    return bool(words) and words[-1].lower() == "yes"


def parse() -> int:
    execution_file = env("VERDICT_EXECUTION_FILE").strip()
    text = verdict_text(Path(execution_file)) if execution_file else ""
    value = "true" if decide(text) else "false"
    output_path = env("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"publish-on-failure={value}\n")
    print(f"publish-on-failure={value} (reply: {text.strip()[:200]!r})")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prompt", action="store_true")
    mode.add_argument("--parse", action="store_true")
    arguments = parser.parse_args()
    raise SystemExit(build_prompt() if arguments.prompt else parse())
