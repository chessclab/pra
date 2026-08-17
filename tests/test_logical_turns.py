import unittest

from skill.pra.scripts.logical_turns import build_logical_turns, summarize_logical_turns


def event(event_id, timestamp, role, session="s1", **extra):
    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "role": role,
        "session": session,
        **extra,
    }


class LogicalTurnsTests(unittest.TestCase):
    def test_groups_user_and_following_assistant_events(self):
        events = [
            event("u1", "2026-01-01T00:00:00Z", "user", signals=["goal", "verification"]),
            event("a1", "2026-01-01T00:00:02Z", "assistant", has_tool_calls=True),
            event("a2", "2026-01-01T00:00:05Z", "assistant", has_tool_calls=False),
            event("u2", "2026-01-01T00:01:00Z", "user", signals=[]),
        ]
        turns = build_logical_turns(events)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["assistant_event_count"], 2)
        self.assertEqual(turns[0]["tool_call_count"], 1)
        self.assertTrue(turns[0]["has_verification_mention"])
        self.assertEqual(turns[0]["duration_seconds"], 5.0)
        self.assertFalse(turns[1]["completed"])

    def test_sessions_are_not_mixed(self):
        events = [
            event("u2", "2026-01-01T00:00:01Z", "user", session="s2", signals=[]),
            event("u1", "2026-01-01T00:00:00Z", "user", session="s1", signals=[]),
            event("a1", "2026-01-01T00:00:02Z", "assistant", session="s1", has_tool_calls=True),
        ]
        turns = build_logical_turns(events)
        self.assertEqual([(t["session"], t["assistant_event_count"]) for t in turns], [("s1", 1), ("s2", 0)])

    def test_summary_marks_unobservable_metrics(self):
        turns = build_logical_turns([
            event("u1", "2026-01-01T00:00:00Z", "user", signals=[]),
            event("a1", "2026-01-01T00:00:01Z", "assistant", has_tool_calls=True),
        ])
        summary = summarize_logical_turns(turns)
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["tool_calls_total"], 1)
        self.assertIsNone(summary["replanning_count"])
        self.assertEqual(summary["evidence"]["replanning"], "not_observed_in_metadata")


if __name__ == "__main__":
    unittest.main()
