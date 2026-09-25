#!/usr/bin/env python3
"""Read-only integrity checks for the permanent barcode registry."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import create_engine, inspect, text

from app.config import get_settings
from app.models.barcode_identities import BarcodeEntityKind
from app.services.barcodes import parse_barcode

ENTITY_TABLES = {
    BarcodeEntityKind.lot: "lots",
    BarcodeEntityKind.pallet: "pallets",
    BarcodeEntityKind.box: "boxes",
    BarcodeEntityKind.file: "box_files",
}


def analyze_rows(
    identities: Sequence[Mapping[str, Any]],
    entity_rows: Mapping[BarcodeEntityKind, Sequence[Mapping[str, Any]]],
    *,
    mutable_legacy_barcode_column: bool = False,
) -> dict[str, Any]:
    """Analyze already-read rows without performing database writes."""
    conflicts: dict[str, list[Any]] = {
        "missing_identity_coverage": [],
        "duplicate_barcodes": [],
        "duplicate_issuance_numbers": [],
        "cross_type_collisions": [],
        "malformed_barcodes": [],
        "broken_identity_links": [],
        "unexpected_mutable_legacy_inputs": [],
        "deleted_objects_with_unretired_identity": [],
    }
    by_id = {int(row["id"]): row for row in identities}
    barcode_counts = Counter(str(row["barcode"]) for row in identities)
    number_counts = Counter(int(row["issuance_number"]) for row in identities)
    conflicts["duplicate_barcodes"] = sorted(
        value for value, count in barcode_counts.items() if count > 1
    )
    conflicts["duplicate_issuance_numbers"] = sorted(
        value for value, count in number_counts.items() if count > 1
    )

    number_kinds: dict[int, set[str]] = {}
    for row in identities:
        identity_id = int(row["id"])
        kind_text = str(row["entity_kind"])
        number = int(row["issuance_number"])
        number_kinds.setdefault(number, set()).add(kind_text)
        try:
            parsed_kind, parsed_number = parse_barcode(str(row["barcode"]))
        except ValueError as exc:
            conflicts["malformed_barcodes"].append(
                {"identity_id": identity_id, "reason": str(exc)}
            )
        else:
            if parsed_kind.value != kind_text or parsed_number != number:
                conflicts["malformed_barcodes"].append(
                    {
                        "identity_id": identity_id,
                        "reason": "prefix or embedded sequence does not match registry fields",
                    }
                )
    conflicts["cross_type_collisions"] = [
        {"issuance_number": number, "entity_kinds": sorted(kinds)}
        for number, kinds in sorted(number_kinds.items())
        if len(kinds) > 1
    ]

    live_keys: set[tuple[str, int]] = set()
    for kind, rows in entity_rows.items():
        for entity in rows:
            entity_id = int(entity["id"])
            live_keys.add((kind.value, entity_id))
            identity_id = entity.get("barcode_identity_id")
            identity = by_id.get(int(identity_id)) if identity_id is not None else None
            if identity is None:
                conflicts["missing_identity_coverage"].append(
                    {"entity_kind": kind.value, "entity_id": entity_id}
                )
            elif (
                str(identity["entity_kind"]) != kind.value
                or int(identity["entity_id"]) != entity_id
            ):
                conflicts["broken_identity_links"].append(
                    {
                        "entity_kind": kind.value,
                        "entity_id": entity_id,
                        "identity_id": int(identity["id"]),
                    }
                )

    for identity in identities:
        key = (str(identity["entity_kind"]), int(identity["entity_id"]))
        if key not in live_keys and identity.get("retired_at") is None:
            conflicts["deleted_objects_with_unretired_identity"].append(
                {
                    "identity_id": int(identity["id"]),
                    "entity_kind": key[0],
                    "entity_id": key[1],
                }
            )
    if mutable_legacy_barcode_column:
        conflicts["unexpected_mutable_legacy_inputs"].append("box_files.barcode column")

    conflict_count = sum(len(values) for values in conflicts.values())
    return {
        "safe": conflict_count == 0,
        "conflict_count": conflict_count,
        "counts": {
            "identities": len(identities),
            **{
                kind.value: len(rows)
                for kind, rows in entity_rows.items()
            },
        },
        "conflicts": conflicts,
    }


def inspect_database(database_url: str) -> dict[str, Any]:
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            identities = list(
                connection.execute(
                    text(
                        """
                        SELECT
                            id, entity_kind, issuance_number, barcode, entity_id,
                            retired_at
                        FROM barcode_identities
                        ORDER BY issuance_number
                        """
                    )
                ).mappings()
            )
            entities = {
                kind: list(
                    connection.execute(
                        text(
                            f"SELECT id, barcode_identity_id FROM {table_name} ORDER BY id"
                        )
                    ).mappings()
                )
                for kind, table_name in ENTITY_TABLES.items()
            }
            columns = {
                column["name"] for column in inspect(connection).get_columns("box_files")
            }
            report = analyze_rows(
                identities,
                entities,
                mutable_legacy_barcode_column="barcode" in columns,
            )
            connection.rollback()
            return report
    finally:
        engine.dispose()


def _render_text(report: Mapping[str, Any]) -> str:
    lines = [
        f"Barcode identities: {report['counts']['identities']}",
        f"Conflicts: {report['conflict_count']}",
    ]
    for name, values in report["conflicts"].items():
        if values:
            lines.append(f"CONFLICT {name}: {values}")
    lines.append(
        "Result: barcode registry is consistent"
        if report["safe"]
        else "Result: barcode registry requires reconciliation"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL") or get_settings().database_url,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = inspect_database(args.database_url)
    print(json.dumps(report, indent=2) if args.json else _render_text(report))
    return 0 if report["safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
