#!/usr/bin/env python3
"""Report aggregate JSONL shapes without emitting field values or message text."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sanitize_history import (
    PROVIDER_CHOICES,
    classify,
    discover_sources,
    find_timestamp,
    load_json_safely,
    sanitize_for_metrics,
    user_text_parts,
)


def _bucket(length: int) -> str:
    if length == 0:
        return "empty"
    if length < 40:
        return "short"
    if length < 200:
        return "medium"
    if length < 800:
        return "long"
    return "very-long"


def shape_of(obj: dict[str, Any]) -> tuple[str, ...]:
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    role, kind = classify(obj)
    parts = user_text_parts(obj) if role == "user" else []
    raw_text = "\n".join(parts)
    cleaned, redactions = sanitize_for_metrics(raw_text)
    content = payload.get("content")
    content_types: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                content_types.append(str(item_type) if item_type is not None else "missing")
            else:
                content_types.append(type(item).__name__)
    return (
        f"record={obj.get('type', 'missing')}",
        f"kind={kind}",
        f"role={role or 'missing'}",
        "top-keys=" + ",".join(sorted(str(key) for key in obj.keys())),
        "payload-keys=" + ",".join(sorted(str(key) for key in payload.keys())),
        "content-types=" + ",".join(content_types),
        f"parts={len(parts)}",
        f"raw={_bucket(len(raw_text))}",
        f"clean={_bucket(len(' '.join(cleaned.split())))}",
        f"redactions={'10+' if redactions >= 10 else redactions}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--provider", choices=PROVIDER_CHOICES, required=True)
    args = parser.parse_args()
    if not 1 <= args.days <= 366:
        parser.error("--days must be between 1 and 366")
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.days)
    shapes: Counter[tuple[str, ...]] = Counter()
    files = 0
    lines = 0
    for source in discover_sources(provider=args.provider):
        files += 1
        with Path(source).open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines += 1
                try:
                    obj, _ = load_json_safely(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(obj, dict):
                    continue
                timestamp = find_timestamp(obj)
                if timestamp is None or not since <= timestamp < until:
                    continue
                role, _ = classify(obj)
                if role == "user":
                    shapes[shape_of(obj)] += 1
    print(f"period-days: {args.days}")
    print(f"files: {files}")
    print(f"lines: {lines}")
    print(f"user-shapes: {len(shapes)}")
    for index, (shape, count) in enumerate(shapes.most_common(), start=1):
        print(f"\nshape-{index}: count={count}")
        for field in shape:
            print(f"  {field}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
