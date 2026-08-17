"""Build logical user-to-final-assistant turns from sanitized event metadata."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any


def _timestamp(event: dict[str, Any]) -> datetime | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_logical_turns(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group a user event with following assistant events in its session."""
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_session[str(event.get("session", "unknown"))].append(event)

    turns: list[dict[str, Any]] = []
    for session, session_events in by_session.items():
        ordered = sorted(session_events, key=lambda item: str(item.get("timestamp", "")))
        current: dict[str, Any] | None = None
        for event in ordered:
            role = str(event.get("role", "user"))
            if role == "user":
                if current is not None:
                    turns.append(current)
                current = {
                    "session": session,
                    "user_event_id": str(event.get("event_id", "event-unknown")),
                    "started_at": event.get("timestamp"),
                    "assistant_events": [],
                    "user_signals": list(event.get("signals", [])),
                }
            elif current is not None and role == "assistant":
                current["assistant_events"].append(event)
        if current is not None:
            turns.append(current)

    for turn in turns:
        assistants = turn["assistant_events"]
        timestamps = [_timestamp(item) for item in assistants]
        timestamps = [item for item in timestamps if item is not None]
        started = _timestamp({"timestamp": turn["started_at"]})
        ended = max(timestamps) if timestamps else None
        turn["assistant_event_count"] = len(assistants)
        turn["tool_call_count"] = sum(bool(item.get("has_tool_calls")) for item in assistants)
        turn["has_verification_mention"] = "verification" in turn["user_signals"]
        turn["completed"] = bool(assistants)
        turn["duration_seconds"] = (
            round((ended - started).total_seconds(), 3)
            if started is not None and ended is not None and ended >= started
            else None
        )
    return sorted(turns, key=lambda item: (str(item.get("started_at", "")), str(item["user_event_id"])))


def summarize_logical_turns(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Return aggregate logical-turn metrics without storing turn content."""
    durations = [item["duration_seconds"] for item in turns if item.get("duration_seconds") is not None]
    assistant_counts = [int(item["assistant_event_count"]) for item in turns]
    tool_counts = [int(item["tool_call_count"]) for item in turns]
    completed = sum(bool(item.get("completed")) for item in turns)
    verification = sum(bool(item.get("has_verification_mention")) for item in turns)
    return {
        "count": len(turns),
        "completed_count": completed,
        "incomplete_count": len(turns) - completed,
        "verification_mention_count": verification,
        "verification_mention_rate": round(verification / len(turns) * 100, 1) if turns else 0.0,
        "assistant_events_total": sum(assistant_counts),
        "assistant_events_mean": round(statistics.mean(assistant_counts), 1) if turns else 0.0,
        "tool_calls_total": sum(tool_counts),
        "tool_calls_mean": round(statistics.mean(tool_counts), 1) if turns else 0.0,
        "duration_seconds_mean": round(statistics.mean(durations), 1) if durations else None,
        "duration_seconds_median": round(statistics.median(durations), 1) if durations else None,
        "replanning_count": None,
        "user_intervention_count": None,
        "evidence": {
            "replanning": "not_observed_in_metadata",
            "user_intervention": "not_observed_in_metadata",
        },
    }
