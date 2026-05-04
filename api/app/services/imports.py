"""XLSX import: parse a workbook into a list of receive-new-box requests and
apply them via `services.boxes.create_box`. Best-effort, with row-indexed
reasons for the skipped rows so the UI can show actionable errors.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.boxes import Box
from app.models.users import User
from app.models.warehouses import Warehouse
from app.services.boxes import BoxRuleError, create_box

# Hard caps: we do this on the request thread so we want a worst-case bound.
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000

# Header alias map: lowercased sheet header -> canonical key.
_HEADER_ALIASES = {
    "box_number": "box_number",
    "box number": "box_number",
    "box": "box_number",
    "owner": "owner",
    "customer": "owner",
    "warehouse_id": "warehouse_id",
    "warehouse id": "warehouse_id",
    "warehouse": "warehouse",
    "building": "warehouse",
}


@dataclass
class ImportSkipEntry:
    row: int
    box_number: str | None
    reason: str


@dataclass
class ImportOutcome:
    created: list[Box] = field(default_factory=list)
    skipped: list[ImportSkipEntry] = field(default_factory=list)


def _normalise_headers(raw: list[object | None]) -> dict[str, int]:
    out: dict[str, int] = {}
    for idx, value in enumerate(raw):
        if value is None:
            continue
        key = str(value).strip().lower()
        canonical = _HEADER_ALIASES.get(key)
        if canonical and canonical not in out:
            out[canonical] = idx
    return out


def _cell_str(cell: object | None) -> str:
    if cell is None:
        return ""
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    return str(cell).strip()


def import_boxes_xlsx(
    db: Session,
    *,
    user: User,
    file_bytes: bytes,
    default_warehouse_id: int | None = None,
) -> ImportOutcome:
    if len(file_bytes) > MAX_FILE_BYTES:
        raise BoxRuleError(
            f"file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"
        )
    try:
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a few different types
        raise BoxRuleError(f"could not read workbook: {exc}") from exc

    ws = wb.active
    if ws is None:
        raise BoxRuleError("workbook has no sheets")

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration as exc:
        raise BoxRuleError("workbook is empty") from exc

    headers = _normalise_headers(list(header_row))
    if "box_number" not in headers:
        raise BoxRuleError(
            "missing required column 'box_number' "
            "(recognised headers: box_number, owner, warehouse_id, warehouse)"
        )

    # Pre-resolve warehouses so we don't hit the DB once per row.
    warehouses_by_id = {
        w.id: w for w in db.scalars(select(Warehouse)).all()
    }
    warehouses_by_name = {
        w.name.strip().lower(): w for w in warehouses_by_id.values()
    }
    if default_warehouse_id is not None and default_warehouse_id not in warehouses_by_id:
        raise BoxRuleError(
            f"default warehouse {default_warehouse_id} does not exist"
        )

    outcome = ImportOutcome()
    seen_numbers: set[str] = set()

    for offset, row in enumerate(rows_iter, start=2):  # row 1 was the header
        if offset - 1 > MAX_ROWS:
            outcome.skipped.append(
                ImportSkipEntry(
                    row=offset,
                    box_number=None,
                    reason=f"row limit {MAX_ROWS} exceeded; remainder ignored",
                )
            )
            break

        if row is None or all(cell is None or _cell_str(cell) == "" for cell in row):
            continue  # blank row

        cells = list(row)

        def get(name: str) -> str:
            idx = headers.get(name)
            if idx is None or idx >= len(cells):
                return ""
            return _cell_str(cells[idx])

        box_number = get("box_number")
        owner = get("owner")
        wh_id_raw = get("warehouse_id")
        wh_name_raw = get("warehouse")

        if not box_number:
            outcome.skipped.append(
                ImportSkipEntry(
                    row=offset, box_number=None, reason="box_number is empty"
                )
            )
            continue
        if box_number in seen_numbers:
            outcome.skipped.append(
                ImportSkipEntry(
                    row=offset,
                    box_number=box_number,
                    reason="duplicate box_number within the uploaded file",
                )
            )
            continue
        seen_numbers.add(box_number)

        warehouse_id: int | None = None
        if wh_id_raw:
            try:
                warehouse_id = int(wh_id_raw)
            except ValueError:
                outcome.skipped.append(
                    ImportSkipEntry(
                        row=offset,
                        box_number=box_number,
                        reason=f"warehouse_id {wh_id_raw!r} is not an integer",
                    )
                )
                continue
            if warehouse_id not in warehouses_by_id:
                outcome.skipped.append(
                    ImportSkipEntry(
                        row=offset,
                        box_number=box_number,
                        reason=f"warehouse {warehouse_id} does not exist",
                    )
                )
                continue
        elif wh_name_raw:
            wh = warehouses_by_name.get(wh_name_raw.strip().lower())
            if wh is None:
                outcome.skipped.append(
                    ImportSkipEntry(
                        row=offset,
                        box_number=box_number,
                        reason=f"unknown warehouse name {wh_name_raw!r}",
                    )
                )
                continue
            warehouse_id = wh.id
        elif default_warehouse_id is not None:
            warehouse_id = default_warehouse_id
        else:
            outcome.skipped.append(
                ImportSkipEntry(
                    row=offset,
                    box_number=box_number,
                    reason=(
                        "no warehouse for this row "
                        "(set warehouse_id/warehouse column or pass a default)"
                    ),
                )
            )
            continue

        try:
            box = create_box(
                db,
                user=user,
                box_number=box_number,
                owner=owner,
                warehouse_id=warehouse_id,
            )
        except BoxRuleError as exc:
            outcome.skipped.append(
                ImportSkipEntry(
                    row=offset, box_number=box_number, reason=str(exc)
                )
            )
            continue
        outcome.created.append(box)

    return outcome
