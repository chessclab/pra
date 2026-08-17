import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from skill.pra.scripts.sanitize_history import scan_hermes_database


class HermesHistoryTests(unittest.TestCase):
    def test_scans_sqlite_metadata_without_persisting_message_content(self):
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
            connection.execute("INSERT INTO sessions VALUES (?, ?)", ("s1", "C:\\private\\project"))
            connection.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (1, "s1", "user", "add feature and run tests", None, None, None, stamp, "user"),
                    (2, "s1", "assistant", "PRIVATE_ASSISTANT_TEXT", "[{\"name\":\"tool\"}]", None, None, stamp + 1, None),
                    (3, "s1", "tool", "PRIVATE_TOOL_OUTPUT", None, None, "terminal", stamp + 2, None),
                ],
            )
            connection.commit()
            connection.close()

            output = StringIO()
            stats = scan_hermes_database(
                db, now - timedelta(minutes=1), now + timedelta(minutes=1), output, b"test-salt"
            )
            events = [json.loads(line) for line in output.getvalue().splitlines()]

            self.assertEqual(stats.files, 1)
            self.assertEqual(stats.events, 1)
            self.assertEqual(stats.assistant_events, 2)
            self.assertEqual(len(events), 3)
            serialized = output.getvalue()
            self.assertNotIn("PRIVATE_ASSISTANT_TEXT", serialized)
            self.assertNotIn("PRIVATE_TOOL_OUTPUT", serialized)
            self.assertNotIn("C:\\private\\project", serialized)
            self.assertEqual(events[0]["role"], "user")
            self.assertTrue(events[1]["has_tool_calls"])

    def test_provider_hermes_discovers_state_db(self):
        from skill.pra.scripts import sanitize_history

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "state.db"
            sqlite3.connect(db).close()
            old = sanitize_history.os.environ.get("HERMES_HOME")
            sanitize_history.os.environ["HERMES_HOME"] = str(root)
            try:
                sources = sanitize_history.discover_sources("hermes")
            finally:
                if old is None:
                    sanitize_history.os.environ.pop("HERMES_HOME", None)
                else:
                    sanitize_history.os.environ["HERMES_HOME"] = old
            self.assertEqual(sources, [db.resolve()])


if __name__ == "__main__":
    unittest.main()
