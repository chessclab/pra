#!/usr/bin/env python3
"""Build a local Markdown retrospective from sanitized event metadata."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

REPORT_SCHEMA_VERSION = 4
SCOPE_KEYS = ("provider", "source_mode", "period_duration_seconds")


def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("schema_version") == 1:
                events.append(item)
    return events


def _rate(count: int, total: int) -> float:
    return round((count / total * 100) if total else 0.0, 1)


def _event_refs(events: Iterable[dict[str, Any]], signal: str | None = None, limit: int = 3) -> list[dict[str, str]]:
    _events = list(events)
    refs: list[dict[str, str]] = []
    for event in _events:
        if signal and signal not in event.get("signals", []):
            continue
        refs.append(
            {
                "event_id": str(event.get("event_id", "event-unknown")),
                "timestamp": str(event.get("timestamp", "unknown")),
                "session": str(event.get("session", "session-unknown")),
                "summary": str(event.get("evidence", {}).get("summary", "metadata only")),
            }
        )
        if len(refs) >= limit:
            break
    return refs


def _build_recommendations(
    events: list[dict[str, Any]],
    metrics: dict[str, Any],
    rates: dict[str, Any],
) -> list[dict[str, Any]]:
    _events = list(events)
    # Evidence must only reference user events, never assistant events
    user_events = [e for e in _events if e.get("role", "user") == "user"]
    recommendations: list[dict[str, Any]] = []
    prompt_count = metrics["prompts"]

    if prompt_count >= 4 and rates["verification_rate"] < 25:
        recommendations.append(
            {
                "title": "Явно задавать критерий проверки",
                "reason": f"Проверка или тесты упомянуты только в {rates['verification_rate']}% запросов.",
                "action": "Для задач с изменениями добавлять ожидаемую проверку: тест, lint, build или конкретный сценарий.",
                "evidence": _event_refs(user_events, limit=3),
            }
        )
    if prompt_count >= 4 and rates["constraint_rate"] < 30:
        recommendations.append(
            {
                "title": "Фиксировать границы задачи в первом запросе",
                "reason": f"Явные ограничения обнаружены в {rates['constraint_rate']}% запросов.",
                "action": "Добавлять, что разрешено менять, что нельзя трогать и какой результат считается завершённым.",
                "evidence": _event_refs(user_events, limit=3),
            }
        )
    if metrics["repeated_correction_sessions"] >= 1:
        recommendations.append(
            {
                "title": "Проверять понимание перед широкими изменениями",
                "reason": f"Обнаружено {metrics['corrections']} сигналов исправления в {metrics['repeated_correction_sessions']} повторяющихся сессиях.",
                "action": "Для неоднозначных задач сначала кратко зафиксировать план и область изменений.",
                "evidence": _event_refs(user_events, "correction"),
            }
        )
    if prompt_count >= 4 and rates["short_prompt_rate"] > 45:
        recommendations.append(
            {
                "title": "Добавлять минимальный контекст к коротким командам",
                "reason": f"Короткие запросы составляют {rates['short_prompt_rate']}% периода.",
                "action": "Указывать цель, затрагиваемый компонент и ожидаемую проверку хотя бы одной строкой.",
                "evidence": _event_refs(user_events, "short-prompt"),
            }
        )
    return recommendations


def _compute_assistant_events_per_user_turn(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Assistant events counted for each user turn, including trailing events.

    For every user event, count all assistant events up to (but not including)
    the next user event in the same session.  Null counts (user messages with
    no assistant reply) are included.  Sessions are processed independently.
    """
    session_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        session_events[str(event.get("session", "unknown"))].append(event)
    for evts in session_events.values():
        evts.sort(key=lambda e: str(e.get("timestamp", "")))

    counts: list[int] = []
    for sess_events in session_events.values():
        assistant_count = 0
        has_user = False
        for event in sess_events:
            role = event.get("role", "user")
            if role == "user":
                if has_user:
                    counts.append(assistant_count)
                has_user = True
                assistant_count = 0
            elif role == "assistant":
                assistant_count += 1
        # Include trailing assistant events after the last user message
        if has_user:
            counts.append(assistant_count)

    if not counts:
        return {"count": 0, "mean": 0.0, "median": 0.0, "distribution": {}}

    return {
        "count": len(counts),
        "mean": round(statistics.mean(counts), 1),
        "median": round(statistics.median(counts), 1),
        "distribution": dict(sorted(Counter(counts).items())),
    }


def _compute_time_distribution(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Hour-of-day and day-of-week distribution from user event timestamps (UTC)."""
    user_events = [e for e in events if e.get("role", "user") == "user"]
    by_hour: Counter[int] = Counter()
    by_day: Counter[int] = Counter()
    late_night = 0
    weekend = 0

    for event in user_events:
        ts = event.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        by_hour[dt.hour] += 1
        by_day[dt.weekday()] += 1
        if dt.hour >= 23 or dt.hour < 6:
            late_night += 1
        if dt.weekday() >= 5:
            weekend += 1

    peak_hour = max(by_hour, key=by_hour.get) if by_hour else None
    peak_day = max(by_day, key=by_day.get) if by_day else None

    return {
        "by_hour": dict(sorted(by_hour.items())),
        "by_day": dict(sorted(by_day.items())),
        "late_night_events": late_night,
        "weekend_events": weekend,
        "peak_hour": peak_hour,
        "peak_day": peak_day,
    }


def _compute_tool_ratio(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Tool call ratio in assistant responses.

    Only events with a recognized content layout contribute to tool_ratio.
    Unknown-format events are counted separately.
    """
    assistant_events = [e for e in events if e.get("role") == "assistant"]
    total = len(assistant_events)
    recognized = [e for e in assistant_events if e.get("tool_content_available", False)]
    recognized_count = len(recognized)
    unknown_count = total - recognized_count
    with_tools = sum(1 for e in recognized if e.get("has_tool_calls", False))
    tool_ratio = round(with_tools / recognized_count * 100, 1) if recognized_count else None
    coverage_rate = round(recognized_count / total * 100, 1) if total else None
    return {
        "total_assistant_events": total,
        "recognized_format_count": recognized_count,
        "unknown_format_count": unknown_count,
        "with_tool_calls": with_tools,
        "tool_ratio": tool_ratio,
        "coverage_rate": coverage_rate,
    }


def _parse_event_dt(ts: Any) -> datetime | None:
    """Parse an event timestamp to a datetime, returning None on failure."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def _compute_response_time(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Approximate time from user message to next assistant reply in the same session.

    This is a diagnostic metric — actual response time depends on model,
    tool execution, tests, and environment.  Outliers > 10 min are excluded.
    """
    session_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        session_events[str(event.get("session", "unknown"))].append(event)
    for evts in session_events.values():
        evts.sort(key=lambda e: str(e.get("timestamp", "")))

    gaps: list[float] = []
    excluded = 0
    for evts in session_events.values():
        for i in range(len(evts) - 1):
            cur = evts[i]
            nxt = evts[i + 1]
            if cur.get("role", "user") == "user" and nxt.get("role") == "assistant":
                cur_dt = _parse_event_dt(cur.get("timestamp"))
                nxt_dt = _parse_event_dt(nxt.get("timestamp"))
                if cur_dt and nxt_dt:
                    delta = (nxt_dt - cur_dt).total_seconds()
                    if 0 < delta < 600:
                        gaps.append(delta)
                    elif delta >= 600:
                        excluded += 1

    if not gaps:
        return {"count": 0, "mean_seconds": 0.0, "median_seconds": 0.0, "excluded_outliers": excluded}

    return {
        "count": len(gaps),
        "mean_seconds": round(statistics.mean(gaps), 1),
        "median_seconds": round(statistics.median(gaps), 1),
        "excluded_outliers": excluded,
    }


def _compute_correction_chains(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Track whether corrections lead to success within the same session (user events only)."""
    user_events = [e for e in events if e.get("role", "user") == "user"]
    session_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in user_events:
        session_events[str(event.get("session", "unknown"))].append(event)
    for evts in session_events.values():
        evts.sort(key=lambda e: str(e.get("timestamp", "")))

    total_corrections = 0
    successful_corrections = 0
    stuck_corrections = 0
    clean_sessions = 0
    correction_sessions = 0

    for evts in session_events.values():
        session_signals: list[str] = []
        for e in evts:
            session_signals.extend(e.get("signals", []))
        has_correction = "correction" in session_signals
        has_success = "success" in session_signals

        if has_success and not has_correction:
            clean_sessions += 1
        if has_correction:
            correction_sessions += 1

        for i, e in enumerate(evts):
            if "correction" not in e.get("signals", []):
                continue
            total_corrections += 1
            found = any(
                "success" in evts[j].get("signals", [])
                and "correction" not in evts[j].get("signals", [])
                for j in range(i + 1, len(evts))
            )
            if found:
                successful_corrections += 1
            else:
                stuck_corrections += 1

    success_rate = round(successful_corrections / total_corrections * 100, 1) if total_corrections else 0.0

    return {
        "total_corrections": total_corrections,
        "successful_corrections": successful_corrections,
        "stuck_corrections": stuck_corrections,
        "correction_success_rate": success_rate,
        "clean_sessions": clean_sessions,
        "correction_sessions": correction_sessions,
    }


def _compute_recovery_time(events: list[dict[str, Any]]) -> dict[str, Any]:
    """How many user messages after a correction until a success is seen (same session)."""
    user_events = [e for e in events if e.get("role", "user") == "user"]
    session_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in user_events:
        session_events[str(event.get("session", "unknown"))].append(event)
    for evts in session_events.values():
        evts.sort(key=lambda e: str(e.get("timestamp", "")))

    distances: list[int] = []
    unrecovered = 0

    for evts in session_events.values():
        for i, e in enumerate(evts):
            if "correction" not in e.get("signals", []):
                continue
            found = False
            for j in range(i + 1, len(evts)):
                sigs = evts[j].get("signals", [])
                if "success" in sigs and "correction" not in sigs:
                    distances.append(j - i)
                    found = True
                    break
            if not found:
                unrecovered += 1

    if not distances:
        return {"count": 0, "mean_messages": 0.0, "fast": 0, "slow": 0, "unrecovered": unrecovered}

    return {
        "count": len(distances),
        "mean_messages": round(statistics.mean(distances), 1),
        "fast": sum(1 for d in distances if d <= 1),
        "slow": sum(1 for d in distances if d >= 3),
        "unrecovered": unrecovered,
    }


def _compute_initial_prompt_structure(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Score the first user event of each session for goal, constraint, verification."""
    user_events = [e for e in events if e.get("role", "user") == "user"]
    session_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in user_events:
        session_events[str(event.get("session", "unknown"))].append(event)
    for evts in session_events.values():
        evts.sort(key=lambda e: str(e.get("timestamp", "")))

    aspect_names = ("goal", "constraint", "verification")
    scores: list[int] = []
    for evts in session_events.values():
        if not evts:
            continue
        first = evts[0]
        signals = first.get("signals", [])
        total = sum(1 for a in aspect_names if a in signals)
        scores.append(total)

    sample_size = len(scores)
    if sample_size == 0:
        return {"mean_score": 0.0, "max_possible": 10, "sample_size": 0, "high_quality_count": 0, "high_quality_ratio": 0.0}

    mean_raw = statistics.mean(scores)
    high_count = sum(1 for s in scores if s >= 2)
    return {
        "mean_score": round(mean_raw * 10 / 3, 1),
        "max_possible": 10,
        "sample_size": sample_size,
        "high_quality_count": high_count,
        "high_quality_ratio": round(high_count / sample_size * 100, 1),
    }


def build_report_state(
    events: list[dict[str, Any]],
    since: datetime,
    until: datetime,
    scan_stats: dict[str, Any],
    previous: dict[str, Any] | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Split by role (backward compat: missing role = user)
    user_events = [e for e in events if e.get("role", "user") == "user"]
    prompt_count = len(user_events)
    sessions = {str(event.get("session")) for event in user_events}
    projects = {str(event.get("project")) for event in user_events}
    signal_counts = Counter(signal for event in user_events for signal in event.get("signals", []))
    kind_counts = Counter(str(event.get("kind", "unknown")) for event in user_events)
    session_corrections: dict[str, int] = defaultdict(int)
    for event in user_events:
        if "correction" in event.get("signals", []):
            session_corrections[str(event.get("session"))] += 1
    repeated_correction_sessions = sum(count >= 2 for count in session_corrections.values())

    metrics = {
        "prompts": prompt_count,
        "sessions": len(sessions),
        "projects": len(projects),
        "corrections": signal_counts["correction"],
        "success_markers": signal_counts["success"],
        "verification_mentions": signal_counts["verification"],
        "constraints_mentioned": signal_counts["constraint"],
        "goals_mentioned": signal_counts["goal"],
        "short_prompts": signal_counts["short-prompt"],
        "privacy_redactions": signal_counts["privacy-redaction"],
        "repeated_correction_sessions": repeated_correction_sessions,
    }
    rates = {
        "correction_rate": _rate(metrics["corrections"], prompt_count),
        "verification_rate": _rate(metrics["verification_mentions"], prompt_count),
        "constraint_rate": _rate(metrics["constraints_mentioned"], prompt_count),
        "goal_rate": _rate(metrics["goals_mentioned"], prompt_count),
        "short_prompt_rate": _rate(metrics["short_prompts"], prompt_count),
    }

    # ── Behaviour metrics ────────────────────────────────────────────
    behaviour: dict[str, Any] = {
        "assistant_events_per_user_turn": _compute_assistant_events_per_user_turn(events),
        "time_distribution": _compute_time_distribution(events),
        "tool_ratio": _compute_tool_ratio(events),
        "response_time": _compute_response_time(events),
        "correction_chains": _compute_correction_chains(events),
        "recovery_time": _compute_recovery_time(events),
        "initial_prompt_structure": _compute_initial_prompt_structure(events),
    }

    recommendations = _build_recommendations(events, metrics, rates)

    # ── Behavioural recommendations ─────────────────────────────────
    bc = behaviour.get("correction_chains", {})
    if bc.get("total_corrections", 0) >= 3 and bc.get("correction_success_rate", 100) < 50:
        recommendations.append({
            "title": "Подтверждать результат после исправления",
            "reason": f"Из {bc['total_corrections']} исправлений только {bc['correction_success_rate']}% привели к успеху. "
                      f"{bc['stuck_corrections']} исправлений остались без подтверждения.",
            "action": "После каждого цикла исправления явно проверять результат: тест, сборка или визуальная инспекция.",
            "evidence": _event_refs((e for e in user_events if "correction" in e.get("signals", [])), limit=3),
        })

    rcv = behaviour.get("recovery_time", {})
    if rcv.get("count", 0) >= 3 and rcv.get("mean_messages", 0) > 2:
        recommendations.append({
            "title": "Уточнять причину после исправления",
            "reason": f"В среднем требуется {rcv['mean_messages']} сообщений после коррекции, чтобы получить подтверждение. "
                      f"{rcv['unrecovered']} исправлений остались без успеха.",
            "action": "После «не так» сразу указывать, что именно не так и какой результат ожидается вместо этого.",
            "evidence": _event_refs((e for e in user_events if "correction" in e.get("signals", [])), limit=3),
        })

    ips = behaviour.get("initial_prompt_structure", {})
    if ips.get("sample_size", 0) >= 10 and ips.get("mean_score", 10) < 5:
        recommendations.append({
            "title": "Добавлять цель, ограничения и проверку в первый запрос новой задачи",
            "reason": f"Индекс структуры первого промпта: {ips['mean_score']} из {ips['max_possible']} (выборка: {ips['sample_size']} сессий). "
                      f"Только {ips['high_quality_ratio']}% первых запросов содержат минимум два элемента из трёх (цель, ограничение, проверка).",
            "action": "При начале новой задачи явно указывать: что сделать, в каких границах, как проверить. "
                      "Короткие уточнения и follow-up сообщения не требуют полной структуры.",
            "evidence": _event_refs(user_events, limit=3),
        })

    previous_metrics = (previous or {}).get("metrics", {})
    comparison = {
        key: metrics[key] - int(previous_metrics.get(key, metrics[key]))
        for key in ("prompts", "sessions", "corrections", "verification_mentions", "repeated_correction_sessions")
    } if previous else None

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "period": {"since": since.isoformat(), "until": until.isoformat()},
        "scope": scope or {"provider": "all", "source_mode": "auto", "period_duration_seconds": int((until - since).total_seconds())},
        "privacy": {
            "raw_content_included": False,
            "evidence_mode": "anonymous-metadata",
            "network_used": False,
        },
        "scan": scan_stats,
        "metrics": metrics,
        "rates": rates,
        "behaviour": behaviour,
        "event_kinds": dict(sorted(kind_counts.items())),
        "comparison": comparison,
        "recommendations": recommendations,
    }


def render_markdown(state: dict[str, Any]) -> str:
    period = state["period"]
    metrics = state["metrics"]
    rates = state["rates"]
    scope = state.get("scope", {})
    provider_label = scope.get("provider", "all")
    source_mode_label = scope.get("source_mode", "auto")

    period_duration_seconds = scope.get("period_duration_seconds", 0)
    duration_days = period_duration_seconds / 86400 if period_duration_seconds else 0

    lines = [
        "# Codex Retrospective (Codex + Claude Code)",
        "",
        f"Период: `{period['since']}` — `{period['until']}`",
        f"Источник: {provider_label} · режим: {source_mode_label} · "
        f"длительность: {duration_days:.0f} дн. ({period_duration_seconds} с)",
        "",
        "> Отчёт построен только из обезличенных метаданных. Исходные сообщения, код, пути, названия проектов и секреты не включены.",
        "",
        "## Сводка",
        "",
        "| Метрика | Значение |",
        "|---|---:|",
        f"| Запросы пользователя | {metrics['prompts']} |",
        f"| Сессии | {metrics['sessions']} |",
        f"| Обезличенные проекты | {metrics['projects']} |",
        f"| Сигналы исправления | {metrics['corrections']} ({rates['correction_rate']}%) |",
        f"| Упоминания проверки | {metrics['verification_mentions']} ({rates['verification_rate']}%) |",
        f"| Явные ограничения | {metrics['constraints_mentioned']} ({rates['constraint_rate']}%) |",
        f"| Короткие запросы | {metrics['short_prompts']} ({rates['short_prompt_rate']}%) |",
        f"| Дубликаты журналирования удалены | {state.get('scan', {}).get('duplicate_events', 0)} |",
        f"| Пустые служебные события удалены | {state.get('scan', {}).get('empty_after_sanitization', 0)} |",
        f"| Высокорисковые контекстные события исключены | {state.get('scan', {}).get('high_risk_events', 0)} |",
        f"| Внутренние продолжения исключены | {state.get('scan', {}).get('internal_user_events', 0)} |",
        "",
        "## Характер работы",
        "",
    ]
    behaviour = state.get("behaviour", {})
    aept = behaviour.get("assistant_events_per_user_turn", {})
    tool = behaviour.get("tool_ratio", {})
    time_dist = behaviour.get("time_distribution", {})
    rt = behaviour.get("response_time", {})
    chains = behaviour.get("correction_chains", {})

    aept_str = f"{aept.get('mean', 0)}"
    if aept.get("count", 0) > 0:
        aept_str += (
            f" (среднее {aept.get('mean', 0)}, медиана {aept.get('median', 0)})"
        )

    tool_str = "—"
    recognized = tool.get("recognized_format_count", 0)
    unknown = tool.get("unknown_format_count", 0)
    coverage = tool.get("coverage_rate")
    ratio = tool.get("tool_ratio")
    if tool.get("total_assistant_events", 0) > 0 and recognized > 0:
        parts = []
        if ratio is not None:
            parts.append(f"{ratio}% событий ассистента с вызовами инструментов")
        if unknown > 0 and coverage is not None:
            parts.append(f"coverage: {coverage}% распознанных форматов")
        elif coverage is not None:
            parts.append(f"coverage: {coverage}%")
        tool_str = " · ".join(parts)
    elif recognized == 0 and tool.get("total_assistant_events", 0) > 0:
        tool_str = "0% (формат не распознан)"

    peak_day_str = "—"
    day_names = {0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"}
    if time_dist.get("peak_day") is not None:
        peak_day_str = day_names.get(time_dist["peak_day"], "?")

    rt_str = "—"
    if rt.get("count", 0) > 0:
        rt_str = (
            f"{rt['mean_seconds']} с в среднем, медиана {rt['median_seconds']} с "
            f"(исключено выбросов: {rt['excluded_outliers']})"
        )

    chains_str = "—"
    if chains.get("total_corrections", 0) > 0:
        chains_str = f"{chains['correction_success_rate']}% успешных из {chains['total_corrections']}"
    if chains.get("clean_sessions", 0) > 0:
        chains_str += f" · {chains['clean_sessions']} сессий без исправлений"

    rcv = behaviour.get("recovery_time", {})
    rcv_str = "—"
    if rcv.get("count", 0) > 0:
        rcv_str = (
            f"{rcv['mean_messages']} сообщ. в среднем "
            f"(быстро {rcv['fast']}, медленно {rcv['slow']}, без успеха {rcv['unrecovered']})"
        )

    ips = behaviour.get("initial_prompt_structure", {})
    ips_str = "—"
    if ips.get("sample_size", 0) > 0:
        ips_str = (
            f"{ips['mean_score']} из {ips['max_possible']} "
            f"(выборка: {ips['sample_size']} сессий, "
            f"{ips['high_quality_ratio']}% с ≥2 элементами)"
        )

    lines.extend(
        [
            "| Метрика | Значение |",
            "|---|---:|",
            f"| События ассистента на пользовательский ход | {aept_str} |",
            f"| Всего событий ассистента | {tool.get('total_assistant_events', 0)} |",
            f"| Вызовы инструментов | {tool_str} |",
            f"| Пиковый день | {peak_day_str} |",
            f"| Пиковый час (UTC) | {str(time_dist.get('peak_hour', '—')) + ':00' if time_dist.get('peak_hour') is not None else '—'} |",
            f"| Ночные пользовательские события (UTC, 23:00–06:00) | {time_dist.get('late_night_events', 0)} |",
            f"| Пользовательские события в выходные | {time_dist.get('weekend_events', 0)} |",
            f"| Приблизительное время до следующего события ассистента | {rt_str} |",
            f"| Коррекция → успех | {chains_str} |",
            f"| Шаги до успеха после коррекции | {rcv_str} |",
            f"| Индекс структуры первого промпта (0–10) | {ips_str} |",
            "",
            "## Диагностика источников",
        ],
    )
    scan = state.get("scan", {})
    lines.extend(
        [
            "| Показатель | Значение |",
            "|---|---:|",
            f"| Файлы | {scan.get('files', 0)} |",
            f"| JSONL-записи | {scan.get('lines', 0)} |",
            f"| Вне периода | {scan.get('outside_period', 0)} |",
            f"| Без временной метки | {scan.get('undated', 0)} |",
            f"| Непользовательские события | {scan.get('skipped_non_user', 0)} |",
            f"| Некорректный JSON | {scan.get('invalid_json', 0)} |",
            "",
            "Типы принятых пользовательских событий:",
            "",
        ]
    )
    kinds = state.get("event_kinds", {})
    if kinds:
        lines.extend(["| Тип | Количество |", "|---|---:|"])
        for kind, count in kinds.items():
            lines.append(f"| `{kind}` | {count} |")
    else:
        lines.append("Нет принятых событий.")
    lines.extend(["", "## Сравнение с предыдущим отчётом", ""])
    comparison = state.get("comparison")
    if comparison is None:
        if state.get("scope", {}).get("source_mode") == "explicit":
            lines.append("Сравнение отключено для явно заданных источников.")
        else:
            lines.append("Предыдущий совместимый отчёт не найден.")
    else:
        labels = {
            "prompts": "Запросы",
            "sessions": "Сессии",
            "corrections": "Исправления",
            "verification_mentions": "Упоминания проверки",
            "repeated_correction_sessions": "Сессии с повторными исправлениями",
        }
        lines.extend(["| Метрика | Изменение |", "|---|---:|"])
        for key, value in comparison.items():
            lines.append(f"| {labels[key]} | {value:+d} |")

    lines.extend(["", "## Рекомендации", ""])
    recommendations = state.get("recommendations", [])
    if not recommendations:
        lines.append("Недостаточно сигналов для обоснованной рекомендации.")
    for index, recommendation in enumerate(recommendations, start=1):
        lines.extend(
            [
                f"### {index}. {recommendation['title']}",
                "",
                recommendation["reason"],
                "",
                f"Действие: {recommendation['action']}",
                "",
                "Основание:",
                "",
            ]
        )
        evidence = recommendation.get("evidence", [])
        if not evidence:
            lines.append("- Агрегированная метрика периода; безопасный пример отсутствует.")
        for item in evidence:
            lines.append(
                f"- `{item['event_id']}` · `{item['timestamp']}` · `{item['session']}` — {item['summary']}"
            )
        lines.append("")
    lines.extend(
        [
            "## Ограничения",
            "",
            "Сигналы являются эвристиками и не доказывают причину ошибки. Используйте рекомендации как гипотезы для следующей недели.",
            "",
            "### Поведенческие метрики",
            "",
            "- **События ассистента на пользовательский ход**: одно событие ассистента может не совпадать с одним логическим ответом — "
            "некоторые ответы состоят из нескольких событий (текст, вызов инструмента, результат).",
            "- **Приблизительное время до следующего события ассистента**: фактическое время зависит от модели, исполнения инструментов, "
            "тестов и окружения. Метрика показывает только временной промежуток между записью событий в журнал.",
            "- **Индекс структуры первого промпта**: оценивает только первое сообщение каждой сессии. "
            "Короткие уточнения и follow-up не влияют на оценку.",
            "",
        ]
    )
    return "\n".join(lines)


def load_previous(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # Accept schema_version 1 through 4 (read-only; v4 only compares with v4)
    ver = value.get("schema_version") if isinstance(value, dict) else None
    return value if isinstance(value, dict) and ver in (1, 2, 3, 4) else None


def find_compatible_previous(output_dir: Path, current_scope: dict[str, Any]) -> dict[str, Any] | None:
    """Find the most recent previous JSON report with a compatible scope.

    Compatible means: same *schema_version*, same *provider*, same *source_mode*,
    same *period_duration_seconds* — reports with mismatched scope or schema
    version are not comparable.
    """
    candidates = sorted(output_dir.glob("retrospective-*.json"), reverse=True)
    for candidate in candidates:
        previous = load_previous(candidate)
        if previous is None:
            continue
        if previous.get("schema_version") != REPORT_SCHEMA_VERSION:
            continue
        prev_scope = previous.get("scope", {})
        if all(prev_scope.get(k) == current_scope.get(k) for k in SCOPE_KEYS):
            return previous
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--since", type=datetime.fromisoformat, required=True)
    parser.add_argument("--until", type=datetime.fromisoformat, required=True)
    parser.add_argument("--scan-stats", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    stats = json.loads(args.scan_stats.read_text(encoding="utf-8"))
    state = build_report_state(load_events(args.events), args.since, args.until, stats, load_previous(args.previous))
    args.markdown.write_text(render_markdown(state), encoding="utf-8", newline="\n")
    args.json.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
