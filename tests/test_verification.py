import unittest

from skill.pra.scripts.verification import detect_verification, parse_exit_code, summarize_verification_events


class VerificationTests(unittest.TestCase):
    def test_detects_categories_without_returning_command(self):
        categories = detect_verification("terminal", '{"command":"pytest -q && git diff"}')
        self.assertEqual(categories, ["diff", "test"])

    def test_ignores_non_terminal_tools(self):
        self.assertEqual(detect_verification("read_file", '{"path":"tests/test_api.py"}'), [])

    def test_parses_tool_result_exit_code(self):
        self.assertEqual(parse_exit_code('{"output":"ok","exit_code":0}'), 0)
        self.assertEqual(parse_exit_code('{"output":"failed","exit_code":2}'), 2)
        self.assertIsNone(parse_exit_code('{"output":"unknown"}'))

    def test_summarizes_attempts_by_category(self):
        result = summarize_verification_events([
            {"verification_categories": ["test"], "verification_status": "passed"},
            {"verification_categories": ["test", "diff"], "verification_status": "failed"},
            {"verification_categories": ["build"], "verification_status": None},
        ])
        self.assertEqual((result["attempted"], result["passed"], result["failed"], result["unknown"]), (3, 1, 1, 1))
        self.assertEqual(result["by_category"]["test"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
