import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from skill.pra.scripts.sanitize_history import scan_hermes_database


class ContextArtifactIntegrationTests(unittest.TestCase):
    def test_handoff_and_resume_are_emitted_without_content(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            connection = sqlite3.connect(db)
            connection.executescript(
                """
                CREATE TABLE sessions (id TEXT, cwd TEXT);
                CREATE TABLE messages (
                    id INTEGER, session_id TEXT, role TEXT, content TEXT,
                    tool_call_id TEXT, tool_calls TEXT, tool_name TEXT,
                    effect_disposition TEXT, timestamp REAL, token_count INTEGER,
                    finish_reason TEXT, reasoning TEXT, reasoning_content TEXT,
                    reasoning_details TEXT, codex_reasoning_items TEXT,
                    codex_message_items TEXT, platform_message_id TEXT,
                    observed INTEGER, active INTEGER, compacted INTEGER,
                    api_content TEXT, display_kind TEXT, display_metadata TEXT
                );
                """
            )
            now = datetime.now(timezone.utc).replace(microsecond=0)
            stamp = now.timestamp()
            calls = json.dumps([{
                "call_id": "call-ctx",
                "function": {"name": "write_file", "arguments": '{"path":"SESSION-HANDOFF.md","content":"private"}'},
            }])
            empty = (None,) * 12
            rows = [
                (1, "s1", "user", "save a handoff", None, None, None, None, stamp, None, None, None, None, None, None, None, None, 0, 1, 0, None, "user", None),
                (2, "s1", "assistant", "", None, calls, None, None, stamp + 1, None, None, None, None, None, None, None, None, 0, 1, 0, None, None, None),
                (3, "s1", "tool", '{"success":true,"content":"private"}', "call-ctx", None, "write_file", None, stamp + 2, None, None, None, None, None, None, None, None, 0, 1, 0, None, None, None),
                (4, "s1", "user", "continue after handoff", None, None, None, None, stamp + 3, None, None, None, None, None, None, None, None, 0, 1, 0, None, "user", None),
            ]
            connection.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            connection.commit()
            connection.close()
            output = StringIO()
            scan_hermes_database(db, now - timedelta(minutes=1), now + timedelta(minutes=1), output, b"salt")
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(sum(event.get("handoff_created", False) for event in events), 1)
            self.assertEqual(sum(event.get("resume_after_handoff", False) for event in events), 1)
            self.assertNotIn("SESSION-HANDOFF.md", output.getvalue())
            self.assertNotIn("private", output.getvalue())


if __name__ == "__main__":
    unittest.main()
