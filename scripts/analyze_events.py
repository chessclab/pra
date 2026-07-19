#!/usr/bin/env python3
"""Build a local Markdown retrospective from sanitized event metadata."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

REPORT_SCHEMA_VERSION = 3
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
    refs: list[dict[str, str]] = []
    for event in events:
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


def build_report_state(
    events: list[dict[str, Any]],
    since: datetime,
    until: datetime,
    scan_stats: dict[str, Any],
    previous: dict[str, Any] | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt_count = len(events)
    sessions = {str(event.get("session")) for event in events}
    projects = {str(event.get("project")) for event in events}
    signal_counts = Counter(signal for event in events for signal in event.get("signals", []))
    kind_counts = Counter(str(event.get("kind", "unknown")) for event in events)
    session_corrections: dict[str, int] = defaultdict(int)
    for event in events:
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

    recommendations: list[dict[str, Any]] = []
    if prompt_count >= 4 and rates["verification_rate"] < 25:
        recommendations.append(
            {
                "title": "Явно задавать критерий проверки",
                "reason": f"Проверка или тесты упомянуты только в {rates['verification_rate']}% запросов.",
                "action": "Для задач с изменениями добавлять ожидаемую проверку: тест, lint, build или конкретный сценарий.",
                "evidence": _event_refs(events, limit=3),
            }
        )
    if prompt_count >= 4 and rates["constraint_rate"] < 30:
        recommendations.append(
            {
                "title": "Фиксировать границы задачи в первом запросе",
                "reason": f"Явные ограничения обнаружены в {rates['constraint_rate']}% запросов.",
                "action": "Добавлять, что разрешено менять, что нельзя трогать и какой результат считается завершённым.",
                "evidence": _event_refs(events, limit=3),
            }
        )
    if metrics["repeated_correction_sessions"] >= 1:
        recommendations.append(
            {
                "title": "Проверять понимание перед широкими изменениями",
                "reason": f"Обнаружено {metrics['corrections']} сигналов исправления в {metrics['repeated_correction_sessions']} повторяющихся сессиях.",
                "action": "Для неоднозначных задач сначала кратко зафиксировать план и область изменений.",
                "evidence": _event_refs(events, "correction"),
            }
        )
    if prompt_count >= 4 and rates["short_prompt_rate"] > 45:
        recommendations.append(
            {
                "title": "Добавлять минимальный контекст к коротким командам",
                "reason": f"Короткие запросы составляют {rates['short_prompt_rate']}% периода.",
                "action": "Указывать цель, затрагиваемый компонент и ожидаемую проверку хотя бы одной строкой.",
                "evidence": _event_refs(events, "short-prompt"),
            }
        )

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
        "## Диагностика источников",
        "",
    ]
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
    # Accept schema_version 1, 2, or 3
    ver = value.get("schema_version") if isinstance(value, dict) else None
    return value if isinstance(value, dict) and ver in (1, 2, 3) else None


def find_compatible_previous(output_dir: Path, current_scope: dict[str, Any]) -> dict[str, Any] | None:
    """Find the most recent previous JSON report with a compatible scope.

    Compatible means: same *provider*, same *source_mode*, same
    *period_duration_seconds*, and matching *schema_version* — reports with
    mismatched scope or schema version are not comparable.
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
