import unittest

from skill.pra.scripts.analyze_events import _compute_initial_prompt_structure
from skill.pra.scripts.sanitize_history import derive_metrics


class PromptBreakdownTests(unittest.TestCase):
    def test_derives_five_practical_aspects(self):
        text = (
            "Implement the login fix. Scope: only auth.py. "
            "Use the existing project context. Done when pytest passes. "
            "Do not change the API."
        )
        metrics, signals, _ = derive_metrics(text, 0)
        self.assertTrue(metrics["has_goal"])
        self.assertTrue(metrics["has_scope"])
        self.assertTrue(metrics["has_readiness"])
        self.assertTrue(metrics["has_file_context"])
        self.assertIn("constraint", signals)

    def test_breakdown_reports_counts_and_rates(self):
        events = [
            {"role": "user", "session": "s1", "timestamp": "2026-08-01T00:00:00Z", "signals": ["goal", "scope", "readiness", "file-context", "constraint"], "metrics": {"has_file_context": True}},
            {"role": "user", "session": "s1", "timestamp": "2026-08-01T00:01:00Z", "signals": []},
            {"role": "user", "session": "s2", "timestamp": "2026-08-01T00:00:00Z", "signals": ["goal", "constraint"]},
        ]
        result = _compute_initial_prompt_structure(events)
        self.assertEqual(result["sample_size"], 2)
        self.assertEqual(result["aspect_counts"]["goal"], 2)
        self.assertEqual(result["aspect_counts"]["readiness"], 1)
        self.assertEqual(result["aspect_rates"]["scope"], 50.0)

    def test_empty_breakdown_has_all_aspects(self):
        result = _compute_initial_prompt_structure([])
        self.assertEqual(set(result["aspect_counts"]), {"goal", "scope", "readiness", "context", "constraint"})


if __name__ == "__main__":
    unittest.main()
