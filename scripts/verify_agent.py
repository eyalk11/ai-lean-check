#!/usr/bin/env python3
"""Run agent verification with lexical scanning over Lean code only."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re


SCRIPT = Path(__file__).with_name("ai_lean_check.py")
SPEC = importlib.util.spec_from_file_location("ai_lean_check_impl", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"could not load verifier implementation from {SCRIPT}")

impl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(impl)


def validate_lean_source(source: str) -> list[str]:
    code = impl.lean_code_without_comments_or_strings(source)
    problems: list[str] = []
    for pattern, label in impl.FORBIDDEN.items():
        if re.search(pattern, code, flags=re.IGNORECASE):
            problems.append(f"forbidden construct: {label}")
    for module in impl.lines(impl.env("AI_LEAN_IMPORTS")):
        if not re.search(rf"(?m)^\s*import\s+{re.escape(module)}\s*$", code):
            problems.append(f"missing required import: {module}")
    return problems


impl.validate = validate_lean_source
raise SystemExit(impl.verify_agent_result())
