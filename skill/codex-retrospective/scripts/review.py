#!/usr/bin/env python3
"""Run an explicit-period, local-only retrospective of Codex / Claude Code sessions."""

from __future__ import annotations

import argparse
import json
import secrets
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from analyze_events import build_report_state, find_compatible_previous, load_events, render_markdown
from sanitize_history import PROVIDER_CHOICES, count_source_files, discover_sources, expand_sources, scan_sources

TOOL_VERSION = "0.1.0-beta.1"


def _date_start(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD or an ISO timestamp") from exc
    if parsed.tzinfo is None:
        if "T" not in value:
            parsed = datetime.combine(parsed.date(), time.min)
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _period(args: argparse.Namespace) -> tuple[datetime, datetime]:
    if args.days is not None:
        until = datetime.now(timezone.utc)
        return until - timedelta(days=args.days), until
    since = args.since
    until = args.until or datetime.now(timezone.utc)
    return since, until


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    period = parser.add_mutually_exclusive_group(required=True)
    period.add_argument("--days", type=int)
    period.add_argument("--since", type=_date_start)
    parser.add_argument("--until", type=_date_start)
    parser.add_argument("--provider", choices=PROVIDER_CHOICES, help="Auto-discover sources (mutually exclusive with --source)")
    parser.add_argument("--source", action="append", type=Path, help="Explicit JSONL file(s) (mutually exclusive with --provider)")
    parser.add_argument("--output-dir", type=Path, default=Path.home() / ".codex-retrospective" / "reports")
    parser.add_argument("--dry-run", action="store_true", help="Discover files without reading their contents")
    args = parser.parse_args()

    if args.days is not None and not 1 <= args.days <= 366:
        parser.error("--days must be between 1 and 366")
    since, until = _period(args)
    if until <= since:
        parser.error("The end of the period must be later than the start")

    # --provider and --source are mutually exclusive
    if args.provider and args.source:
        parser.error(
            "--provider and --source are mutually exclusive. "
            "Use --source for explicit file paths or --provider for auto-discovery."
        )

    if args.source:
        sources = expand_sources(args.source)
        provider = "custom"
        source_mode = "explicit"
    elif args.provider:
        sources = discover_sources(provider=args.provider)
        source_mode = "auto"
        provider = args.provider
    else:
        parser.error("--provider (codex|claude|all) is required when --source is not given")

    if not sources:
        parser.error("No Codex/Claude Code JSONL history files were found")

    scope_dict = {
        "provider": provider,
        "source_mode": source_mode,
        "period_duration_seconds": int((until - since).total_seconds()),
    }

    if args.dry_run:
        print(f"provider: {provider}")
        print(f"source_mode: {source_mode}")
        if provider == "custom":
            print(f"Custom files: {len(sources)}")
        else:
            counts = count_source_files(provider)
            for label, count in counts.items():
                print(f"{label} files: {count}")
            if len(counts) > 1:
                print(f"Total files: {sum(counts.values())}")
        print(f"period: {since.isoformat()} .. {until.isoformat()}")
        print(f"period_duration_seconds: {scope_dict['period_duration_seconds']}")
        print("contents read: no")
        print("network used: no")
        return 0

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    markdown_path = output_dir / f"retrospective-{stamp}.md"
    json_path = output_dir / f"retrospective-{stamp}.json"

    # Custom / explicit sources never auto-compare
    if provider == "custom":
        previous = None
    else:
        previous = find_compatible_previous(output_dir, scope_dict)

    with tempfile.TemporaryDirectory(prefix="codex-retrospective-") as temporary:
        events_path = Path(temporary) / "events.jsonl"
        salt = secrets.token_bytes(32)
        with events_path.open("w", encoding="utf-8", newline="\n") as output:
            stats = scan_sources(sources, since, until, output, salt)
        state = build_report_state(load_events(events_path), since, until, stats.public_dict(), previous, scope=scope_dict)

    markdown_path.write_text(render_markdown(state), encoding="utf-8", newline="\n")
    json_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(f"report: {markdown_path}")
    print(f"state: {json_path}")
    print(f"sanitized events: {state['metrics']['prompts']}")
    print("raw content persisted: no")
    print("network used: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
