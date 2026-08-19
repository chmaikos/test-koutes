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
from app.schemas.requests import RequestAnalyticsOut, RequestReconciliationIssue
from app.services.lots import LotSummary
from app.services.pallets import PalletSummary
from app.services.productivity import EmployeeAverages

EXPORT_COLUMNS = [
    "Box Number",
    "Lot",
    "Pallet ID",
    "Pallet Number",
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
_DATETIME_COLUMN_INDICES = (8, 9, 10, 11)

STATUS_LABELS: dict[BoxStatus, str] = {
    BoxStatus.quarantined: "Quarantined",
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

REQUEST_RECONCILIATION_COLUMNS = [
    "Severity",
    "Issue Type",
    "Request ID",
    "Box ID",
    "Pallet ID",
    "Pallet Number",
    "Warehouse ID",
    "Warehouse",
    "Assignee ID",
    "Assignee",
    "Request Status",
    "Title",
    "Detail",
    "Occurred At",
    "Due At",
    "Request Link",
    "Box Link",
]

REQUEST_ANALYTICS_COLUMNS = [
    "Section",
    "Metric",
    "Name",
    "Value",
    "Numerator",
    "Denominator",
    "Rate",
    "Average Seconds",
    "Sample Size",
    "Completed Requests",
    "Completed Quantity",
]

LOT_SUMMARY_COLUMNS = [
    "Lot ID",
    "Lot",
    "Physical Box Count",
    "Total Non-Archived Boxes",
    "Quarantined",
    "Received",
    "Processing",
    "Incomplete",
    "Ready To Return",
    "Returned",
    "Eligible Box Count",
    "Completed Box Count",
    "Completion Percent",
    "Progress State",
    "Visible Warehouse Count",
    "Visible Warehouses",
    "Staged Receipt Count",
    "Last Box Activity",
    "Created At",
    "Updated At",
    "Metrics Scope",
]

PALLET_SUMMARY_COLUMNS = [
    "Pallet ID",
    "Pallet Number",
    "Lot ID",
    "Lot",
    "Warehouse IDs",
    "Warehouses",
    "Active",
    "Physical Box Count",
    "Total Non-Archived Boxes",
    "Eligible Box Count",
    "Completed Box Count",
    "Completion Percent",
    "Progress State",
    "Latest Activity",
    "Created At",
    "Updated At",
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
        getattr(box, "pallet_id", None),
        getattr(box, "pallet_number", None) or "",
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


def _lot_summary_row(summary: LotSummary) -> list:
    counts = summary.status_counts
    return [
        summary.id,
        summary.name,
        summary.physical_box_count,
        summary.box_count,
        counts["quarantined"],
        counts["received"],
        counts["processing"],
        counts["incomplete"],
        counts["ready_to_return"],
        counts["returned"],
        summary.eligible_box_count,
        summary.completed_box_count,
        summary.completion_percent,
        summary.progress_state,
        summary.warehouse_count,
        "; ".join(summary.warehouse_names),
        summary.staged_receipt_count,
        _to_naive_utc(summary.last_box_activity),
        _to_naive_utc(summary.created_at),
        _to_naive_utc(summary.updated_at),
        summary.scope_label,
    ]


def lot_summaries_to_csv(summaries: Iterable[LotSummary]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(LOT_SUMMARY_COLUMNS)
    for summary in summaries:
        writer.writerow(
            [_format_csv_value(value) for value in _lot_summary_row(summary)]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def lot_summaries_to_xlsx(summaries: Iterable[LotSummary]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Lot summary"
    _write_sheet(
        sheet,
        LOT_SUMMARY_COLUMNS,
        (_lot_summary_row(summary) for summary in summaries),
    )
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def _pallet_summary_row(summary: PalletSummary) -> list:
    return [
        summary.id,
        summary.pallet_number,
        summary.lot_id,
        summary.lot_name,
        "; ".join(str(value) for value in summary.warehouse_ids),
        "; ".join(summary.warehouse_names),
        summary.is_active,
        summary.physical_box_count,
        summary.box_count,
        summary.eligible_box_count,
        summary.completed_box_count,
        summary.completion_percent,
        summary.progress_state,
        _to_naive_utc(summary.latest_activity),
        _to_naive_utc(summary.created_at),
        _to_naive_utc(summary.updated_at),
    ]


def pallet_summaries_to_csv(summaries: Iterable[PalletSummary]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(PALLET_SUMMARY_COLUMNS)
    for summary in summaries:
        writer.writerow(
            [_format_csv_value(value) for value in _pallet_summary_row(summary)]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def pallet_summaries_to_xlsx(summaries: Iterable[PalletSummary]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Pallet summary"
    _write_sheet(
        sheet,
        PALLET_SUMMARY_COLUMNS,
        (_pallet_summary_row(summary) for summary in summaries),
    )
    buf = io.BytesIO()
    workbook.save(buf)
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


def _reconciliation_row(issue: RequestReconciliationIssue) -> list:
    return [
        issue.severity,
        issue.issue_type,
        issue.request_id,
        issue.box_id,
        issue.pallet_id,
        issue.pallet_number,
        issue.warehouse_id,
        issue.warehouse_name,
        issue.assigned_mover_user_id,
        issue.assigned_mover_name,
        issue.request_status.value,
        issue.title,
        issue.detail,
        _to_naive_utc(issue.occurred_at),
        _to_naive_utc(issue.due_at),
        issue.request_path,
        issue.box_path,
    ]


def request_reconciliation_to_csv(
    issues: Iterable[RequestReconciliationIssue],
) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(REQUEST_RECONCILIATION_COLUMNS)
    for issue in issues:
        writer.writerow(
            [_format_csv_value(value) for value in _reconciliation_row(issue)]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def request_reconciliation_to_xlsx(
    issues: Iterable[RequestReconciliationIssue],
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Reconciliation"
    _write_sheet(
        sheet,
        REQUEST_RECONCILIATION_COLUMNS,
        (_reconciliation_row(issue) for issue in issues),
    )
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def _analytics_rows(report: RequestAnalyticsOut) -> list[list]:
    rows: list[list] = [
        ["summary", "total_requests", "", report.total_requests],
        ["summary", "completed_requests", "", report.completed_requests],
    ]
    for name in (
        "approval_duration",
        "preparation_duration",
        "transport_duration",
        "acceptance_duration",
    ):
        metric = getattr(report, name)
        rows.append(
            [
                "duration",
                name,
                "supported" if metric.supported else "unsupported",
                None,
                None,
                None,
                None,
                metric.average_seconds,
                metric.sample_size,
            ]
        )
    for name in ("on_time", "discrepancy", "shortage", "overage"):
        metric = getattr(report, name)
        rows.append(
            [
                "rate",
                name,
                "",
                None,
                metric.numerator,
                metric.denominator,
                metric.rate,
            ]
        )
    for section, reasons in (
        ("rejection_reason", report.rejection_reasons),
        ("cancellation_reason", report.cancellation_reasons),
    ):
        rows.extend(
            [section, "reason", reason.reason, reason.count] for reason in reasons
        )
    for section, throughput in (
        ("warehouse_throughput", report.throughput_by_warehouse),
        ("mover_throughput", report.throughput_by_mover),
    ):
        rows.extend(
            [
                section,
                "throughput",
                item.name,
                item.id,
                None,
                None,
                None,
                None,
                None,
                item.completed_requests,
                item.completed_quantity,
            ]
            for item in throughput
        )
    return [row + [None] * (len(REQUEST_ANALYTICS_COLUMNS) - len(row)) for row in rows]


def request_analytics_to_csv(report: RequestAnalyticsOut) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(REQUEST_ANALYTICS_COLUMNS)
    for row in _analytics_rows(report):
        writer.writerow([_format_csv_value(value) for value in row])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def request_analytics_to_xlsx(report: RequestAnalyticsOut) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Request analytics"
    _write_sheet(sheet, REQUEST_ANALYTICS_COLUMNS, _analytics_rows(report))
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()
