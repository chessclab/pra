from __future__ import annotations

import json
import ast
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = REPO_ROOT / "skill" / "pra"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from analyze_events import build_report_state, find_compatible_previous, load_events, render_markdown  # noqa: E402
from sanitize_history import (  # noqa: E402
    _provider_root,
    classify,
    count_source_files,
    discover_sources,
    is_high_risk_content,
    scan_sources,
    user_text_parts,
)


class SanitizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TESTS_ROOT / "fixtures" / "synthetic_history.jsonl"
        self.since = datetime(2026, 7, 1, tzinfo=timezone.utc)
        self.until = datetime(2026, 8, 1, tzinfo=timezone.utc)

    def test_private_values_never_reach_events_or_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events_path = Path(temporary) / "events.jsonl"
            with events_path.open("w", encoding="utf-8") as output:
                stats = scan_sources([self.fixture], self.since, self.until, output, b"test-salt")
            events_text = events_path.read_text(encoding="utf-8")
            events = load_events(events_path)
            state = build_report_state(events, self.since, self.until, stats.public_dict())
            report = render_markdown(state)
            combined = events_text + report + json.dumps(state, ensure_ascii=False)

        forbidden = (
            "CANARY_PASTED_PRIVATE_SOURCE",
            "CANARY_ASSISTANT_PRIVATE_OUTPUT",
            "CANARY_CODE_BLOCK",
            "CANARY_OUTSIDE_PERIOD",
            "CANARY_TOOL_NAME",
            "CANARY_TOOL_INPUT",
            "closed-project",
            "alice@example.com",
            "sk-proj-abcdefghijklmnopqrstuvwxyz",
            "C:\\Users\\alice",
            "real-session-id",
        )
        for value in forbidden:
            self.assertNotIn(value, combined)
        self.assertEqual(7, len(events))  # 5 user + 2 assistant
        self.assertEqual(2, state["metrics"]["sessions"])
        self.assertEqual(2, state["metrics"]["corrections"])
        self.assertIn("user_message", state["event_kinds"])
        self.assertEqual(1, stats.duplicate_events)
        self.assertEqual(1, stats.internal_user_events)
        first_event = next(event for event in events if event["timestamp"] == "2026-07-10T10:01:00.001000Z")
        self.assertEqual("user_message", first_event["kind"])
        context_event = next(
            event
            for event in events
            if event["timestamp"] == "2026-07-10T10:06:00Z" and event["kind"] == "user_message"
        )
        self.assertEqual(["short-prompt", "privacy-redaction"], context_event["signals"])
        self.assertGreaterEqual(stats.dropped_fields, 1)
        self.assertEqual(1, stats.outside_period)

    def test_output_schema_contains_no_raw_text_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events_path = Path(temporary) / "events.jsonl"
            with events_path.open("w", encoding="utf-8") as output:
                scan_sources([self.fixture], self.since, self.until, output, b"test-salt")
            events = load_events(events_path)
        self.assertTrue(events)
        serialized_keys = {key for event in events for key in event.keys()}
        self.assertNotIn("text", serialized_keys)
        self.assertNotIn("message", serialized_keys)
        self.assertNotIn("content", serialized_keys)
        self.assertTrue(all(event["evidence"]["mode"] == "metadata" for event in events))

    def test_report_comparison_uses_only_aggregate_metrics(self) -> None:
        previous = {
            "schema_version": 1,
            "metrics": {"prompts": 1, "sessions": 1, "corrections": 0, "verification_mentions": 0, "repeated_correction_sessions": 0},
        }
        current = build_report_state([], self.since, self.until, {}, previous)
        self.assertEqual(-1, current["comparison"]["prompts"])
        self.assertFalse(current["privacy"]["raw_content_included"])

    def test_runtime_scripts_import_no_network_clients(self) -> None:
        forbidden_roots = {"socket", "urllib", "http", "requests", "httpx", "aiohttp"}
        found: set[str] = set()
        for script in (SKILL_ROOT / "scripts").glob("*.py"):
            tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module.split(".", 1)[0])
        self.assertTrue(forbidden_roots.isdisjoint(found), found & forbidden_roots)

    def test_context_heavy_content_is_quarantined(self) -> None:
        self.assertTrue(is_high_risk_content("x" * 800, 10))
        self.assertFalse(is_high_risk_content("x" * 799, 10))
        self.assertFalse(is_high_risk_content("x" * 800, 9))


class ClaudeCodeTests(unittest.TestCase):
    """Tests for Claude Code and Codex Desktop display format support."""

    def setUp(self) -> None:
        self.fixture = TESTS_ROOT / "fixtures" / "claude_code_history.jsonl"
        self.since = datetime(2026, 7, 15, tzinfo=timezone.utc)
        self.until = datetime(2026, 8, 1, tzinfo=timezone.utc)

    def test_classify_recognizes_display_format(self) -> None:
        record = {"display": "hello", "timestamp": 1773264000000, "sessionId": "s1"}
        role, kind = classify(record)
        self.assertEqual("user", role)
        self.assertEqual("display_message", kind)

    def test_classify_recognizes_structured_format(self) -> None:
        record = {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "timestamp": "2026-07-16T10:00:00Z",
            "sessionId": "s1",
            "userType": "external",
        }
        role, kind = classify(record)
        self.assertEqual("user", role)
        self.assertEqual("user_message", kind)

    def test_classify_skips_assistant_in_structured_format(self) -> None:
        record = {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "..."}]},
            "timestamp": "2026-07-16T10:00:05Z",
            "sessionId": "s1",
            "userType": "external",
        }
        role, kind = classify(record)
        # role is "assistant", not None – scan_sources skips any role != "user"
        self.assertEqual("assistant", role)
        self.assertIsNotNone(kind)

    def test_classify_skips_progress_records(self) -> None:
        record = {
            "type": "progress",
            "data": {"type": "hook_progress"},
            "timestamp": "2026-07-16T09:59:00Z",
            "sessionId": "s1",
            "userType": "external",
        }
        role, kind = classify(record)
        self.assertIsNone(role)

    def test_user_text_parts_display_format(self) -> None:
        record = {"display": "hello world", "timestamp": 1773264000000}
        parts = user_text_parts(record)
        self.assertEqual(["hello world"], parts)

    def test_user_text_parts_structured_string_content(self) -> None:
        record = {
            "type": "user",
            "message": {"role": "user", "content": "add tests"},
        }
        parts = user_text_parts(record)
        self.assertEqual(["add tests"], parts)

    def test_user_text_parts_structured_array_text_only(self) -> None:
        record = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "check output"},
                    {"type": "tool_result", "content": "test passed"},
                ],
            },
        }
        parts = user_text_parts(record)
        self.assertEqual(["check output"], parts)

    def test_user_text_parts_structured_array_tool_result_only(self) -> None:
        record = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "content": "only automated result"},
                ],
            },
        }
        parts = user_text_parts(record)
        self.assertEqual([], parts)

    def test_scan_sources_parses_both_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events_path = Path(temporary) / "events.jsonl"
            with events_path.open("w", encoding="utf-8") as output:
                stats = scan_sources([self.fixture], self.since, self.until, output, b"test-salt")
            events = load_events(events_path)

        # 4 user events + 2 assistant events = 6
        self.assertEqual(6, len(events))
        self.assertEqual(4, stats.events)
        self.assertEqual(2, stats.assistant_events)
        # 2 sessions: display-session-001 + structured-session-001
        sessions = {e.get("session") for e in events}
        self.assertEqual(2, len(sessions))

        # skipped: progress = 1 (assistant no longer skipped)
        self.assertEqual(1, stats.skipped_non_user)
        # internal userType skip: 1
        self.assertEqual(1, stats.internal_user_events)
        # tool_result-only content → no user text, silently skipped (no counter)
        self.assertEqual(0, stats.empty_after_sanitization)

    def test_no_raw_text_in_events_or_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events_path = Path(temporary) / "events.jsonl"
            with events_path.open("w", encoding="utf-8") as output:
                scan_sources([self.fixture], self.since, self.until, output, b"test-salt")
            events = load_events(events_path)

        forbidden_keys = {"text", "message", "content"}
        for event in events:
            self.assertTrue(forbidden_keys.isdisjoint(event.keys()))
            self.assertEqual("metadata", event["evidence"]["mode"])

    def test_canary_values_do_not_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events_path = Path(temporary) / "events.jsonl"
            with events_path.open("w", encoding="utf-8") as output:
                stats = scan_sources([self.fixture], self.since, self.until, output, b"test-salt")
            events = load_events(events_path)
            state = build_report_state(events, self.since, self.until, stats.public_dict())
            combined = events_path.read_text(encoding="utf-8")
            combined += json.dumps(state, ensure_ascii=False)
            combined += render_markdown(state)

        canaries = (
            "CANARY_DISPLAY_TEXT",
            "CANARY_PASTED_DISPLAY",
            "CANARY_TOOL_RESULTS",
            "CANARY_ASSISTANT_THINKING",
            "CANARY_TOOL_NAME",
            "CANARY_TOOL_INPUT",
        )
        for value in canaries:
            self.assertNotIn(value, combined)


class ProviderScopeTests(unittest.TestCase):
    """Tests for --provider selection, env var replacement, and scope-based comparison."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.codex_dir = self.temp_dir / "codex_home"
        self.claude_dir = self.temp_dir / "claude_home"
        self.codex_dir.mkdir(parents=True, exist_ok=True)
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        (self.codex_dir / "history.jsonl").write_text("{}", encoding="utf-8")
        (self.claude_dir / "history.jsonl").write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ── provider discovery ──────────────────────────────────────────────────

    def test_provider_codex_excludes_claude(self) -> None:
        """provider=codex discovers only codex paths."""
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_dir), "CLAUDE_HOME": str(self.claude_dir)}):
            sources = discover_sources(provider="codex")
        self.assertGreater(len(sources), 0)
        for source in sources:
            self.assertIn(str(self.codex_dir.resolve()), str(source))
        claude_paths = [s for s in sources if str(self.claude_dir.resolve()) in str(s)]
        self.assertEqual(0, len(claude_paths))

    def test_provider_claude_excludes_codex(self) -> None:
        """provider=claude discovers only claude paths."""
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_dir), "CLAUDE_HOME": str(self.claude_dir)}):
            sources = discover_sources(provider="claude")
        self.assertGreater(len(sources), 0)
        for source in sources:
            self.assertIn(str(self.claude_dir.resolve()), str(source))
        codex_paths = [s for s in sources if str(self.codex_dir.resolve()) in str(s)]
        self.assertEqual(0, len(codex_paths))

    def test_provider_all_discovers_both(self) -> None:
        """provider=all discovers both codex and claude paths."""
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_dir), "CLAUDE_HOME": str(self.claude_dir)}):
            sources = discover_sources(provider="all")
        self.assertGreaterEqual(len(sources), 2)

    def test_env_var_replaces_default_codex(self) -> None:
        """CODEX_HOME replaces ~/.codex — not added alongside it."""
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_dir)}):
            root = _provider_root("codex")
        self.assertEqual(self.codex_dir.resolve(), root)

    def test_env_var_not_set_codex(self) -> None:
        """When CODEX_HOME is unset, _provider_root falls back to ~/.codex."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEX_HOME", None)
            root = _provider_root("codex")
        self.assertEqual(Path.home() / ".codex", root)

    # ── scope-based previous report matching ────────────────────────────────

    def _write_report(self, directory: Path, filename: str, scope: dict, prompts: int = 10, schema_version: int = 4) -> Path:
        data = {"schema_version": schema_version, "scope": scope, "metrics": {"prompts": prompts}}
        path = directory / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_find_compatible_previous_matches_scope(self) -> None:
        """find_compatible_previous skips incompatible scopes and finds the matching one."""
        scope = {"provider": "all", "source_mode": "auto", "period_duration_seconds": 604800}
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            # Newest — incompatible (different duration)
            self._write_report(output_dir, "retrospective-20260703T000000Z.json",
                               {"provider": "all", "source_mode": "auto", "period_duration_seconds": 1209600}, prompts=8)
            # Middle — matching
            self._write_report(output_dir, "retrospective-20260702T000000Z.json", scope, prompts=10)
            # Oldest — incompatible (different provider)
            self._write_report(output_dir, "retrospective-20260701T000000Z.json",
                               {"provider": "codex", "source_mode": "auto", "period_duration_seconds": 604800}, prompts=5)

            result = find_compatible_previous(output_dir, scope)
        self.assertIsNotNone(result)
        self.assertEqual(10, result["metrics"]["prompts"])

    def test_find_compatible_previous_none_when_no_match(self) -> None:
        """find_compatible_previous returns None when no report matches the scope."""
        scope = {"provider": "all", "source_mode": "auto", "period_duration_seconds": 604800}
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            self._write_report(output_dir, "retrospective-20260701T000000Z.json",
                               {"provider": "codex", "source_mode": "auto", "period_duration_seconds": 604800}, prompts=5)
            result = find_compatible_previous(output_dir, scope)
        self.assertIsNone(result)

    def test_find_compatible_previous_empty_dir(self) -> None:
        """find_compatible_previous returns None when no reports exist."""
        with tempfile.TemporaryDirectory() as temporary:
            result = find_compatible_previous(Path(temporary),
                                              {"provider": "all", "source_mode": "auto", "period_duration_seconds": 604800})
        self.assertIsNone(result)

    # ── exact period-duration matching ───────────────────────────────────

    def test_period_duration_seconds_exact(self) -> None:
        """Same exact seconds → compatible."""
        scope = {"provider": "all", "source_mode": "auto", "period_duration_seconds": 604800}
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            self._write_report(output_dir, "retrospective-20260701T000000Z.json", scope, prompts=5)
            result = find_compatible_previous(output_dir, scope)
        self.assertIsNotNone(result)
        self.assertEqual(5, result["metrics"]["prompts"])

    def test_period_duration_seconds_mismatch(self) -> None:
        """7 days vs 7 days 23 hours → incompatible."""
        scope_7d = {"provider": "all", "source_mode": "auto", "period_duration_seconds": 604800}
        scope_7d23h = {"provider": "all", "source_mode": "auto", "period_duration_seconds": 687600}
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            self._write_report(output_dir, "retrospective-20260701T000000Z.json", scope_7d23h, prompts=5)
            result = find_compatible_previous(output_dir, scope_7d)
        self.assertIsNone(result)

    # ── schema-version incompatibility ───────────────────────────────────

    def test_schema_version_incompatible(self) -> None:
        """Schema v2 and v4 are incompatible (v3 reports are readable but not auto-compared)."""
        scope = {"provider": "all", "source_mode": "auto", "period_duration_seconds": 604800}
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            self._write_report(output_dir, "retrospective-20260701T000000Z.json", scope, prompts=5, schema_version=2)
            result = find_compatible_previous(output_dir, scope)
        self.assertIsNone(result)

    # ── provider counts ──────────────────────────────────────────────────

    def test_provider_counts_codex(self) -> None:
        """count_source_files with provider=codex returns only Codex count."""
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_dir), "CLAUDE_HOME": str(self.claude_dir)}):
            counts = count_source_files("codex")
        self.assertIn("Codex", counts)
        self.assertNotIn("Claude Code", counts)
        self.assertEqual(1, counts["Codex"])

    def test_provider_counts_all(self) -> None:
        """count_source_files with provider=all returns both counts."""
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_dir), "CLAUDE_HOME": str(self.claude_dir)}):
            counts = count_source_files("all")
        self.assertIn("Codex", counts)
        self.assertIn("Claude Code", counts)
        self.assertEqual(1, counts["Codex"])
        self.assertEqual(1, counts["Claude Code"])

    # ── CLI mutual exclusion ─────────────────────────────────────────────

    def test_provider_source_mutual_exclusion(self) -> None:
        """--provider and --source together should exit with error."""
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "review.py"),
             "--provider", "codex", "--source", str(TESTS_ROOT / "fixtures" / "synthetic_history.jsonl"),
             "--days", "7"],
            capture_output=True, text=True, cwd=str(REPO_ROOT)
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("mutually exclusive", result.stderr)

    def test_explicit_source_is_custom(self) -> None:
        """--source should result in provider=custom in dry-run output."""
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "review.py"),
             "--source", str(TESTS_ROOT / "fixtures" / "synthetic_history.jsonl"),
             "--days", "7", "--dry-run"],
            capture_output=True, text=True, cwd=str(REPO_ROOT)
        )
        self.assertEqual(0, result.returncode)
        self.assertIn("provider: custom", result.stdout)
        self.assertIn("Custom files:", result.stdout)

    def test_custom_no_auto_comparison(self) -> None:
        """Two explicit source reports in the same dir do not compare."""
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            # Run 1: synthetic_history
            subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "review.py"),
                 "--source", str(TESTS_ROOT / "fixtures" / "synthetic_history.jsonl"),
                 "--since", "2026-07-01", "--until", "2026-08-01",
                 "--output-dir", str(output_dir)],
                capture_output=True, text=True, cwd=str(REPO_ROOT), check=False
            )
            # Run 2: claude_code_history (different source, same period)
            subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "review.py"),
                 "--source", str(TESTS_ROOT / "fixtures" / "claude_code_history.jsonl"),
                 "--since", "2026-07-01", "--until", "2026-08-01",
                 "--output-dir", str(output_dir)],
                capture_output=True, text=True, cwd=str(REPO_ROOT), check=False
            )
            jsons = sorted(output_dir.glob("retrospective-*.json"), reverse=True)
            self.assertGreaterEqual(len(jsons), 1)
            latest_state = json.loads(jsons[0].read_text(encoding="utf-8"))
            self.assertIsNone(latest_state.get("comparison"))
            self.assertEqual("custom", latest_state.get("scope", {}).get("provider"))

    def test_schema_probe_requires_provider(self) -> None:
        """schema_probe.py should error without --provider."""
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "schema_probe.py"), "--days", "7"],
            capture_output=True, text=True, cwd=str(REPO_ROOT)
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("required", result.stderr)


class VersionTest(unittest.TestCase):
    """TOOL_VERSION and --version flag."""

    def test_version_flag(self) -> None:
        """--version should print version and exit with code 0."""
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "review.py"), "--version"],
            capture_output=True, text=True, cwd=str(REPO_ROOT)
        )
        self.assertEqual(0, result.returncode)
        self.assertIn("0.1.0-beta.1", result.stdout)


if __name__ == "__main__":
    unittest.main()
