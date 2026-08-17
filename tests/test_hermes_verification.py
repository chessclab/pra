import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from skill.pra.scripts.sanitize_history import scan_hermes_database


class HermesVerificationIntegrationTests(unittest.TestCase):
    def test_terminal_check_is_linked_to_tool_result_status(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            connection = sqlite3.connect(db)
            connection.executescript(
                """
                CREATE TABLE sessions (id TEXT, cwd TEXT);
                CREATE TABLE messages (
                    id INTEGER, session_id TEXT, role TEXT, content TEXT,
                    tool_calls TEXT, tool_call_id TEXT, tool_name TEXT,
                    timestamp REAL, display_kind TEXT
                );
                """
            )
            now = datetime.now(timezone.utc).replace(microsecond=0)
            stamp = now.timestamp()
            calls = json.dumps([{
                "call_id": "call-1",
                "function": {"name": "terminal", "arguments": '{"command":"pytest -q"}'},
            }])
            connection.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (1, "s1", "user", "run checks", None, None, None, stamp, "user"),
                    (2, "s1", "assistant", "", calls, None, None, stamp + 1, None),
                    (3, "s1", "tool", '{"exit_code":0,"output":"private"}', None, "call-1", "terminal", stamp + 2, None),
                ],
            )
            connection.commit()
            connection.close()

            output = StringIO()
            scan_hermes_database(db, now - timedelta(minutes=1), now + timedelta(minutes=1), output, b"salt")
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            verification = [event for event in events if event.get("verification_categories")]

            self.assertEqual(len(verification), 1)
            self.assertEqual(verification[0]["verification_categories"], ["test"])
            self.assertEqual(verification[0]["verification_status"], "passed")
            self.assertNotIn("pytest", output.getvalue())
            self.assertNotIn("private", output.getvalue())


if __name__ == "__main__":
    unittest.main()
