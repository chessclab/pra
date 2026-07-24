"""Unit tests for behaviour metrics in analyze_events.py."""

from __future__ import annotations

import json
import statistics
import tempfile
import unittest
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skill" / "codex-retrospective"
import sys

sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from analyze_events import (
    REPORT_SCHEMA_VERSION,
    _compute_assistant_events_per_user_turn,
    _compute_correction_chains,
    _compute_initial_prompt_structure,
    _compute_recovery_time,
    _compute_response_time,
    _compute_time_distribution,
    _compute_tool_ratio,
    build_report_state,
    load_events,
    render_markdown,
    find_compatible_previous,
    load_previous,
)


def _make_event(
    timestamp: str,
    session: str = "s1",
    role: str = "user",
    signals: list[str] | None = None,
    has_tool_calls: bool = False,
    tool_content_available: bool = True,
    kind: str = "user_message",
) -> dict:
    event: dict = {
        "schema_version": 1,
        "event_id": f"event-{timestamp}-{session}",
        "timestamp": timestamp,
        "session": session,
        "role": role,
        "kind": kind,
        "evidence": {"mode": "metadata"},
    }
    if role == "user":
        event["metrics"] = {"characters_bucket": "short", "text_parts": 1}
        event["signals"] = signals or []
    elif role == "assistant":
        event["has_tool_calls"] = has_tool_calls
        event["tool_content_available"] = tool_content_available
    return event


class AssistantEventsPerUserTurnTests(unittest.TestCase):
    """Tests for _compute_assistant_events_per_user_turn."""

    def test_single_user_to_assistant(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T10:01:00Z", role="assistant"),
        ]
        result = _compute_assistant_events_per_user_turn(events)
        self.assertEqual(1, result["count"])
        self.assertEqual(1.0, result["mean"])

    def test_user_assistant_user_assistant(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T10:01:00Z", role="assistant"),
            _make_event("2026-07-10T10:02:00Z", role="user"),
            _make_event("2026-07-10T10:03:00Z", role="assistant"),
        ]
        result = _compute_assistant_events_per_user_turn(events)
        # counts: [1 (from first user→assistant), 1 (from second user→assistant)]
        self.assertEqual(2, result["count"])

    def test_user_without_assistant_yields_zero(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
        ]
        result = _compute_assistant_events_per_user_turn(events)
        self.assertEqual(1, result["count"])
        self.assertEqual(0.0, result["mean"])
        self.assertEqual(0.0, result["median"])

    def test_multiple_assistant_between_users(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T10:01:00Z", role="assistant"),
            _make_event("2026-07-10T10:02:00Z", role="assistant"),
            _make_event("2026-07-10T10:03:00Z", role="assistant"),
            _make_event("2026-07-10T10:04:00Z", role="user"),
            _make_event("2026-07-10T10:05:00Z", role="assistant"),
        ]
        result = _compute_assistant_events_per_user_turn(events)
        self.assertEqual(2, result["count"])
        self.assertAlmostEqual(2.0, result["mean"], places=1)

    def test_sessions_do_not_mix(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user"),
            _make_event("2026-07-10T10:01:00Z", session="s1", role="assistant"),
            _make_event("2026-07-10T10:02:00Z", session="s2", role="user"),
            _make_event("2026-07-10T10:02:00Z", session="s1", role="assistant"),
            _make_event("2026-07-10T10:03:00Z", session="s2", role="assistant"),
            _make_event("2026-07-10T10:03:00Z", session="s1", role="assistant"),
        ]
        result = _compute_assistant_events_per_user_turn(events)
        self.assertGreater(result["count"], 0)
        # s1: user → assistant,assistant,assistant → count 3 for s1 user turn
        # s2: user → assistant → count 1 for s2 user turn
        # s1: no second user, so no further count
        # Total counts: [3, 1] = 2 entries
        self.assertEqual(2, result["count"])

    def test_distribution_keys_are_integers(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T10:01:00Z", role="assistant"),
            _make_event("2026-07-10T10:02:00Z", role="user"),
        ]
        result = _compute_assistant_events_per_user_turn(events)
        for k in result["distribution"]:
            self.assertIsInstance(k, int)

    def test_empty_events(self) -> None:
        result = _compute_assistant_events_per_user_turn([])
        self.assertEqual(0, result["count"])

    def test_trailing_assistant_after_last_user(self) -> None:
        """Assistant events after the last user must be counted."""
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T10:01:00Z", role="assistant"),
            _make_event("2026-07-10T10:02:00Z", role="assistant"),
        ]
        result = _compute_assistant_events_per_user_turn(events)
        self.assertEqual(1, result["count"])
        self.assertEqual(2.0, result["mean"])


class TimeDistributionTests(unittest.TestCase):
    """Tests for _compute_time_distribution."""

    def test_user_events_only(self) -> None:
        """Assistant events must not affect the distribution."""
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T11:00:00Z", role="assistant"),
            _make_event("2026-07-10T12:00:00Z", role="user"),
        ]
        result = _compute_time_distribution(events)
        self.assertEqual(2, sum(result["by_hour"].values()))  # 2 user events
        self.assertIn(10, result["by_hour"])
        self.assertIn(12, result["by_hour"])
        self.assertNotIn(11, result["by_hour"])

    def test_invalid_timestamp_skipped(self) -> None:
        events = [
            _make_event("invalid-date", role="user"),
            _make_event("2026-07-10T10:00:00Z", role="user"),
        ]
        result = _compute_time_distribution(events)
        self.assertIn(10, result["by_hour"])
        self.assertEqual(1, sum(result["by_hour"].values()))

    def test_late_night_and_weekend(self) -> None:
        events = [
            _make_event("2026-07-11T23:30:00Z", role="user"),  # Saturday (5) night
            _make_event("2026-07-12T10:00:00Z", role="user"),  # Sunday (6)
            _make_event("2026-07-13T10:00:00Z", role="user"),  # Monday (0)
        ]
        result = _compute_time_distribution(events)
        self.assertEqual(1, result["late_night_events"])
        self.assertEqual(2, result["weekend_events"])

    def test_peak_fields(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T10:30:00Z", role="user"),
            _make_event("2026-07-10T11:00:00Z", role="user"),
        ]
        result = _compute_time_distribution(events)
        self.assertEqual(10, result["peak_hour"])
        self.assertEqual(4, result["peak_day"])  # 2026-07-10 is Friday = 4

    def test_empty_events(self) -> None:
        result = _compute_time_distribution([])
        self.assertEqual({}, result["by_hour"])
        self.assertIsNone(result["peak_hour"])


class ToolRatioTests(unittest.TestCase):
    """Tests for _compute_tool_ratio."""

    def test_tool_calls_detected(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="assistant", has_tool_calls=True),
            _make_event("2026-07-10T10:01:00Z", role="assistant", has_tool_calls=False),
        ]
        result = _compute_tool_ratio(events)
        self.assertEqual(2, result["total_assistant_events"])
        self.assertEqual(1, result["with_tool_calls"])
        self.assertEqual(50.0, result["tool_ratio"])

    def test_unknown_format_tracked(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="assistant", tool_content_available=False),
            _make_event("2026-07-10T10:01:00Z", role="assistant", has_tool_calls=True),
        ]
        result = _compute_tool_ratio(events)
        self.assertEqual(1, result["unknown_format_count"])

    def test_no_assistant_events(self) -> None:
        result = _compute_tool_ratio([])
        self.assertEqual(0, result["total_assistant_events"])
        self.assertIsNone(result["tool_ratio"])

    def test_missing_content_is_unknown(self) -> None:
        """Assistant event without tool_content_available should be unknown."""
        events = [
            _make_event("2026-07-10T10:00:00Z", role="assistant", tool_content_available=False),
        ]
        result = _compute_tool_ratio(events)
        self.assertEqual(1, result["unknown_format_count"])
        self.assertEqual(0, result["recognized_format_count"])
        self.assertIsNone(result["tool_ratio"])

    def test_empty_content_list_is_recognized(self) -> None:
        """Explicit content=[] → recognized, no tool call."""
        events = [
            _make_event("2026-07-10T10:00:00Z", role="assistant", has_tool_calls=False, tool_content_available=True),
        ]
        result = _compute_tool_ratio(events)
        self.assertEqual(1, result["recognized_format_count"])
        self.assertEqual(0, result["unknown_format_count"])
        self.assertEqual(0.0, result["tool_ratio"])

    def test_all_unknown_yields_none_ratio(self) -> None:
        """All events unknown → tool_ratio is None."""
        events = [
            _make_event("2026-07-10T10:00:00Z", role="assistant", tool_content_available=False),
            _make_event("2026-07-10T10:01:00Z", role="assistant", tool_content_available=False),
        ]
        result = _compute_tool_ratio(events)
        self.assertEqual(2, result["unknown_format_count"])
        self.assertEqual(0, result["recognized_format_count"])
        self.assertIsNone(result["tool_ratio"])

    def test_ratio_from_recognized_only(self) -> None:
        """One tool + one recognized non-tool → 50%."""
        events = [
            _make_event("2026-07-10T10:00:00Z", role="assistant", has_tool_calls=True),
            _make_event("2026-07-10T10:01:00Z", role="assistant", has_tool_calls=False),
        ]
        result = _compute_tool_ratio(events)
        self.assertEqual(2, result["recognized_format_count"])
        self.assertEqual(1, result["with_tool_calls"])
        self.assertEqual(50.0, result["tool_ratio"])

    def test_coverage_rate_with_mixed_formats(self) -> None:
        """One recognized + one unknown → ratio from recognized, coverage=50%."""
        events = [
            _make_event("2026-07-10T10:00:00Z", role="assistant", has_tool_calls=True),
            _make_event("2026-07-10T10:01:00Z", role="assistant", tool_content_available=False),
        ]
        result = _compute_tool_ratio(events)
        self.assertEqual(2, result["total_assistant_events"])
        self.assertEqual(1, result["recognized_format_count"])
        self.assertEqual(1, result["unknown_format_count"])
        self.assertEqual(100.0, result["tool_ratio"])  # 1/1 recognized
        self.assertEqual(50.0, result["coverage_rate"])


class ResponseTimeTests(unittest.TestCase):
    """Tests for _compute_response_time."""

    def test_basic_gap(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T10:00:30Z", role="assistant"),
        ]
        result = _compute_response_time(events)
        self.assertEqual(1, result["count"])
        self.assertAlmostEqual(30.0, result["mean_seconds"], places=1)
        self.assertAlmostEqual(30.0, result["median_seconds"], places=1)

    def test_different_sessions_not_mixed(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user"),
            _make_event("2026-07-10T10:00:00Z", session="s2", role="user"),
            _make_event("2026-07-10T10:01:00Z", session="s2", role="assistant"),
            _make_event("2026-07-10T10:02:00Z", session="s1", role="assistant"),
        ]
        result = _compute_response_time(events)
        self.assertEqual(2, result["count"])

    def test_missing_assistant_response(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
        ]
        result = _compute_response_time(events)
        self.assertEqual(0, result["count"])

    def test_negative_interval_skipped(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="assistant"),
            _make_event("2026-07-10T10:05:00Z", role="user"),
        ]
        result = _compute_response_time(events)
        self.assertEqual(0, result["count"])

    def test_outlier_excluded(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T11:00:00Z", role="assistant"),  # 3600s > 600s
        ]
        result = _compute_response_time(events)
        self.assertEqual(0, result["count"])
        self.assertEqual(1, result["excluded_outliers"])

    def test_edge_zero_gap(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T10:00:00Z", role="assistant"),  # same timestamp
        ]
        result = _compute_response_time(events)
        self.assertEqual(0, result["count"])  # delta=0 not >0, skipped


class CorrectionChainsTests(unittest.TestCase):
    """Tests for _compute_correction_chains."""

    def test_successful_correction(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", role="assistant"),
            _make_event("2026-07-10T10:02:00Z", role="user", signals=["success"]),
        ]
        result = _compute_correction_chains(events)
        self.assertEqual(1, result["total_corrections"])
        self.assertEqual(1, result["successful_corrections"])
        self.assertEqual(0, result["stuck_corrections"])
        self.assertEqual(100.0, result["correction_success_rate"])

    def test_stuck_correction(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", role="user", signals=["goal"]),
        ]
        result = _compute_correction_chains(events)
        self.assertEqual(1, result["total_corrections"])
        self.assertEqual(0, result["successful_corrections"])
        self.assertEqual(1, result["stuck_corrections"])

    def test_multiple_sessions(self) -> None:
        events = [
            # s1: correction → success
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", session="s1", role="user", signals=["success"]),
            # s2: correction → nothing
            _make_event("2026-07-10T10:02:00Z", session="s2", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:03:00Z", session="s2", role="user", signals=["goal"]),
        ]
        result = _compute_correction_chains(events)
        self.assertEqual(2, result["total_corrections"])
        self.assertEqual(1, result["successful_corrections"])
        self.assertEqual(1, result["stuck_corrections"])
        self.assertEqual(50.0, result["correction_success_rate"])

    def test_multiple_corrections_in_a_row(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:02:00Z", role="user", signals=["success"]),
        ]
        result = _compute_correction_chains(events)
        # First correction: success found after it (event at 10:02)
        # Second correction: success found after it (same event at 10:02)
        # But success event also has "correction"? No, only success.
        # So both corrections find the success at 10:02.
        self.assertEqual(2, result["total_corrections"])
        self.assertEqual(2, result["successful_corrections"])

    def test_clean_session(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user", signals=["goal"]),
            _make_event("2026-07-10T10:01:00Z", role="user", signals=["success"]),
        ]
        result = _compute_correction_chains(events)
        self.assertEqual(1, result["clean_sessions"])
        self.assertEqual(0, result["correction_sessions"])

    def test_empty_events(self) -> None:
        result = _compute_correction_chains([])
        self.assertEqual(0, result["total_corrections"])


class RecoveryTimeTests(unittest.TestCase):
    """Tests for _compute_recovery_time."""

    def test_successful_recovery(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", role="user", signals=["goal"]),
            _make_event("2026-07-10T10:02:00Z", role="user", signals=["success"]),
        ]
        result = _compute_recovery_time(events)
        self.assertEqual(1, result["count"])
        self.assertEqual(2, result["mean_messages"])  # distance 2 (correction at 0, success at 2)

    def test_unrecovered_correction(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", role="user", signals=["goal"]),
        ]
        result = _compute_recovery_time(events)
        self.assertEqual(0, result["count"])
        self.assertEqual(1, result["unrecovered"])

    def test_multiple_corrections_different_outcomes(self) -> None:
        events = [
            # Correction with success
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", session="s1", role="user", signals=["goal"]),
            _make_event("2026-07-10T10:02:00Z", session="s1", role="user", signals=["success"]),
            # Correction without success
            _make_event("2026-07-10T11:00:00Z", session="s2", role="user", signals=["correction"]),
            _make_event("2026-07-10T11:01:00Z", session="s2", role="user", signals=["goal"]),
        ]
        result = _compute_recovery_time(events)
        self.assertEqual(1, result["count"])  # 1 successful
        self.assertEqual(1, result["unrecovered"])

    def test_fast_and_slow_recovery(self) -> None:
        events = [
            # Fast recovery (distance 1)
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", session="s1", role="user", signals=["success"]),
            # Slow recovery (distance >= 3)
            _make_event("2026-07-10T11:00:00Z", session="s2", role="user", signals=["correction"]),
            _make_event("2026-07-10T11:01:00Z", session="s2", role="user", signals=["goal"]),
            _make_event("2026-07-10T11:02:00Z", session="s2", role="user", signals=["goal"]),
            _make_event("2026-07-10T11:03:00Z", session="s2", role="user", signals=["success"]),
        ]
        result = _compute_recovery_time(events)
        self.assertEqual(2, result["count"])
        self.assertEqual(1, result["fast"])
        self.assertEqual(1, result["slow"])

    def test_success_event_with_correction_signal_not_counted(self) -> None:
        """An event with both 'correction' and 'success' should not mark recovery."""
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", role="user", signals=["correction", "success"]),
        ]
        result = _compute_recovery_time(events)
        self.assertEqual(0, result["count"])  # correction+success not pure success
        # Both events have correction → both unrecovered
        self.assertEqual(2, result["unrecovered"])


class InitialPromptStructureTests(unittest.TestCase):
    """Tests for _compute_initial_prompt_structure."""

    def test_first_event_per_session(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user", signals=["goal", "constraint", "verification"]),
            _make_event("2026-07-10T10:01:00Z", session="s1", role="user", signals=["goal"]),
            _make_event("2026-07-10T11:00:00Z", session="s2", role="user", signals=["goal"]),
        ]
        result = _compute_initial_prompt_structure(events)
        self.assertEqual(2, result["sample_size"])
        # s1 first: 3/3 aspects → score 10
        # s2 first: 1/3 aspects → score 10/3 ≈ 3.3
        # mean = (10 + 3.3) / 2 ≈ 6.7
        self.assertAlmostEqual(6.7, result["mean_score"], places=1)

    def test_followup_messages_ignored(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user", signals=["goal"]),
            _make_event("2026-07-10T10:01:00Z", session="s1", role="user", signals=[]),
            _make_event("2026-07-10T10:02:00Z", session="s1", role="user", signals=[]),
        ]
        result = _compute_initial_prompt_structure(events)
        self.assertEqual(1, result["sample_size"])  # only 1 session
        self.assertGreater(result["mean_score"], 0)

    def test_high_quality_count_and_ratio(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user", signals=["goal", "constraint", "verification"]),
            _make_event("2026-07-10T11:00:00Z", session="s2", role="user", signals=["goal"]),
            _make_event("2026-07-10T12:00:00Z", session="s3", role="user", signals=["goal", "verification"]),
        ]
        result = _compute_initial_prompt_structure(events)
        self.assertEqual(2, result["high_quality_count"])  # s1 and s3 have ≥2
        self.assertAlmostEqual(66.7, result["high_quality_ratio"], places=1)

    def test_empty_events(self) -> None:
        result = _compute_initial_prompt_structure([])
        self.assertEqual(0, result["sample_size"])
        self.assertEqual(0.0, result["mean_score"])

    def test_max_possible_constant(self) -> None:
        result = _compute_initial_prompt_structure([])
        self.assertEqual(10, result["max_possible"])


class BehaviouralRecommendationsTests(unittest.TestCase):
    """Tests for behavioural recommendations in build_report_state."""

    def test_correction_chains_recommendation_with_evidence(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", session="s1", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:02:00Z", session="s1", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:03:00Z", session="s1", role="user", signals=["goal"]),
            _make_event("2026-07-10T11:00:00Z", session="s2", role="user", signals=["goal", "success"]),
        ]
        state = build_report_state(events, datetime(2026, 7, 10, tzinfo=timezone.utc),
                                   datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        rec_titles = [r["title"] for r in state["recommendations"]]
        self.assertIn("Подтверждать результат после исправления", rec_titles)
        # Evidence should reference user events only
        rec = next(r for r in state["recommendations"] if r["title"] == "Подтверждать результат после исправления")
        for ev in rec.get("evidence", []):
            self.assertIn("event-", ev["event_id"])

    def test_recovery_time_recommendation(self) -> None:
        events = [
            # s1: correction → success (distance 4)
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", session="s1", role="user", signals=["goal"]),
            _make_event("2026-07-10T10:02:00Z", session="s1", role="user", signals=["goal"]),
            _make_event("2026-07-10T10:03:00Z", session="s1", role="user", signals=["goal"]),
            _make_event("2026-07-10T10:04:00Z", session="s1", role="user", signals=["success"]),
            # s2: correction → success (distance 2)
            _make_event("2026-07-10T11:00:00Z", session="s2", role="user", signals=["correction"]),
            _make_event("2026-07-10T11:01:00Z", session="s2", role="user", signals=["goal"]),
            _make_event("2026-07-10T11:02:00Z", session="s2", role="user", signals=["success"]),
            # s3: correction → success (distance 3)
            _make_event("2026-07-10T12:00:00Z", session="s3", role="user", signals=["correction"]),
            _make_event("2026-07-10T12:01:00Z", session="s3", role="user", signals=["goal"]),
            _make_event("2026-07-10T12:02:00Z", session="s3", role="user", signals=["goal"]),
            _make_event("2026-07-10T12:03:00Z", session="s3", role="user", signals=["success"]),
        ]
        state = build_report_state(events, datetime(2026, 7, 10, tzinfo=timezone.utc),
                                   datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        rec_titles = [r["title"] for r in state["recommendations"]]
        self.assertIn("Уточнять причину после исправления", rec_titles)

    def test_initial_prompt_structure_recommendation_threshold(self) -> None:
        """Recommendation only triggers when sample_size >= 10 and score < 5."""
        # 9 sessions with low quality → no recommendation
        events = []
        for i in range(9):
            events.append(
                _make_event(f"2026-07-10T{10+i:02d}:00:00Z", session=f"s{i}", role="user", signals=[])
            )
        state = build_report_state(events, datetime(2026, 7, 10, tzinfo=timezone.utc),
                                   datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        rec_titles = [r["title"] for r in state["recommendations"]]
        self.assertNotIn("Добавлять цель, ограничения и проверку в первый запрос новой задачи", rec_titles)

    def test_no_recovery_time_recommendation_for_small_sample(self) -> None:
        """Recovery time recommendation should only trigger with count >= 3."""
        events = [
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user", signals=["correction"]),
            _make_event("2026-07-10T10:01:00Z", session="s1", role="user", signals=["goal"]),
        ]
        state = build_report_state(events, datetime(2026, 7, 10, tzinfo=timezone.utc),
                                   datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        rec_titles = [r["title"] for r in state["recommendations"]]
        self.assertNotIn("Уточнять причину после исправления", rec_titles)

    def test_recommendation_evidence_excludes_assistant_events(self) -> None:
        """Assistant events must not appear in recommendation evidence."""
        assistant_event = _make_event("2026-07-10T09:00:00Z", session="s1", role="assistant")
        assistant_id = assistant_event["event_id"]
        user_events = [
            _make_event(f"2026-07-10T{10 + i:02d}:00:00Z", session="s1", role="user", signals=["goal"])
            for i in range(4)
        ]
        events = [assistant_event] + user_events
        state = build_report_state(
            events,
            datetime(2026, 7, 10, tzinfo=timezone.utc),
            datetime(2026, 7, 11, tzinfo=timezone.utc),
            {},
        )
        self.assertGreater(
            len(state["recommendations"]), 0,
            "Should have recommendations for 4 user events without verification/constraint",
        )
        for rec in state["recommendations"]:
            for ev in rec.get("evidence", []):
                self.assertNotEqual(assistant_id, ev["event_id"])


class MarkdownRenderingTests(unittest.TestCase):
    """Tests for render_markdown."""

    def setUp(self) -> None:
        self.minimal_events = [
            _make_event("2026-07-10T10:00:00Z", session="s1", role="user", signals=["goal"]),
            _make_event("2026-07-10T10:01:00Z", session="s1", role="assistant"),
        ]
        self.since = datetime(2026, 7, 10, tzinfo=timezone.utc)
        self.until = datetime(2026, 7, 11, tzinfo=timezone.utc)

    def test_contains_expected_sections(self) -> None:
        state = build_report_state(self.minimal_events, self.since, self.until, {})
        md = render_markdown(state)
        self.assertIn("## Сводка", md)
        self.assertIn("## Характер работы", md)
        self.assertIn("## Диагностика источников", md)
        self.assertIn("## Рекомендации", md)
        self.assertIn("## Ограничения", md)

    def test_contains_new_metric_labels(self) -> None:
        state = build_report_state(self.minimal_events, self.since, self.until, {})
        md = render_markdown(state)
        self.assertIn("События ассистента на пользовательский ход", md)
        self.assertIn("Приблизительное время до следующего события ассистента", md)
        self.assertIn("Индекс структуры первого промпта (0–10)", md)

    def test_no_raw_text_in_report(self) -> None:
        state = build_report_state(self.minimal_events, self.since, self.until, {})
        md = render_markdown(state)
        self.assertNotIn("raw text", md.lower())
        self.assertNotIn("secret", md.lower())

    def test_empty_state_renders_without_error(self) -> None:
        state = build_report_state([], self.since, self.until, {})
        md = render_markdown(state)
        self.assertIn("## Сводка", md)

    def test_with_fixtures(self) -> None:
        """Smoke test using synthetic fixtures."""
        import tempfile
        from pathlib import Path
        from sanitize_history import scan_sources

        fixture = Path(__file__).resolve().parent / "fixtures" / "synthetic_history.jsonl"
        if not fixture.is_file():
            self.skipTest("Fixture not found")
        with tempfile.TemporaryDirectory() as td:
            events_path = Path(td) / "events.jsonl"
            with events_path.open("w", encoding="utf-8") as out:
                scan_sources([fixture], self.since, self.until, out, b"test")
            events = load_events(events_path)
            state = build_report_state(events, self.since, self.until, {})
            md = render_markdown(state)
            self.assertIn("## Сводка", md)
            # Verify schema version
            self.assertEqual(4, state["schema_version"])


class SchemaVersionTests(unittest.TestCase):
    """Tests for schema version 4 bump."""

    def test_report_schema_version_is_four(self) -> None:
        self.assertEqual(4, REPORT_SCHEMA_VERSION)

    def test_previous_v4_is_compatible(self) -> None:
        """A v4 report should be auto-comparable with v4."""
        scope = {"provider": "all", "source_mode": "auto", "period_duration_seconds": 604800}
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            data = {
                "schema_version": 4,
                "scope": scope,
                "metrics": {"prompts": 10, "sessions": 3, "corrections": 2, "verification_mentions": 1, "repeated_correction_sessions": 0},
            }
            (out / "retrospective-20260701T000000Z.json").write_text(json.dumps(data), encoding="utf-8")
            result = find_compatible_previous(out, scope)
            self.assertIsNotNone(result)
            self.assertEqual(10, result["metrics"]["prompts"])

    def test_v3_loaded_but_not_comparable_with_v4(self) -> None:
        """V3 reports are readable (load_previous) but not auto-compared (find_compatible_previous)."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "v3.json"
            data = {"schema_version": 3, "scope": {}, "metrics": {}}
            path.write_text(json.dumps(data), encoding="utf-8")
            # load_previous accepts v3
            loaded = load_previous(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(3, loaded["schema_version"])
            # find_compatible_previous rejects v3 (v3 != v4)
            scope = {"provider": "all", "source_mode": "auto", "period_duration_seconds": 604800}
            v3_data = {
                "schema_version": 3, "scope": scope,
                "metrics": {"prompts": 10, "sessions": 3, "corrections": 2, "verification_mentions": 1, "repeated_correction_sessions": 0},
            }
            (Path(td) / "retrospective-20260701T000000Z.json").write_text(json.dumps(v3_data), encoding="utf-8")
            result = find_compatible_previous(Path(td), scope)
            self.assertIsNone(result)

    def test_load_previous_accepts_v1_v2_v3_v4(self) -> None:
        for ver in (1, 2, 3, 4):
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / f"r{ver}.json"
                path.write_text(json.dumps({"schema_version": ver, "metrics": {}}), encoding="utf-8")
                loaded = load_previous(path)
                self.assertIsNotNone(loaded, f"v{ver} should be loadable")

    def test_load_previous_returns_none_for_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "r99.json"
            path.write_text(json.dumps({"schema_version": 99, "metrics": {}}), encoding="utf-8")
            loaded = load_previous(path)
            self.assertIsNone(loaded)


class EdgeCaseTests(unittest.TestCase):
    """Tests for edge cases and boundary conditions."""

    def test_empty_events_list(self) -> None:
        state = build_report_state([], datetime(2026, 7, 10, tzinfo=timezone.utc),
                                   datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        self.assertEqual(0, state["metrics"]["prompts"])
        self.assertEqual(0, state["metrics"]["sessions"])

    def test_single_user_event(self) -> None:
        events = [_make_event("2026-07-10T10:00:00Z", role="user", signals=["goal"])]
        state = build_report_state(events, datetime(2026, 7, 10, tzinfo=timezone.utc),
                                   datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        self.assertEqual(1, state["metrics"]["prompts"])
        self.assertEqual(1, state["metrics"]["sessions"])
        self.assertEqual(1, state["behaviour"]["assistant_events_per_user_turn"]["count"])

    def test_only_assistant_events(self) -> None:
        events = [
            _make_event("2026-07-10T10:00:00Z", role="assistant", has_tool_calls=True),
            _make_event("2026-07-10T10:01:00Z", role="assistant", has_tool_calls=False),
        ]
        state = build_report_state(events, datetime(2026, 7, 10, tzinfo=timezone.utc),
                                   datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        self.assertEqual(0, state["metrics"]["prompts"])

    def test_invalid_timestamps_do_not_crash(self) -> None:
        events = [
            _make_event("not-a-timestamp", role="user", signals=["goal"]),
            _make_event("also-invalid", role="assistant"),
        ]
        # Should not raise
        state = build_report_state(events, datetime(2026, 7, 10, tzinfo=timezone.utc),
                                   datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        self.assertEqual(1, state["metrics"]["prompts"])

    def test_no_response_time_recommendation(self) -> None:
        """The old response-time simplification recommendation must not appear."""
        events = [
            _make_event("2026-07-10T10:00:00Z", role="user"),
            _make_event("2026-07-10T10:05:00Z", role="assistant"),
        ]
        state = build_report_state(events, datetime(2026, 7, 10, tzinfo=timezone.utc),
                                   datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        rec_titles = [r["title"] for r in state["recommendations"]]
        self.assertNotIn("Упрощать запросы для ускорения ответа", rec_titles)

    def test_no_abandoned_sessions_in_behaviour(self) -> None:
        """The abandoned_sessions key must not exist in behaviour."""
        state = build_report_state([], datetime(2026, 7, 10, tzinfo=timezone.utc),
                                   datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        self.assertNotIn("abandoned_sessions", state["behaviour"])


class CorrectionSuccessRegexIntegrationTests(unittest.TestCase):
    """Integration tests for correction/success regex via derive_metrics."""

    def setUp(self) -> None:
        from sanitize_history import derive_metrics
        self.derive = derive_metrics

    def test_ne_rabotaet_not_success(self) -> None:
        _, signals, _ = self.derive("не работает", 0)
        self.assertNotIn("success", signals)

    def test_teper_rabotaet_is_success(self) -> None:
        _, signals, _ = self.derive("Теперь работает", 0)
        self.assertIn("success", signals)

    def test_ne_idealno_not_success(self) -> None:
        _, signals, _ = self.derive("не идеально", 0)
        self.assertNotIn("success", signals)

    def test_no_network_access_not_correction(self) -> None:
        _, signals, _ = self.derive("No network access", 0)
        self.assertNotIn("correction", signals)

    def test_no_external_deps_not_correction(self) -> None:
        _, signals, _ = self.derive("No external dependencies", 0)
        self.assertNotIn("correction", signals)

    def test_net_eto_neverno_is_correction(self) -> None:
        _, signals, _ = self.derive("Нет, это неверно", 0)
        self.assertIn("correction", signals)

    def test_no_this_is_wrong_is_correction(self) -> None:
        _, signals, _ = self.derive("No, this is wrong", 0)
        self.assertIn("correction", signals)

    def test_works_now_thanks_is_success(self) -> None:
        _, signals, _ = self.derive("Works now, thanks", 0)
        self.assertIn("success", signals)

    def test_works_is_success(self) -> None:
        _, signals, _ = self.derive("Works fine", 0)
        self.assertIn("success", signals)

    def test_short_prompt_signal(self) -> None:
        """Very short texts should get short-prompt signal."""
        _, signals, _ = self.derive("ok", 0)
        self.assertIn("short-prompt", signals)

    def test_no_standalone_word_not_correction(self) -> None:
        """Standalone 'no' without punctuation should not be a correction."""
        # 'no' at end of sentence without punctuation context
        _, signals, _ = self.derive("There is no way to do this", 0)
        self.assertNotIn("correction", signals)


if __name__ == "__main__":
    unittest.main()
