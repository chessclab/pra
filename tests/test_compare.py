import unittest

from skill.pra.scripts.compare import build_comparison, render_markdown


def report(sessions, prompts, corrections, attempted, passed, logical, incomplete, resumed):
    return {
        "privacy": {"raw_content_included": False},
        "scope": {"provider": "hermes"},
        "period": {"since": "2026-08-01T00:00:00+00:00", "until": "2026-08-08T00:00:00+00:00"},
        "metrics": {"sessions": sessions, "prompts": prompts, "corrections": corrections},
        "behaviour": {
            "verification_actions": {"attempted": attempted, "passed": passed},
            "logical_turns": {"count": logical, "incomplete_count": incomplete},
            "context_artifacts": {"sessions_resumed_after_handoff": resumed},
        },
    }


class ComparisonTests(unittest.TestCase):
    def test_builds_absolute_and_rate_deltas(self):
        result = build_comparison(
            report(10, 20, 6, 4, 2, 20, 4, 1),
            report(12, 30, 3, 15, 12, 30, 2, 3),
            "Require a test command",
        )
        self.assertEqual(result["absolute_delta"]["prompts"], 10.0)
        self.assertEqual(result["rates"]["verification_attempt_rate"]["baseline_percent"], 20.0)
        self.assertEqual(result["rates"]["verification_attempt_rate"]["follow_up_percent"], 50.0)
        self.assertEqual(result["rates"]["verification_attempt_rate"]["delta_percentage_points"], 30.0)
        self.assertIn("does not prove", result["interpretation"])

    def test_markdown_contains_experiment_sections(self):
        result = build_comparison(report(1, 2, 0, 1, 1, 2, 0, 0), report(1, 2, 0, 2, 2, 2, 0, 0))
        markdown = render_markdown(result)
        self.assertIn("Baseline → Intervention → Follow-up", markdown)
        self.assertIn("Проверки на запрос", markdown)
        self.assertIn("Сессии", markdown)

    def test_zero_denominator_is_unknown(self):
        result = build_comparison(report(1, 1, 0, 0, 0, 0, 0, 0), report(1, 1, 0, 0, 0, 0, 0, 0))
        self.assertIsNone(result["rates"]["verification_pass_rate"]["baseline_percent"])


if __name__ == "__main__":
    unittest.main()
