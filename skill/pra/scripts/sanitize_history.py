#!/usr/bin/env python3
"""Convert Codex / Claude Code JSONL into anonymous, content-free event metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TextIO

from verification import detect_verification, parse_exit_code
from context_artifacts import detect_context_action, detect_decision_signal

SCHEMA_VERSION = 1
BLOCKED_KEYS = {
    "pastedcontents",
    "pastedcontent",
    "clipboard",
    "clipboardcontent",
    "filecontents",
    "filecontent",
    "binarydata",
    "base64",
    "imageurl",
    "image_url",
    "localimages",
    "local_images",
}

CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
PASTED_TAG_RE = re.compile(
    r"<\s*pastedcontents\b[^>]*>.*?<\s*/\s*pastedcontents\s*>",
    re.IGNORECASE | re.DOTALL,
)
INJECTED_BLOCK_RES = tuple(
    re.compile(
        rf"<\s*{tag}\b[^>]*>.*?<\s*/\s*{tag}\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    for tag in (
        "recommended_plugins",
        "environment_context",
        r"permissions\s+instructions",
        "app-context",
        "collaboration_mode",
        "skills_instructions",
        "apps_instructions",
        "plugins_instructions",
        "system",
        "developer",
    )
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
WINDOWS_PATH_RE = re.compile(r"(?<!\w)[A-Za-z]:\\(?:[^\s<>:\"|?*]+\\)*[^\s<>:\"|?*]*")
UNIX_PATH_RE = re.compile(r"(?<![\w:])/(?:[^\s/]+/)+[^\s/]*")
SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|rk|pk)-(?:proj-)?[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:bearer|authorization)\s*[:=]?\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]{4,}"),
)

CORRECTION_RE = re.compile(
    r"(?i)(?:\bno[,!.;?]|\bwrong\b|\binstead\b|\brevert\b|неверно|не так|нет[,!]|исправ|вместо|отмени|передел)"
)
SUCCESS_RE = re.compile(
    r"(?i)(?:\bworks\b|\bperfect\b|\bgreat\b|"
    r"(?<!не )готово|(?<!не )работает|(?<!не )идеально|(?<!не )отлично|"
    r"спасибо)"
)
VERIFY_RE = re.compile(r"(?i)(?:\btest|verify|check|lint|build\b|тест|проверь|провер|сборк|линт)")
CONSTRAINT_RE = re.compile(r"(?i)(?:\bmust\b|\bwithout\b|\bonly\b|\bdo not\b|нужно|нельзя|только|без |не (?:делай|меняй|используй))")
GOAL_RE = re.compile(r"(?i)(?:\bfix\b|\badd\b|\bcreate\b|\bimplement\b|\bupdate\b|\bremove\b|исправ|добав|созда|реализ|обнов|удал)")
SCOPE_RE = re.compile(r"(?i)(?:\bscope\b|\bwithin\b|\bonly change\b|\blimit(?:ed)? to\b|границ|объём|только в|не меняй)")
READINESS_RE = re.compile(r"(?i)(?:\bacceptance criteria\b|\bdefinition of done\b|\bdone when\b|\bexpected result\b|\bready when\b|критерий готов|готово когда|ожидаемый результат)")
FILE_HINT_RE = re.compile(r"(?:[A-Za-z]:\\|/[^\s]+/|\b[\w.-]+\.(?:py|js|ts|tsx|rs|go|java|md|json|ya?ml)\b)")


@dataclass
class ScanStats:
    files: int = 0
    lines: int = 0
    events: int = 0
    assistant_events: int = 0
    dropped_fields: int = 0
    invalid_json: int = 0
    outside_period: int = 0
    undated: int = 0
    skipped_non_user: int = 0
    duplicate_events: int = 0
    empty_after_sanitization: int = 0
    high_risk_events: int = 0
    internal_user_events: int = 0
    source_labels: list[str] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "lines": self.lines,
            "events": self.events,
            "assistant_events": self.assistant_events,
            "dropped_fields": self.dropped_fields,
            "invalid_json": self.invalid_json,
            "outside_period": self.outside_period,
            "undated": self.undated,
            "skipped_non_user": self.skipped_non_user,
            "duplicate_events": self.duplicate_events,
            "empty_after_sanitization": self.empty_after_sanitization,
            "high_risk_events": self.high_risk_events,
            "internal_user_events": self.internal_user_events,
            "sources": self.source_labels,
        }


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", key.lower())


def load_json_safely(line: str) -> tuple[Any, int]:
    """Drop forbidden fields while decoding and never return their values."""
    dropped = 0

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal dropped
        result: dict[str, Any] = {}
        for key, value in pairs:
            if _normalized_key(str(key)) in BLOCKED_KEYS:
                dropped += 1
                continue
            result[key] = value
        return result

    return json.loads(line, object_pairs_hook=hook), dropped


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp /= 1000
        try:
            return datetime.fromtimestamp(stamp, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def find_timestamp(obj: dict[str, Any]) -> datetime | None:
    for container in (obj, obj.get("payload")):
        if not isinstance(container, dict):
            continue
        for key in ("timestamp", "created_at", "createdAt", "time", "ts"):
            parsed = parse_timestamp(container.get(key))
            if parsed:
                return parsed
    return None


def private_label(prefix: str, value: str, salt: bytes) -> str:
    digest = hashlib.sha256(salt + value.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def classify(obj: dict[str, Any]) -> tuple[str | None, str]:
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    top_type = str(obj.get("type", "")).lower()
    payload_type = str(payload.get("type", "")).lower()
    role = _first_string(obj.get("role"), msg.get("role"), payload.get("role"))
    role = role.lower() if role else None

    # Claude Code structured format: {"type":"user","message":{"role":"user","content":...}}
    if top_type == "user" and isinstance(msg.get("role"), str) and msg["role"].lower() == "user":
        return "user", "user_message"

    # Codex Desktop display format: {"display":"...","pastedContents":{},...}
    if isinstance(obj.get("display"), str) and obj.get("display"):
        if role not in ("assistant", "system"):
            return "user", "display_message"

    if payload_type in {"user_message", "input_text"}:
        role = "user"
    elif payload_type in {"assistant_message", "output_text"}:
        role = "assistant"
    elif (
        isinstance(obj.get("text"), str)
        and obj.get("session_id") is not None
        and obj.get("ts") is not None
    ):
        role = role or "user"

    kind = payload_type or top_type or "unknown"
    return role, kind


def user_text_parts(obj: dict[str, Any]) -> list[str]:
    """Extract only user message text from known Codex / Claude Code layouts."""
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    containers = (obj, payload)
    parts: list[str] = []

    # Codex Desktop display format: {"display":"...",...}
    display = obj.get("display")
    if isinstance(display, str) and display.strip():
        parts.append(display)

    # Claude Code structured format: {"type":"user","message":{"role":"user","content":...}}
    content = msg.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "")).lower()
            if item_type in {"input_text", "text", "user_message"}:
                value = _first_string(item.get("text"), item.get("message"))
                if value:
                    parts.append(value)

    # Old format extraction
    for container in containers:
        for key in ("text", "message"):
            value = container.get(key)
            if isinstance(value, str):
                parts.append(value)
        content = container.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "")).lower()
                if item_type not in {"input_text", "text", "user_message"}:
                    continue
                value = _first_string(item.get("text"), item.get("message"))
                if value:
                    parts.append(value)
    return parts


def sanitize_for_metrics(text: str) -> tuple[str, int]:
    redactions = 0
    cleaned = text
    for pattern in INJECTED_BLOCK_RES:
        cleaned, count = pattern.subn(" ", cleaned)
        redactions += count
    cleaned, count = PASTED_TAG_RE.subn(" <PASTED_CONTENT> ", cleaned)
    redactions += count
    cleaned, count = CODE_FENCE_RE.subn(" <CODE_BLOCK> ", cleaned)
    redactions += count
    for pattern, replacement in (
        (EMAIL_RE, "<EMAIL>"),
        (IP_RE, "<IP>"),
        (WINDOWS_PATH_RE, "<PATH>"),
        (UNIX_PATH_RE, "<PATH>"),
    ):
        cleaned, count = pattern.subn(replacement, cleaned)
        redactions += count
    for pattern in SECRET_PATTERNS:
        cleaned, count = pattern.subn("<SECRET>", cleaned)
        redactions += count
    return cleaned, redactions


def derive_metrics(text: str, dropped_fields: int, text_parts: int = 1) -> tuple[dict[str, Any], list[str], str]:
    cleaned, redactions = sanitize_for_metrics(text)
    had_file_hint = "<PATH>" in cleaned or bool(FILE_HINT_RE.search(cleaned))
    compact = " ".join(cleaned.split())
    char_count = min(len(compact), 20_000)
    signals: list[str] = []
    for name, pattern in (
        ("correction", CORRECTION_RE),
        ("success", SUCCESS_RE),
        ("verification", VERIFY_RE),
        ("constraint", CONSTRAINT_RE),
        ("goal", GOAL_RE),
        ("scope", SCOPE_RE),
        ("readiness", READINESS_RE),
    ):
        if pattern.search(compact):
            signals.append(name)
    if char_count < 40:
        signals.append("short-prompt")
    if had_file_hint:
        signals.append("file-context")
    if redactions or dropped_fields:
        signals.append("privacy-redaction")

    metrics = {
        "raw_characters_bucket": _length_bucket(len(text)),
        "characters_bucket": _length_bucket(char_count),
        "text_parts": min(text_parts, 99),
        "question_count": min(compact.count("?"), 9),
        "redaction_count": min(redactions + dropped_fields, 99),
        "has_goal": "goal" in signals,
        "has_constraint": "constraint" in signals,
        "has_verification": "verification" in signals,
        "has_file_context": had_file_hint,
        "has_scope": "scope" in signals,
        "has_readiness": "readiness" in signals,
    }
    summary_bits = [
        f"length={metrics['characters_bucket']}",
        f"raw-length={metrics['raw_characters_bucket']}",
        f"parts={metrics['text_parts']}",
        f"redactions={metrics['redaction_count']}",
    ]
    if signals:
        summary_bits.append("signals=" + ",".join(signals))
    return metrics, signals, "; ".join(summary_bits)


def _length_bucket(length: int) -> str:
    if length == 0:
        return "empty"
    if length < 40:
        return "short"
    if length < 200:
        return "medium"
    if length < 800:
        return "long"
    return "very-long"


def is_high_risk_content(cleaned_text: str, redactions: int) -> bool:
    """Exclude context-heavy records instead of interpreting uncertain content."""
    compact_length = len(" ".join(cleaned_text.split()))
    return compact_length >= 800 and redactions >= 10


PROVIDER_CHOICES = ("codex", "claude", "hermes", "all")


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser().resolve()


def _hermes_db_path() -> Path:
    return _hermes_home() / "state.db"


def _merge_stats(target: ScanStats, source: ScanStats) -> None:
    for field_name in (
        "files", "lines", "events", "assistant_events", "dropped_fields", "invalid_json",
        "outside_period", "undated", "skipped_non_user", "duplicate_events",
        "empty_after_sanitization", "high_risk_events", "internal_user_events",
    ):
        setattr(target, field_name, getattr(target, field_name) + getattr(source, field_name))
    target.source_labels.extend(source.source_labels)


def _hermes_text(value: Any) -> list[str]:
    """Extract metric input from Hermes content without retaining the raw value."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return [value]
        if parsed != value:
            return _hermes_text(parsed)
        return [value]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_hermes_text(item))
        return parts
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "content", "message", "input"):
            if key in value:
                parts.extend(_hermes_text(value[key]))
        return parts
    return []


def scan_hermes_database(
    source: Path,
    since: datetime,
    until: datetime,
    output: TextIO,
    salt: bytes,
) -> ScanStats:
    """Read Hermes state.db and emit anonymous metadata events only."""
    stats = ScanStats(files=1)
    source_label = private_label("source", str(source), salt)
    stats.source_labels.append(source_label)
    session_projects: dict[str, str] = {}
    emitted: list[dict[str, Any]] = []
    pending_verifications: dict[str, dict[str, Any]] = {}
    pending_context: dict[str, dict[str, Any]] = {}
    try:
        connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return stats
    try:
        for session_id, cwd in connection.execute("SELECT id, cwd FROM sessions"):
            if session_id and cwd:
                session_projects[str(session_id)] = private_label("project", str(cwd), salt)
        rows = connection.execute(
            "SELECT id, session_id, role, content, tool_calls, tool_call_id, tool_name, timestamp, display_kind "
            "FROM messages ORDER BY timestamp, id"
        )
        for message_id, raw_session, role, content, tool_calls, tool_call_id, tool_name, raw_timestamp, display_kind in rows:
            stats.lines += 1
            timestamp = parse_timestamp(raw_timestamp)
            if timestamp is None:
                stats.undated += 1
                continue
            if timestamp < since or timestamp >= until:
                stats.outside_period += 1
                continue
            session_value = str(raw_session or "unknown")
            session_hint = private_label("session", session_value, salt)
            kind = str(display_kind or role or "unknown")
            event_seed = f"{source_label}:{message_id}:{timestamp.isoformat()}"
            normalized_role = str(role or "").lower()
            if normalized_role in {"assistant", "tool"}:
                if normalized_role == "tool":
                    if tool_call_id in pending_verifications:
                        exit_code = parse_exit_code(content)
                        if exit_code is not None:
                            pending_verifications[tool_call_id]["verification_status"] = (
                                "passed" if exit_code == 0 else "failed"
                            )
                    if tool_call_id in pending_context:
                        result = content
                        if isinstance(result, str):
                            try:
                                result = json.loads(result)
                            except (json.JSONDecodeError, TypeError):
                                result = {}
                        success = (isinstance(result, dict) and result.get("success") is True) or (
                            isinstance(result, dict) and result.get("exit_code") == 0
                        )
                        if success:
                            event_ref = pending_context.pop(tool_call_id)
                            event_ref.update(event_ref.pop("_context_pending", {}))
                verification_categories: list[str] = []
                call_ids: list[str] = []
                context_call_ids: list[str] = []
                context_flags: dict[str, bool] = {}
                if normalized_role == "assistant" and tool_calls:
                    try:
                        calls = json.loads(tool_calls)
                    except (json.JSONDecodeError, TypeError):
                        calls = []
                    if isinstance(calls, list):
                        for call in calls:
                            if not isinstance(call, dict):
                                continue
                            function = call.get("function") if isinstance(call.get("function"), dict) else {}
                            categories = detect_verification(function.get("name"), function.get("arguments"))
                            verification_categories.extend(categories)
                            detected_context = detect_context_action(function.get("name"), function.get("arguments"))
                            context_flags.update(detected_context)
                            call_id = call.get("call_id") or call.get("id")
                            if call_id and detected_context:
                                context_call_ids.append(str(call_id))
                            if call_id and categories:
                                call_ids.append(str(call_id))
                event = {
                    "schema_version": SCHEMA_VERSION,
                    "event_id": private_label("event", event_seed, salt),
                    "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                    "session": session_hint,
                    "role": "assistant",
                    "kind": kind,
                    "has_tool_calls": bool(tool_calls) or normalized_role == "tool",
                    "tool_content_available": bool(content) or bool(tool_calls),
                    "evidence": {"mode": "metadata", "provider": "hermes"},
                }
                if verification_categories:
                    event["verification_categories"] = sorted(set(verification_categories))
                    event["verification_status"] = None
                    for call_id in call_ids:
                        pending_verifications[call_id] = event
                if context_flags:
                    event["_context_pending"] = context_flags
                    for call_id in context_call_ids:
                        pending_context[call_id] = event
                emitted.append(event)
                stats.assistant_events += 1
                continue
            if normalized_role != "user":
                stats.skipped_non_user += 1
                continue
            text_parts = _hermes_text(content)
            raw_text = "\\n".join(part for part in text_parts if part)
            if not raw_text:
                continue
            cleaned_text, cleaning_redactions = sanitize_for_metrics(raw_text)
            if not cleaned_text.strip():
                stats.empty_after_sanitization += 1
                continue
            if is_high_risk_content(cleaned_text, cleaning_redactions):
                stats.high_risk_events += 1
                continue
            metrics, signals, evidence_summary = derive_metrics(raw_text, 0, len(text_parts))
            event = {
                "schema_version": SCHEMA_VERSION,
                "event_id": private_label("event", event_seed, salt),
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "session": session_hint,
                "project": session_projects.get(session_value, "project-unknown"),
                "role": "user",
                "kind": kind,
                "metrics": metrics,
                "signals": signals,
                "evidence": {"mode": "metadata", "provider": "hermes", "summary": evidence_summary},
            }
            if detect_decision_signal(cleaned_text):
                event["decision_recorded"] = True
            emitted.append(event)
            stats.events += 1
    finally:
        connection.close()
    ordered = sorted(emitted, key=lambda item: (str(item["timestamp"]), str(item["event_id"])))
    handoff_seen: set[str] = set()
    for event in ordered:
        session = str(event.get("session") or "unknown")
        if event.get("handoff_created"):
            handoff_seen.add(session)
        elif event.get("role") == "user" and session in handoff_seen:
            event["resume_after_handoff"] = True
            handoff_seen.remove(session)
        event.pop("_context_pending", None)
        output.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return stats

def _provider_root(provider_key: str) -> Path | None:
    """Resolve the root directory for a provider.

    Environment variable **replaces** the default path (never adds to it).
    Returns None if neither env var nor default directory exists.
    """
    if provider_key == "codex":
        env = os.environ.get("CODEX_HOME")
        return Path(env).expanduser().resolve() if env else Path.home() / ".codex"
    if provider_key == "claude":
        env = os.environ.get("CLAUDE_HOME")
        return Path(env).expanduser().resolve() if env else Path.home() / ".claude"
    return None


def _find_jsonl_files(root: Path) -> list[Path]:
    """List all JSONL files inside a provider root (no dedup, no sorting)."""
    candidates: list[Path] = []
    history = root / "history.jsonl"
    if history.is_file():
        candidates.append(history)
    for directory_name in ("sessions", "archived_sessions"):
        directory = root / directory_name
        if directory.is_dir():
            candidates.extend(path for path in directory.rglob("*.jsonl") if path.is_file())
    projects = root / "projects"
    if projects.is_dir():
        candidates.extend(projects.glob("*/*.jsonl"))
    return candidates


def discover_sources(provider: str = "all") -> list[Path]:
    """Discover Codex and Claude Code session history files.

    Args:
        provider: ``"codex"`` — only CODEX_HOME / ``~/.codex``
                  ``"claude"`` — only CLAUDE_HOME / ``~/.claude``
                  ``"all"`` — both (each with its own override rules)

    Returns:
        Sorted list of JSONL file paths.
    """
    roots: list[Path] = []
    if provider in ("codex", "all"):
        root = _provider_root("codex")
        if root is not None:
            roots.append(root)
    if provider in ("claude", "all"):
        root = _provider_root("claude")
        if root is not None:
            roots.append(root)

    seen: set[str] = set()
    candidates: list[Path] = []
    for root in roots:
        resolved = str(root.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        if not root.is_dir():
            continue
        candidates.extend(_find_jsonl_files(root))
    if provider in ("hermes", "all"):
        hermes_db = _hermes_db_path()
        if hermes_db.is_file():
            candidates.append(hermes_db)
    return sorted(set(candidates))


def count_source_files(provider: str) -> dict[str, int]:
    """Count JSONL files per provider label **without exposing paths**.

    Used only for the safe dry-run display.
    """
    counts: dict[str, int] = {}
    for prov_key, label in (("codex", "Codex"), ("claude", "Claude Code")):
        if provider not in (prov_key, "all"):
            continue
        root = _provider_root(prov_key)
        if root is not None and root.is_dir():
            files = _find_jsonl_files(root)
            if files:
                counts[label] = len(files)
    if provider in ("hermes", "all") and _hermes_db_path().is_file():
        counts["Hermes"] = 1
    return counts


def expand_sources(inputs: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    for source in inputs:
        resolved = source.expanduser().resolve()
        if resolved.is_file() and resolved.suffix.lower() == ".jsonl":
            result.append(resolved)
        elif resolved.is_dir():
            result.extend(path for path in resolved.rglob("*.jsonl") if path.is_file())
    return sorted(set(result))


def scan_sources(
    sources: Iterable[Path],
    since: datetime,
    until: datetime,
    output: TextIO,
    salt: bytes,
) -> ScanStats:
    stats = ScanStats()
    accepted_events: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    for source in sources:
        if source.name == "state.db":
            hermes_stats = scan_hermes_database(source, since, until, output, salt)
            _merge_stats(stats, hermes_stats)
            continue
        source_label = private_label("source", str(source), salt)
        stats.source_labels.append(source_label)
        stats.files += 1
        session_hint = private_label("session", str(source), salt)
        project_hint = "project-unknown"
        try:
            handle = source.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line_number, line in enumerate(handle, start=1):
                stats.lines += 1
                try:
                    obj, dropped = load_json_safely(line)
                except (json.JSONDecodeError, TypeError):
                    stats.invalid_json += 1
                    continue
                stats.dropped_fields += dropped
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                raw_session = _first_string(
                    obj.get("session_id"), obj.get("sessionId"), payload.get("session_id")
                )
                if raw_session is None and str(obj.get("type", "")).lower() == "session_meta":
                    raw_session = _first_string(payload.get("id"))
                if raw_session:
                    session_hint = private_label("session", raw_session, salt)
                raw_project = _first_string(obj.get("cwd"), obj.get("project"), payload.get("cwd"))
                if raw_project:
                    project_hint = private_label("project", raw_project, salt)

                timestamp = find_timestamp(obj)
                if timestamp is None:
                    stats.undated += 1
                    continue
                if timestamp < since or timestamp >= until:
                    stats.outside_period += 1
                    continue
                role, kind = classify(obj)
                if role == "assistant":
                    # Write assistant events for tool ratio
                    has_tool = False
                    content_available = False
                    # Claude Code: message.content[].type
                    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                    if "content" in msg:
                        content = msg["content"]
                        if isinstance(content, list):
                            content_available = True
                            for item in content:
                                if isinstance(item, dict) and str(item.get("type", "")).lower() in ("tool_use", "tool_call"):
                                    has_tool = True
                                    break
                    # Codex: payload.content[].type
                    if not has_tool:
                        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                        if "content" in payload:
                            payload_content = payload["content"]
                            if isinstance(payload_content, list):
                                content_available = True
                                for item in payload_content:
                                    if isinstance(item, dict) and str(item.get("type", "")).lower() in ("tool_call",):
                                        has_tool = True
                                        break
                    event = {
                        "schema_version": SCHEMA_VERSION,
                        "event_id": private_label("event", f"{source_label}:{line_number}:{timestamp.isoformat()}", salt),
                        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                        "session": session_hint,
                        "role": "assistant",
                        "kind": kind,
                        "has_tool_calls": has_tool,
                        "tool_content_available": content_available,
                        "evidence": {"mode": "metadata"},
                    }
                    output.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                    stats.assistant_events += 1
                    continue
                if role != "user":
                    stats.skipped_non_user += 1
                    continue
                record_type = str(obj.get("type", "")).lower()
                is_internal_desktop_turn = (
                    record_type == "event_msg"
                    and kind == "user_message"
                    and "client_id" not in payload
                ) or (
                    record_type == "response_item"
                    and kind == "message"
                    and "internal_chat_message_metadata_passthrough" in payload
                )
                if is_internal_desktop_turn:
                    stats.internal_user_events += 1
                    continue
                user_type = str(obj.get("userType", "")).lower()
                if user_type == "internal":
                    stats.internal_user_events += 1
                    continue
                text_parts = user_text_parts(obj)
                raw_text = "\n".join(text_parts)
                if not raw_text:
                    continue
                timestamp_second = timestamp.replace(microsecond=0).isoformat()
                duplicate_key = (session_hint, timestamp_second)
                cleaned_text, cleaning_redactions = sanitize_for_metrics(raw_text)
                if not cleaned_text.strip():
                    stats.empty_after_sanitization += 1
                    continue
                if is_high_risk_content(cleaned_text, cleaning_redactions + dropped):
                    stats.high_risk_events += 1
                    continue
                metrics, signals, evidence_summary = derive_metrics(raw_text, dropped, len(text_parts))
                event_seed = f"{source_label}:{line_number}:{timestamp.isoformat()}"
                event = {
                    "schema_version": SCHEMA_VERSION,
                    "event_id": private_label("event", event_seed, salt),
                    "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                    "session": session_hint,
                    "project": project_hint,
                    "role": "user",
                    "kind": kind,
                    "metrics": metrics,
                    "signals": signals,
                    "evidence": {"mode": "metadata", "summary": evidence_summary},
                }
                priority = 2 if kind == "user_message" else 1
                existing = accepted_events.get(duplicate_key)
                if existing is not None:
                    stats.duplicate_events += 1
                    if existing[0] >= priority:
                        continue
                accepted_events[duplicate_key] = (priority, event)
    ordered_events = sorted(
        (event for _, event in accepted_events.values()),
        key=lambda event: (str(event["timestamp"]), str(event["event_id"])),
    )
    for event in ordered_events:
        output.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    stats.events = len(ordered_events)
    return stats


def _parse_cli_date(value: str) -> datetime:
    parsed = parse_timestamp(value)
    if not parsed:
        raise argparse.ArgumentTypeError("Use an ISO date or timestamp")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", type=Path, required=True)
    parser.add_argument("--since", type=_parse_cli_date, required=True)
    parser.add_argument("--until", type=_parse_cli_date, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--salt", default="pra-local-v1")
    args = parser.parse_args()
    if args.until <= args.since:
        parser.error("--until must be later than --since")
    sources = expand_sources(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as output:
        stats = scan_sources(sources, args.since, args.until, output, args.salt.encode("utf-8"))
    print(json.dumps(stats.public_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
