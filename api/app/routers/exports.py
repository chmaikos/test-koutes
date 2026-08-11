from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models.boxes import Box
from app.models.employees import Employee, ProductivityEntry
from app.models.warehouses import Warehouse
from app.routers._filters import BoxFilters, apply_box_filters, parse_box_filters
from app.schemas.lots import LotProgressState, LotSortField
from app.services.acl import apply_warehouse_filter
from app.services.exports import (
    ProductivityDetailExportRow,
    ProductivitySummaryExportRow,
    boxes_to_csv,
    boxes_to_xlsx,
    lot_summaries_to_csv,
    lot_summaries_to_xlsx,
    productivity_to_csv,
    productivity_to_xlsx,
    request_analytics_to_csv,
    request_analytics_to_xlsx,
    request_reconciliation_to_csv,
    request_reconciliation_to_xlsx,
)
from app.services.lots import list_lot_summaries
from app.services.productivity import employee_averages
from app.services.request_reporting import (
    RequestReportFilters,
    build_reconciliation,
    build_request_analytics,
)

router = APIRouter(prefix="/exports", tags=["exports"])


def _filename(prefix: str, ext: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}.{ext}"


def _warehouse_names(db) -> dict[int, str]:
    return dict(db.execute(select(Warehouse.id, Warehouse.name)).all())


def _request_filters(
    warehouse_id: int | None,
    assigned_mover_user_id: int | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> RequestReportFilters:
    if from_at is not None and to_at is not None and to_at <= from_at:
        raise HTTPException(status_code=400, detail="to_at must be after from_at")
    return RequestReportFilters(
        warehouse_id=warehouse_id,
        assigned_mover_user_id=assigned_mover_user_id,
        from_at=from_at,
        to_at=to_at,
    )


def _productivity_report_rows(
    db,
    user,
    *,
    warehouse_id: int | None,
    anchor: date,
) -> tuple[list[ProductivitySummaryExportRow], list[ProductivityDetailExportRow]]:
    warehouse_stmt = select(Warehouse)
    if warehouse_id is not None:
        warehouse_stmt = warehouse_stmt.where(Warehouse.id == warehouse_id)
    warehouse_stmt = apply_warehouse_filter(
        warehouse_stmt, user, Warehouse.id
    ).order_by(Warehouse.name)
    warehouses = db.scalars(warehouse_stmt).all()
    names = {warehouse.id: warehouse.name for warehouse in warehouses}

    summaries: list[ProductivitySummaryExportRow] = []
    for warehouse in warehouses:
        averages = employee_averages(
            db,
            user=user,
            warehouse_id=warehouse.id,
            anchor=anchor,
            minimum=warehouse.min_pages_per_day,
        )
        summaries.extend(
            ProductivitySummaryExportRow(
                warehouse_id=warehouse.id,
                warehouse_name=warehouse.name,
                minimum=warehouse.min_pages_per_day,
                average=average,
            )
            for average in averages
        )

    start = anchor - timedelta(days=89)
    detail_stmt = (
        select(ProductivityEntry, Employee.full_name)
        .join(Employee, Employee.id == ProductivityEntry.employee_id)
        .where(
            ProductivityEntry.entry_date >= start,
            ProductivityEntry.entry_date <= anchor,
        )
    )
    if warehouse_id is not None:
        detail_stmt = detail_stmt.where(
            ProductivityEntry.warehouse_id == warehouse_id
        )
    detail_stmt = apply_warehouse_filter(
        detail_stmt, user, ProductivityEntry.warehouse_id
    ).order_by(
        ProductivityEntry.warehouse_id,
        Employee.full_name,
        ProductivityEntry.entry_date,
    )
    details = [
        ProductivityDetailExportRow(
            warehouse_id=entry.warehouse_id,
            warehouse_name=names.get(entry.warehouse_id, ""),
            employee_id=entry.employee_id,
            employee_name=employee_name,
            entry=entry,
        )
        for entry, employee_name in db.execute(detail_stmt).all()
    ]
    return summaries, details


@router.get("/boxes.csv")
def export_csv(
    db: DbSession,
    user: CurrentUser,
    filters: Annotated[BoxFilters, Depends(parse_box_filters)],
) -> Response:
    stmt = apply_box_filters(select(Box), filters)
    stmt = apply_warehouse_filter(stmt, user, Box.current_warehouse_id).order_by(
        Box.updated_at.desc()
    )
    rows = db.scalars(stmt).all()
    payload = boxes_to_csv(rows, _warehouse_names(db))
    return Response(
        content=payload,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename("boxes", "csv")}"'
        },
    )


@router.get("/boxes.xlsx")
def export_xlsx(
    db: DbSession,
    user: CurrentUser,
    filters: Annotated[BoxFilters, Depends(parse_box_filters)],
) -> Response:
    stmt = apply_box_filters(select(Box), filters)
    stmt = apply_warehouse_filter(stmt, user, Box.current_warehouse_id).order_by(
        Box.updated_at.desc()
    )
    rows = db.scalars(stmt).all()
    payload = boxes_to_xlsx(rows, _warehouse_names(db))
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename("boxes", "xlsx")}"'
        },
    )


def _lot_export_rows(
    db,
    user,
    *,
    search: str | None,
    warehouse_id: int | None,
    progress_state: LotProgressState | None,
    sort_by: LotSortField,
    sort_dir: Literal["asc", "desc"],
):
    rows, _total = list_lot_summaries(
        db,
        user=user,
        search=search,
        warehouse_id=warehouse_id,
        progress_state=progress_state,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=1,
        page_size=1_000_000,
    )
    return rows


@router.get("/lots.csv")
def export_lots_csv(
    db: DbSession,
    user: CurrentUser,
    search: str | None = None,
    warehouse_id: int | None = Query(default=None, ge=1),
    progress_state: LotProgressState | None = None,
    sort_by: LotSortField = "last_activity",
    sort_dir: Literal["asc", "desc"] = "desc",
) -> Response:
    rows = _lot_export_rows(
        db,
        user,
        search=search,
        warehouse_id=warehouse_id,
        progress_state=progress_state,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    return Response(
        content=lot_summaries_to_csv(rows),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename("lots", "csv")}"'
        },
    )


@router.get("/lots.xlsx")
def export_lots_xlsx(
    db: DbSession,
    user: CurrentUser,
    search: str | None = None,
    warehouse_id: int | None = Query(default=None, ge=1),
    progress_state: LotProgressState | None = None,
    sort_by: LotSortField = "last_activity",
    sort_dir: Literal["asc", "desc"] = "desc",
) -> Response:
    rows = _lot_export_rows(
        db,
        user,
        search=search,
        warehouse_id=warehouse_id,
        progress_state=progress_state,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    return Response(
        content=lot_summaries_to_xlsx(rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename("lots", "xlsx")}"'
        },
    )


@router.get("/productivity.csv")
def export_productivity_csv(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    report_date: date | None = None,
) -> Response:
    anchor = report_date or datetime.now(UTC).date()
    summaries, _ = _productivity_report_rows(
        db, user, warehouse_id=warehouse_id, anchor=anchor
    )
    return Response(
        content=productivity_to_csv(summaries),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_filename("productivity", "csv")}"'
            )
        },
    )


@router.get("/productivity.xlsx")
def export_productivity_xlsx(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    report_date: date | None = None,
) -> Response:
    anchor = report_date or datetime.now(UTC).date()
    summaries, details = _productivity_report_rows(
        db, user, warehouse_id=warehouse_id, anchor=anchor
    )
    return Response(
        content=productivity_to_xlsx(summaries, details),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_filename("productivity", "xlsx")}"'
            )
        },
    )


def _request_reconciliation_export(
    db,
    user,
    *,
    warehouse_id: int | None,
    assigned_mover_user_id: int | None,
    from_at: datetime | None,
    to_at: datetime | None,
    severity: str | None,
    issue_type: str | None,
):
    return build_reconciliation(
        db,
        user=user,
        filters=_request_filters(
            warehouse_id, assigned_mover_user_id, from_at, to_at
        ),
        severity=severity,
        issue_type=issue_type,
        page=1,
        page_size=100_000,
    )


@router.get("/request-reconciliation.csv")
def export_request_reconciliation_csv(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    assigned_mover_user_id: int | None = Query(default=None, ge=1),
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    severity: str | None = None,
    issue_type: str | None = None,
) -> Response:
    report = _request_reconciliation_export(
        db,
        user,
        warehouse_id=warehouse_id,
        assigned_mover_user_id=assigned_mover_user_id,
        from_at=from_at,
        to_at=to_at,
        severity=severity,
        issue_type=issue_type,
    )
    return Response(
        content=request_reconciliation_to_csv(report.items),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_filename("request-reconciliation", "csv")}"'
            )
        },
    )


@router.get("/request-reconciliation.xlsx")
def export_request_reconciliation_xlsx(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    assigned_mover_user_id: int | None = Query(default=None, ge=1),
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    severity: str | None = None,
    issue_type: str | None = None,
) -> Response:
    report = _request_reconciliation_export(
        db,
        user,
        warehouse_id=warehouse_id,
        assigned_mover_user_id=assigned_mover_user_id,
        from_at=from_at,
        to_at=to_at,
        severity=severity,
        issue_type=issue_type,
    )
    return Response(
        content=request_reconciliation_to_xlsx(report.items),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_filename("request-reconciliation", "xlsx")}"'
            )
        },
    )


def _analytics_report(
    db,
    user,
    *,
    warehouse_id: int | None,
    assigned_mover_user_id: int | None,
    from_at: datetime | None,
    to_at: datetime | None,
):
    return build_request_analytics(
        db,
        user=user,
        filters=_request_filters(
            warehouse_id, assigned_mover_user_id, from_at, to_at
        ),
    )


@router.get("/request-analytics.csv")
def export_request_analytics_csv(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    assigned_mover_user_id: int | None = Query(default=None, ge=1),
    from_at: datetime | None = None,
    to_at: datetime | None = None,
) -> Response:
    report = _analytics_report(
        db,
        user,
        warehouse_id=warehouse_id,
        assigned_mover_user_id=assigned_mover_user_id,
        from_at=from_at,
        to_at=to_at,
    )
    return Response(
        content=request_analytics_to_csv(report),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_filename("request-analytics", "csv")}"'
            )
        },
    )


@router.get("/request-analytics.xlsx")
def export_request_analytics_xlsx(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    assigned_mover_user_id: int | None = Query(default=None, ge=1),
    from_at: datetime | None = None,
    to_at: datetime | None = None,
) -> Response:
    report = _analytics_report(
        db,
        user,
        warehouse_id=warehouse_id,
        assigned_mover_user_id=assigned_mover_user_id,
        from_at=from_at,
        to_at=to_at,
    )
    return Response(
        content=request_analytics_to_xlsx(report),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_filename("request-analytics", "xlsx")}"'
            )
        },
    )
