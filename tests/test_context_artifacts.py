import unittest

from skill.pra.scripts.context_artifacts import (
    detect_context_action,
    detect_decision_signal,
    summarize_context_artifacts,
)


class ContextArtifactTests(unittest.TestCase):
    def test_detects_metadata_only_artifact_action(self):
        result = detect_context_action("write_file", '{"path":"C:/private/CONTEXT.md","content":"secret"}')
        self.assertEqual(result["context_artifact_created"], True)
        self.assertNotIn("secret", str(result))

    def test_detects_handoff_and_decisions(self):
        result = detect_context_action("patch", '{"path":"SESSION-HANDOFF.md","new_string":"decision"}')
        self.assertEqual(result["handoff_created"], True)
        self.assertEqual(result["decision_recorded"], False)
        self.assertTrue(detect_decision_signal("Решение зафиксировано в проектном контексте"))

    def test_summarizes_resume_signals(self):
        result = summarize_context_artifacts([
            {"session": "s1", "context_artifact_created": True},
            {"session": "s1", "handoff_created": True},
            {"session": "s1", "resume_after_handoff": True, "decision_recorded": True},
        ])
        self.assertEqual(result["context_artifact_created"], 1)
        self.assertEqual(result["handoff_created"], 1)
        self.assertEqual(result["decision_recorded"], 1)
        self.assertEqual(result["sessions_resumed_after_handoff"], 1)


if __name__ == "__main__":
    unittest.main()
