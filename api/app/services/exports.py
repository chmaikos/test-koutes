"""CSV and XLSX export helpers, sharing a common iterator."""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import datetime

from openpyxl import Workbook

from app.models.boxes import Box

EXPORT_COLUMNS = [
    "box_number",
    "owner",
    "current_warehouse_id",
    "status",
    "received_at",
    "processing_completed_at",
    "returned_at",
    "created_at",
    "updated_at",
]


def _row_for(box: Box) -> list:
    def _iso(value: datetime | None) -> str:
        return value.isoformat() if value else ""

    return [
        box.box_number,
        box.owner,
        box.current_warehouse_id,
        box.status.value,
        _iso(box.received_at),
        _iso(box.processing_completed_at),
        _iso(box.returned_at),
        _iso(box.created_at),
        _iso(box.updated_at),
    ]


def boxes_to_csv(boxes: Iterable[Box]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    for box in boxes:
        writer.writerow(_row_for(box))
    return buf.getvalue().encode("utf-8")


def boxes_to_xlsx(boxes: Iterable[Box]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Boxes"
    ws.append(EXPORT_COLUMNS)
    for box in boxes:
        ws.append(_row_for(box))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
