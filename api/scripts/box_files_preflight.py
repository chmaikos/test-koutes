#!/usr/bin/env python3
"""Report first-class box-file adoption and integrity without changing data."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.box_files import file_integrity_report


def _split_contents(contents: object) -> list[str]:
    if not isinstance(contents, str):
        return []
    return [segment.strip() for segment in contents.split("|") if segment.strip()]


def _normalized(value: object) -> str:
    return " ".join(str(value).split()).lower()


def analyze_rows(
    box_rows: list[Mapping[str, Any]],
    file_rows: list[Mapping[str, Any]],
    request_item_rows: list[Mapping[str, Any]],
    snapshot_rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    boxes = {int(row["id"]): row for row in box_rows}
    request_items = {int(row["id"]): row for row in request_item_rows}

    blank_file_ids: list[int] = []
    noncanonical_file_ids: list[int] = []
    cross_lot_placements: list[dict[str, int]] = []
    missing_box_file_ids: list[int] = []
    grouped_files: dict[tuple[int, str], list[int]] = {}
    for file_row in file_rows:
        file_id = int(file_row["id"])
        reference = file_row["reference"]
        normalized_reference = file_row["normalized_reference"]
        if not str(reference or "").strip() or not str(normalized_reference or "").strip():
            blank_file_ids.append(file_id)
        elif _normalized(reference) != str(normalized_reference):
            noncanonical_file_ids.append(file_id)
        grouped_files.setdefault(
            (int(file_row["lot_id"]), str(normalized_reference)),
            [],
        ).append(file_id)

        box_id = int(file_row["box_id"])
        box = boxes.get(box_id)
        if box is None:
            missing_box_file_ids.append(file_id)
        elif int(box["lot_id"]) != int(file_row["lot_id"]):
            cross_lot_placements.append(
                {
                    "file_id": file_id,
                    "file_lot_id": int(file_row["lot_id"]),
                    "box_id": box_id,
                    "box_lot_id": int(box["lot_id"]),
                }
            )

    duplicate_references = [
        {
            "lot_id": lot_id,
            "normalized_reference": normalized_reference,
            "file_ids": sorted(file_ids),
        }
        for (lot_id, normalized_reference), file_ids in sorted(grouped_files.items())
        if len(file_ids) > 1
    ]

    snapshots_by_item: dict[int, list[Mapping[str, Any]]] = {}
    blank_snapshot_ids: list[int] = []
    orphan_snapshot_ids: list[int] = []
    for snapshot in snapshot_rows:
        snapshot_id = int(snapshot["id"])
        if not str(snapshot["reference"] or "").strip():
            blank_snapshot_ids.append(snapshot_id)
        item_id = int(snapshot["request_item_id"])
        if item_id not in request_items:
            orphan_snapshot_ids.append(snapshot_id)
        snapshots_by_item.setdefault(item_id, []).append(snapshot)

    request_snapshot_coverage: list[dict[str, Any]] = []
    expected_legacy_snapshot_count = 0
    for item_id, item in request_items.items():
        expected = _split_contents(item["contents"])
        expected_legacy_snapshot_count += len(expected)
        actual_rows = sorted(
            (
                row
                for row in snapshots_by_item.get(item_id, [])
                if row["snapshot_kind"] == "legacy_contents"
            ),
            key=lambda row: (int(row["position"]), int(row["id"])),
        )
        actual = [row["description"] for row in actual_rows]
        if actual != expected:
            request_snapshot_coverage.append(
                {
                    "request_item_id": item_id,
                    "expected_descriptions": expected,
                    "actual_descriptions": actual,
                }
            )

    conflicts = {
        "duplicate_references": duplicate_references,
        "cross_lot_placements": cross_lot_placements,
        "missing_box_file_references": sorted(missing_box_file_ids),
        "blank_file_references": sorted(blank_file_ids),
        "noncanonical_normalized_references": sorted(noncanonical_file_ids),
        "blank_snapshot_references": sorted(blank_snapshot_ids),
        "orphan_request_snapshots": sorted(orphan_snapshot_ids),
        "request_snapshot_coverage": request_snapshot_coverage,
    }
    conflict_count = sum(len(values) for values in conflicts.values())
    return {
        "safe": conflict_count == 0,
        "conflict_count": conflict_count,
        "counts": {
            "boxes": len(box_rows),
            "box_files": len(file_rows),
            "legacy_box_segments": sum(len(_split_contents(row["contents"])) for row in box_rows),
            "request_items": len(request_item_rows),
            "request_file_snapshots": len(snapshot_rows),
            "expected_legacy_request_snapshots": expected_legacy_snapshot_count,
        },
        "conflicts": conflicts,
    }


def _render_text(report: Mapping[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        f"Boxes: {counts['boxes']}",
        f"Box files: {counts['box_files']}",
        f"Legacy box segments: {counts['legacy_box_segments']}",
        f"Request items: {counts['request_items']}",
        f"Request file snapshots: {counts['request_file_snapshots']}",
        f"Expected legacy request snapshots: {counts['expected_legacy_request_snapshots']}",
    ]
    for name, values in report["conflicts"].items():
        if values:
            lines.append(f"CONFLICT {name}: {values}")
    operational = report.get("operational_integrity")
    if isinstance(operational, Mapping) and not operational.get("safe", False):
        lines.append(f"CONFLICT operational_integrity: {operational}")
    lines.append(
        "Result: no box-file integrity conflicts found"
        if report["safe"]
        else "Result: box-file integrity conflicts require reconciliation"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL") or get_settings().database_url,
        help="SQLAlchemy database URL (defaults to the application's configured DB)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    engine = create_engine(args.database_url, future=True)
    try:
        with engine.connect() as connection:
            box_rows = list(
                connection.execute(
                    text("SELECT id, lot_id, contents FROM boxes ORDER BY id")
                ).mappings()
            )
            file_rows = list(
                connection.execute(
                    text(
                        """
                        SELECT
                            id,
                            lot_id,
                            box_id,
                            reference,
                            normalized_reference
                        FROM box_files
                        ORDER BY id
                        """
                    )
                ).mappings()
            )
            request_item_rows = list(
                connection.execute(
                    text("SELECT id, contents FROM box_request_items ORDER BY id")
                ).mappings()
            )
            snapshot_rows = list(
                connection.execute(
                    text(
                        """
                        SELECT
                            id,
                            request_item_id,
                            reference,
                            description,
                            position,
                            snapshot_kind
                        FROM box_request_item_file_snapshots
                        ORDER BY id
                        """
                    )
                ).mappings()
            )
            report = analyze_rows(
                box_rows,
                file_rows,
                request_item_rows,
                snapshot_rows,
            )
            with Session(bind=connection) as integrity_session:
                operational = file_integrity_report(integrity_session)
            report["operational_integrity"] = operational
            report["safe"] = bool(report["safe"] and operational["safe"])
            report["conflict_count"] = int(report["conflict_count"]) + int(
                operational["conflict_count"]
            )
            connection.rollback()
    finally:
        engine.dispose()

    print(json.dumps(report, indent=2) if args.json else _render_text(report))
    return 0 if report["safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
