"""CSV and XLSX export helpers, sharing a common iterator."""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from app.models.boxes import Box, BoxStatus

EXPORT_COLUMNS = [
    "Box Number",
    "Owner",
    "Warehouse ID",
    "Warehouse",
    "Status",
    "Received At",
    "Processing Completed At",
    "Returned At",
    "Created At",
    "Updated At",
]

# Indices of columns whose values are datetimes. Used by the XLSX writer to
# apply a date number_format and by the CSV writer to format them as text.
_DATETIME_COLUMN_INDICES = (5, 6, 7, 8, 9)

STATUS_LABELS: dict[BoxStatus, str] = {
    BoxStatus.received: "Received",
    BoxStatus.in_progress: "In progress",
    BoxStatus.processing_complete: "Processing complete",
    BoxStatus.ready_to_return: "Ready to return",
    BoxStatus.returned: "Returned",
}


def _to_naive_utc(value: datetime | None) -> datetime | None:
    """Normalise a datetime for openpyxl.

    Postgres ``TIMESTAMPTZ`` columns yield tz-aware datetimes, but openpyxl
    refuses to write those (it raises ``TypeError``). Convert to UTC and drop
    the tzinfo so XLSX gets a real date cell and CSV strftime stays
    consistent.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


def _row_for(box: Box, warehouses: Mapping[int, str]) -> list:
    """Return a row of native Python values (datetimes naive UTC for XLSX)."""
    return [
        box.box_number,
        box.owner,
        box.current_warehouse_id,
        warehouses.get(box.current_warehouse_id, ""),
        STATUS_LABELS.get(box.status, box.status.value),
        _to_naive_utc(box.received_at),
        _to_naive_utc(box.processing_completed_at),
        _to_naive_utc(box.returned_at),
        _to_naive_utc(box.created_at),
        _to_naive_utc(box.updated_at),
    ]


def _format_csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(value)


def boxes_to_csv(boxes: Iterable[Box], warehouses: Mapping[int, str]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    for box in boxes:
        writer.writerow([_format_csv_value(v) for v in _row_for(box, warehouses)])
    # Prepend a UTF-8 BOM so Excel on Windows opens non-ASCII owner names
    # correctly instead of mojibake.
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def boxes_to_xlsx(boxes: Iterable[Box], warehouses: Mapping[int, str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Boxes"
    ws.append(EXPORT_COLUMNS)

    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="E2E8F0")
    for col_idx in range(1, len(EXPORT_COLUMNS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill

    column_widths = [len(h) for h in EXPORT_COLUMNS]
    datetime_format = "yyyy-mm-dd hh:mm:ss"

    for box in boxes:
        row = _row_for(box, warehouses)
        ws.append(row)
        row_idx = ws.max_row
        for col_idx, value in enumerate(row, start=1):
            if col_idx - 1 in _DATETIME_COLUMN_INDICES and isinstance(value, datetime):
                ws.cell(row=row_idx, column=col_idx).number_format = datetime_format
                rendered = value.strftime("%Y-%m-%d %H:%M:%S")
            else:
                rendered = "" if value is None else str(value)
            if len(rendered) > column_widths[col_idx - 1]:
                column_widths[col_idx - 1] = len(rendered)

    for col_idx, width in enumerate(column_widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = min(width + 2, 40)

    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
