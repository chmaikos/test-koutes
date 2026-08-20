#!/usr/bin/env python3
"""Report pallet adoption and integrity without changing the database."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from typing import Any

from sqlalchemy import create_engine, text

from app.config import get_settings


def analyze_rows(
    box_rows: list[Mapping[str, Any]],
    pallet_rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    pallets = {int(row["id"]): row for row in pallet_rows}
    unassigned_box_ids: list[int] = []
    missing_pallet_references: list[dict[str, int]] = []
    lot_mismatches: list[dict[str, int]] = []
    inactive_pallet_assignments: list[dict[str, int]] = []
    warehouse_counts: dict[int, dict[int, int]] = {
        pallet_id: {} for pallet_id in pallets
    }

    for box in box_rows:
        box_id = int(box["id"])
        pallet_id = box["pallet_id"]
        if pallet_id is None:
            if (
                box.get("archived_at") is None
                and box.get("status") != "returned"
            ):
                unassigned_box_ids.append(box_id)
            continue
        pallet = pallets.get(int(pallet_id))
        if pallet is None:
            missing_pallet_references.append(
                {"box_id": box_id, "pallet_id": int(pallet_id)}
            )
            continue
        if int(box["lot_id"]) != int(pallet["lot_id"]):
            lot_mismatches.append(
                {
                    "box_id": box_id,
                    "box_lot_id": int(box["lot_id"]),
                    "pallet_id": int(pallet_id),
                    "pallet_lot_id": int(pallet["lot_id"]),
                }
            )
        warehouse_id = int(box["current_warehouse_id"])
        pallet_warehouses = warehouse_counts[int(pallet_id)]
        pallet_warehouses[warehouse_id] = pallet_warehouses.get(warehouse_id, 0) + 1
        if not bool(pallet["is_active"]):
            inactive_pallet_assignments.append(
                {"box_id": box_id, "pallet_id": int(pallet_id)}
            )

    duplicate_identities: list[dict[str, Any]] = []
    grouped: dict[tuple[int, str], list[int]] = {}
    invalid_parent_pallet_ids: list[int] = []
    for pallet in pallet_rows:
        key = (int(pallet["lot_id"]), str(pallet["normalized_pallet_number"]))
        grouped.setdefault(key, []).append(int(pallet["id"]))
        if pallet["merged_into_lot_id"] is not None:
            invalid_parent_pallet_ids.append(int(pallet["id"]))
    for (lot_id, normalized), pallet_ids in grouped.items():
        if len(pallet_ids) > 1:
            duplicate_identities.append(
                {
                    "lot_id": lot_id,
                    "normalized_pallet_number": normalized,
                    "pallet_ids": sorted(pallet_ids),
                }
            )

    conflicts = {
        "missing_pallet_references": missing_pallet_references,
        "lot_mismatches": lot_mismatches,
        "inactive_pallet_assignments": inactive_pallet_assignments,
        "duplicate_identities": duplicate_identities,
        "invalid_parent_pallet_ids": sorted(invalid_parent_pallet_ids),
    }
    conflict_count = sum(len(values) for values in conflicts.values())
    informational = {
        "unassigned_active_boxes": {
            "count": len(unassigned_box_ids),
            "box_ids": sorted(unassigned_box_ids),
        }
    }
    return {
        "safe": conflict_count == 0,
        "conflict_count": conflict_count,
        "informational_count": len(unassigned_box_ids),
        "informational": informational,
        "box_count": len(box_rows),
        "pallet_count": len(pallet_rows),
        # Backwards-compatible fields retained for existing script consumers.
        "unassigned_box_count": len(unassigned_box_ids),
        "unassigned_box_ids": sorted(unassigned_box_ids),
        "warehouse_distribution": [
            {
                "pallet_id": pallet_id,
                "warehouses": [
                    {"warehouse_id": warehouse_id, "box_count": count}
                    for warehouse_id, count in sorted(warehouse_counts[pallet_id].items())
                ],
            }
            for pallet_id in sorted(warehouse_counts)
        ],
        "conflicts": conflicts,
    }


def _render_text(report: Mapping[str, Any]) -> str:
    lines = [
        f"Boxes: {report['box_count']}",
        f"Pallets: {report['pallet_count']}",
        f"INFO unassigned active boxes: {report['unassigned_box_count']} "
        f"{report['unassigned_box_ids']}",
        f"Pallet warehouse distribution: {report['warehouse_distribution']}",
    ]
    for name, values in report["conflicts"].items():
        if values:
            lines.append(f"CONFLICT {name}: {values}")
    lines.append(
        "Result: no impossible pallet references found"
        if report["safe"]
        else "Result: pallet integrity conflicts require reconciliation"
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
                    text(
                        """
                        SELECT
                            id,
                            lot_id,
                            current_warehouse_id,
                            pallet_id,
                            status,
                            archived_at
                        FROM boxes
                        ORDER BY id
                        """
                    )
                ).mappings()
            )
            pallet_rows = list(
                connection.execute(
                    text(
                        """
                        SELECT
                            p.id,
                            p.lot_id,
                            p.normalized_pallet_number,
                            p.is_active,
                            l.merged_into_lot_id
                        FROM pallets p
                        JOIN lots l ON l.id = p.lot_id
                        ORDER BY p.id
                        """
                    )
                ).mappings()
            )
            report = analyze_rows(box_rows, pallet_rows)
            connection.rollback()
    finally:
        engine.dispose()

    print(json.dumps(report, indent=2) if args.json else _render_text(report))
    return 0 if report["safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
