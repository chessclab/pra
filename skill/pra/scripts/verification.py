"""Detect verification intent from tool metadata without retaining commands."""

from __future__ import annotations

import json
import re
from typing import Any

PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "test": (
        re.compile(r"\b(?:pytest|unittest|npm\s+(?:run\s+)?test|yarn\s+test|pnpm\s+(?:run\s+)?test|cargo\s+test|go\s+test|bun\s+test|vitest|jest)\b", re.I),
    ),
    "lint": (
        re.compile(r"\b(?:ruff|eslint|biome|flake8|pylint|golangci-lint|clippy)\b", re.I),
        re.compile(r"\b(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?lint\b", re.I),
    ),
    "typecheck": (
        re.compile(r"\b(?:mypy|pyright|tsc|typecheck|type-check)\b", re.I),
    ),
    "build": (
        re.compile(r"\b(?:build|compile|webpack|vite\s+build|tsup|cargo\s+build)\b", re.I),
        re.compile(r"\b(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?build\b", re.I),
    ),
    "diff": (
        re.compile(r"\bgit\s+(?:diff|show|status)\b", re.I),
    ),
    "review": (
        re.compile(r"\b(?:code[- ]review|review|security review|спроверь|ревью)\b", re.I),
    ),
}


def _argument_text(arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return arguments
        return _argument_text(parsed)
    if isinstance(arguments, dict):
        values: list[str] = []
        for key in ("command", "cmd", "query", "text", "prompt"):
            value = arguments.get(key)
            if isinstance(value, str):
                values.append(value)
        return "\n".join(values)
    return ""


def detect_verification(tool_name: Any, arguments: Any) -> list[str]:
    """Return categories only; never return the command text."""
    if str(tool_name or "") != "terminal":
        return []
    text = _argument_text(arguments)
    return sorted(category for category, patterns in PATTERNS.items() if any(pattern.search(text) for pattern in patterns))


def parse_exit_code(content: Any) -> int | None:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(content, dict):
        value = content.get("exit_code")
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
    return None


def summarize_verification_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = [event for event in events if event.get("verification_categories")]
    passed = [event for event in attempts if event.get("verification_status") == "passed"]
    failed = [event for event in attempts if event.get("verification_status") == "failed"]
    by_category: dict[str, dict[str, int]] = {}
    for event in attempts:
        for category in event.get("verification_categories", []):
            item = by_category.setdefault(category, {"attempted": 0, "passed": 0, "failed": 0, "unknown": 0})
            item["attempted"] += 1
            status = event.get("verification_status")
            item[status if status in ("passed", "failed") else "unknown"] += 1
    return {
        "attempted": len(attempts),
        "passed": len(passed),
        "failed": len(failed),
        "unknown": len(attempts) - len(passed) - len(failed),
        "by_category": by_category,
    }
