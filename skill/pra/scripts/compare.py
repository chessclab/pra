"""Compare two aggregate PRA reports as a baseline/intervention/follow-up experiment."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RATE_METRICS = {
    "verification_attempt_rate": ("verification_attempted", "prompts"),
    "verification_pass_rate": ("verification_passed", "verification_attempted"),
    "correction_rate": ("corrections", "prompts"),
    "incomplete_turn_rate": ("incomplete_logical_turns", "logical_turns"),
    "handoff_resume_rate": ("resumed_after_handoff", "sessions"),
}


def load_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), dict):
        raise ValueError(f"Not a PRA report: {path}")
    privacy = data.get("privacy", {})
    if privacy.get("raw_content_included") is True:
        raise ValueError(f"Refusing report with raw content: {path}")
    return data


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _snapshot(report: dict[str, Any]) -> dict[str, float]:
    metrics = report.get("metrics", {})
    behaviour = report.get("behaviour", {})
    verification = behaviour.get("verification_actions", {})
    logical = behaviour.get("logical_turns", {})
    context = behaviour.get("context_artifacts", {})
    return {
        "prompts": _number(metrics.get("prompts")),
        "sessions": _number(metrics.get("sessions")),
        "corrections": _number(metrics.get("corrections")),
        "verification_attempted": _number(verification.get("attempted")),
        "verification_passed": _number(verification.get("passed")),
        "logical_turns": _number(logical.get("count")),
        "incomplete_logical_turns": _number(logical.get("incomplete_count")),
        "resumed_after_handoff": _number(context.get("sessions_resumed_after_handoff")),
    }


def _rate(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator * 100, 1) if denominator else None


def build_comparison(baseline: dict[str, Any], follow_up: dict[str, Any], intervention: str = "") -> dict[str, Any]:
    before = _snapshot(baseline)
    after = _snapshot(follow_up)
    absolute = {key: round(after[key] - before[key], 1) for key in before}
    rates: dict[str, dict[str, float | None]] = {}
    for name, (numerator, denominator) in RATE_METRICS.items():
        before_rate = _rate(before[numerator], before[denominator])
        after_rate = _rate(after[numerator], after[denominator])
        rates[name] = {
            "baseline_percent": before_rate,
            "follow_up_percent": after_rate,
            "delta_percentage_points": round(after_rate - before_rate, 1) if before_rate is not None and after_rate is not None else None,
        }
    return {
        "schema_version": 1,
        "comparison_type": "baseline-intervention-follow-up",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "privacy": {"raw_content_included": False, "network_used": False, "input_type": "aggregate-reports-only"},
        "intervention": intervention,
        "baseline": {"path_label": baseline.get("scope", {}).get("provider", "unknown"), "period": baseline.get("period"), "sample": {"sessions": before["sessions"], "prompts": before["prompts"]}},
        "follow_up": {"path_label": follow_up.get("scope", {}).get("provider", "unknown"), "period": follow_up.get("period"), "sample": {"sessions": after["sessions"], "prompts": after["prompts"]}},
        "absolute_delta": absolute,
        "rates": rates,
        "interpretation": "Observed association only; this comparison does not prove that the intervention caused the change.",
    }


def render_markdown(comparison: dict[str, Any]) -> str:
    b = comparison["baseline"]
    f = comparison["follow_up"]
    lines = [
        "# PRA — Baseline → Intervention → Follow-up",
        "",
        "> Сравнение построено только по агрегированным отчётам PRA. Исходный текст, команды, пути и содержимое файлов не используются.",
        "",
        f"**Intervention:** {comparison.get('intervention') or 'не указано'}",
        "",
        "## Выборка",
        "",
        "| Период | Сессии | Запросы | Интервал |",
        "|---|---:|---:|---|",
        f"| Baseline | {b['sample']['sessions']:.0f} | {b['sample']['prompts']:.0f} | {b.get('period', {}).get('since', '—')} → {b.get('period', {}).get('until', '—')} |",
        f"| Follow-up | {f['sample']['sessions']:.0f} | {f['sample']['prompts']:.0f} | {f.get('period', {}).get('since', '—')} → {f.get('period', {}).get('until', '—')} |",
        "",
        "## Изменения",
        "",
        "| Метрика | Baseline | Follow-up | Δ п.п. |",
        "|---|---:|---:|---:|",
    ]
    labels = {
        "verification_attempt_rate": "Проверки на запрос",
        "verification_pass_rate": "Успешные проверки",
        "correction_rate": "Сигналы исправления",
        "incomplete_turn_rate": "Незавершённые logical turns",
        "handoff_resume_rate": "Resume после handoff",
    }
    for key, label in labels.items():
        item = comparison["rates"][key]
        values = ["—" if item[name] is None else f"{item[name]:.1f}%" for name in ("baseline_percent", "follow_up_percent")]
        delta = "—" if item["delta_percentage_points"] is None else f"{item['delta_percentage_points']:+.1f}"
        lines.append(f"| {label} | {values[0]} | {values[1]} | {delta} |")
    lines.extend(["", "## Интерпретация", "", comparison["interpretation"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--follow-up", required=True, type=Path)
    parser.add_argument("--intervention", default="")
    parser.add_argument("--output-dir", type=Path, default=Path.home() / ".pra" / "comparisons")
    args = parser.parse_args()
    try:
        comparison = build_comparison(load_report(args.baseline), load_report(args.follow_up), args.intervention)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    markdown = output_dir / f"comparison-{stamp}.md"
    state = output_dir / f"comparison-{stamp}.json"
    markdown.write_text(render_markdown(comparison), encoding="utf-8", newline="\n")
    state.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(f"comparison: {markdown}")
    print(f"state: {state}")
    print("raw content persisted: no")
    print("network used: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
