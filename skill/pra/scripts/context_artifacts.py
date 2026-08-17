"""Detect context-curator artifacts using transient text and emit booleans only."""

from __future__ import annotations

import json
import re
from typing import Any

ARTIFACTS = {
    "context": re.compile(r"\bCONTEXT\.md\b", re.I),
    "handoff": re.compile(r"\bSESSION-HANDOFF\.md\b", re.I),
    "decisions": re.compile(r"\bDECISIONS\.md\b", re.I),
}
DECISION_RE = re.compile(r"(?i)(?:decision recorded|recorded decision|решени[ея].*(?:зафикс|запис)|зафиксир.*решени)")
WRITE_TOOLS = {"write_file", "patch", "terminal", "execute_code", "computer_use"}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _searchable_arguments(arguments: Any) -> str:
    data = _as_dict(arguments)
    values: list[str] = []
    for key in ("path", "file_path", "old_string", "new_string", "command", "cmd", "text", "prompt"):
        if isinstance(data.get(key), str):
            values.append(data[key])
    return "\n".join(values)


def detect_context_action(tool_name: Any, arguments: Any) -> dict[str, Any]:
    """Return safe flags; never return matching text or paths."""
    name = str(tool_name or "")
    if name not in WRITE_TOOLS:
        return {}
    text = _searchable_arguments(arguments)
    matches = {key: bool(pattern.search(text)) for key, pattern in ARTIFACTS.items()}
    if not any(matches.values()):
        return {}
    return {
        "context_artifact_created": matches["context"],
        "handoff_created": matches["handoff"],
        "decision_recorded": matches["decisions"] or bool(DECISION_RE.search(text)),
    }


def detect_decision_signal(text: str) -> bool:
    return bool(DECISION_RE.search(text or ""))


def summarize_context_artifacts(events: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("context_artifact_created", "handoff_created", "decision_recorded", "resume_after_handoff")
    result = {key: sum(1 for event in events if event.get(key)) for key in keys}
    result["sessions_with_context_artifact"] = len({event.get("session") for event in events if event.get("context_artifact_created")})
    result["sessions_with_handoff"] = len({event.get("session") for event in events if event.get("handoff_created")})
    result["sessions_resumed_after_handoff"] = len({event.get("session") for event in events if event.get("resume_after_handoff")})
    return result
