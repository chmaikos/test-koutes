"""CSV and XLSX export helpers, sharing a common iterator."""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from app.models.boxes import Box, BoxStatus
from app.models.employees import ProductivityEntry
from app.services.productivity import EmployeeAverages

EXPORT_COLUMNS = [
    "Box Number",
    "Lot",
    "Contents",
    "Warehouse ID",
    "Warehouse",
    "Status",
    "Received At",
    "Returned At",
    "Created At",
    "Updated At",
]

# Indices of columns whose values are datetimes. Used by the XLSX writer to
# apply a date number_format and by the CSV writer to format them as text.
_DATETIME_COLUMN_INDICES = (6, 7, 8, 9)

STATUS_LABELS: dict[BoxStatus, str] = {
    BoxStatus.received: "Received",
    BoxStatus.ready_to_return: "Ready to return",
    BoxStatus.returned: "Returned",
}

PRODUCTIVITY_SUMMARY_COLUMNS = [
    "Warehouse ID",
    "Warehouse",
    "Employee ID",
    "Employee",
    "Minimum Pages / Day",
    "Week Start",
    "Week End",
    "Weekly Pages / Day",
    "Monthly Start",
    "Monthly End",
    "Monthly Pages / Day",
    "3-Month Start",
    "3-Month End",
    "3-Month Pages / Day",
    "Consistently Below Minimum",
    "Excluded From Metrics",
]

PRODUCTIVITY_DETAIL_COLUMNS = [
    "Warehouse ID",
    "Warehouse",
    "Employee ID",
    "Employee",
    "Entry Date",
    "Pages",
    "Hours Worked",
    "Pages / 8-Hour Day",
    "Entry Excluded",
    "Note",
]


@dataclass(frozen=True)
class ProductivitySummaryExportRow:
    warehouse_id: int
    warehouse_name: str
    minimum: int | None
    average: EmployeeAverages


@dataclass(frozen=True)
class ProductivityDetailExportRow:
    warehouse_id: int
    warehouse_name: str
    employee_id: int
    employee_name: str
    entry: ProductivityEntry


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
        box.lot,
        box.contents or "",
        box.current_warehouse_id,
        warehouses.get(box.current_warehouse_id, ""),
        STATUS_LABELS.get(box.status, box.status.value),
        _to_naive_utc(box.received_at),
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
    # Prepend a UTF-8 BOM so Excel on Windows opens non-ASCII lot names
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


def _summary_export_row(row: ProductivitySummaryExportRow) -> list:
    average = row.average
    return [
        row.warehouse_id,
        row.warehouse_name,
        average.employee_id,
        average.employee_name,
        row.minimum,
        average.weekly.period_start,
        average.weekly.period_end,
        average.weekly.pages_per_day,
        average.monthly.period_start,
        average.monthly.period_end,
        average.monthly.pages_per_day,
        average.three_month.period_start,
        average.three_month.period_end,
        average.three_month.pages_per_day,
        average.consistently_below_minimum,
        average.excluded_from_metrics,
    ]


def _detail_export_row(row: ProductivityDetailExportRow) -> list:
    entry = row.entry
    hours = float(entry.hours_worked)
    pages_per_day = round((entry.pages / hours) * 8, 2) if hours > 0 else None
    return [
        row.warehouse_id,
        row.warehouse_name,
        row.employee_id,
        row.employee_name,
        entry.entry_date,
        entry.pages,
        hours,
        pages_per_day,
        entry.excluded_from_metrics,
        entry.note or "",
    ]


def productivity_to_csv(rows: Iterable[ProductivitySummaryExportRow]) -> bytes:
    """CSV report containing one summary row per active employee."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(PRODUCTIVITY_SUMMARY_COLUMNS)
    for row in rows:
        writer.writerow(
            [_format_csv_value(value) for value in _summary_export_row(row)]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _write_sheet(ws, columns: list[str], rows: Iterable[list]) -> None:
    ws.append(columns)
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="E2E8F0")
    widths = [len(column) for column in columns]
    for column_index in range(1, len(columns) + 1):
        cell = ws.cell(row=1, column=column_index)
        cell.font = header_font
        cell.fill = header_fill
    for row in rows:
        ws.append(row)
        for column_index, value in enumerate(row, start=1):
            if isinstance(value, date | datetime):
                ws.cell(row=ws.max_row, column=column_index).number_format = (
                    "yyyy-mm-dd"
                )
            rendered = "" if value is None else str(value)
            widths[column_index - 1] = max(
                widths[column_index - 1], len(rendered)
            )
    for column_index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(column_index)].width = min(
            width + 2, 40
        )
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def productivity_to_xlsx(
    summaries: Iterable[ProductivitySummaryExportRow],
    details: Iterable[ProductivityDetailExportRow],
) -> bytes:
    """XLSX report with employee averages and raw daily entries."""
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Employee averages"
    _write_sheet(
        summary_sheet,
        PRODUCTIVITY_SUMMARY_COLUMNS,
        (_summary_export_row(row) for row in summaries),
    )
    detail_sheet = workbook.create_sheet("Daily entries")
    _write_sheet(
        detail_sheet,
        PRODUCTIVITY_DETAIL_COLUMNS,
        (_detail_export_row(row) for row in details),
    )
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()
