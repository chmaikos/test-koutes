#!/usr/bin/env python3
"""Read-only preflight for migration 0027's legacy box lot data."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import create_engine, text

from app.models.lots import clean_lot_name, normalize_lot_name


def analyze_box_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    blank_box_ids: list[int] = []
    identities: dict[tuple[str, str], list[int]] = defaultdict(list)

    for row in rows:
        raw_name = row["lot"]
        if raw_name is None:
            blank_box_ids.append(int(row["id"]))
            continue
        raw_name = str(raw_name)
        try:
            display_name = clean_lot_name(raw_name)
            normalized_name = normalize_lot_name(raw_name)
        except ValueError:
            blank_box_ids.append(int(row["id"]))
            continue

        group = groups.setdefault(
            normalized_name,
            {
                "normalized_name": normalized_name,
                "display_spellings": set(),
                "box_count": 0,
                "archived_box_count": 0,
            },
        )
        group["display_spellings"].add(display_name)
        group["box_count"] += 1
        if row.get("archived_at") is not None:
            group["archived_box_count"] += 1
        identities[(normalized_name, str(row["box_number"]))].append(int(row["id"]))

    blocking_duplicates = [
        {
            "normalized_name": normalized_name,
            "box_number": box_number,
            "box_ids": sorted(box_ids),
        }
        for (normalized_name, box_number), box_ids in identities.items()
        if len(box_ids) > 1
    ]
    blocking_duplicates.sort(key=lambda item: (item["normalized_name"], item["box_number"]))

    normalized_groups = []
    for normalized_name in sorted(groups):
        group = groups[normalized_name]
        normalized_groups.append(
            {
                **group,
                "display_spellings": sorted(group["display_spellings"]),
            }
        )

    return {
        "safe_to_migrate": not blank_box_ids and not blocking_duplicates,
        "normalized_groups": normalized_groups,
        "blank_lot_box_ids": sorted(blank_box_ids),
        "blocking_duplicates": blocking_duplicates,
    }


def _render_text(report: Mapping[str, Any]) -> str:
    lines = ["Normalized lot groups:"]
    for group in report["normalized_groups"]:
        spellings = ", ".join(repr(name) for name in group["display_spellings"])
        lines.append(
            f"  {group['normalized_name']!r}: {group['box_count']} boxes "
            f"({group['archived_box_count']} archived); spellings={spellings}"
        )

    if report["blank_lot_box_ids"]:
        lines.append(f"BLOCKER blank lot box ids: {report['blank_lot_box_ids']}")
    for duplicate in report["blocking_duplicates"]:
        lines.append(
            "BLOCKER duplicate identity "
            f"{duplicate['normalized_name']!r} / "
            f"box_number={duplicate['box_number']!r}: "
            f"box ids={duplicate['box_ids']}"
        )
    lines.append(
        "Result: SAFE to run migration 0027"
        if report["safe_to_migrate"]
        else "Result: BLOCKED; reconcile the rows above before migration 0027"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="SQLAlchemy database URL (defaults to DATABASE_URL)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    engine = create_engine(args.database_url, future=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, lot, box_number, archived_at
                    FROM boxes
                    ORDER BY id
                    """
                )
            ).mappings()
            report = analyze_box_rows(rows)
            # The preflight must never persist session-level or accidental writes.
            connection.rollback()
    finally:
        engine.dispose()

    print(json.dumps(report, indent=2) if args.json else _render_text(report))
    return 0 if report["safe_to_migrate"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
