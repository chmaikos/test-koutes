from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, require_operator
from app.models.employees import Employee, ProductivityEntry
from app.schemas.employees import (
    PerformerOut,
    ProductivityEntryCreate,
    ProductivityEntryOut,
    ProductivitySummaryOut,
    WarehouseProductivityOut,
)
from app.services.acl import apply_warehouse_filter, can_access
from app.services.productivity import (
    HOURS_PER_PRODUCTIVITY_DAY,
    WarehouseProductivity,
    daily_summary,
    week_bounds,
    weekly_summary,
)

router = APIRouter(prefix="/productivity", tags=["productivity"])


def _today_in_app_tz() -> date:
    """Today's date in the configured ``APP_TIMEZONE`` (default UTC).

    The "daily" reporting boundary needs to match warehouse local time --
    using UTC midnight in Athens means a shift that ends at 17:00 local
    can spill into the next day's report. Falling back to UTC on an
    unknown TZ keeps a misconfigured deployment functional rather than
    500-ing on every productivity request.
    """
    tz_name = get_settings().app_timezone or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = UTC
    return datetime.now(tz).date()


def _to_performer_out(performer) -> PerformerOut:
    return PerformerOut(
        employee_id=performer.employee_id,
        employee_name=performer.employee_name,
        pages=performer.pages,
        hours=performer.hours,
        pages_per_day=performer.pages_per_day,
    )


def _to_warehouse_out(summary: WarehouseProductivity) -> WarehouseProductivityOut:
    return WarehouseProductivityOut(
        warehouse_id=summary.warehouse_id,
        total_pages=summary.total_pages,
        total_hours=summary.total_hours,
        avg_pages_per_day=summary.avg_pages_per_day,
        entry_count=summary.entry_count,
        active_employees=summary.active_employees,
        top=[_to_performer_out(p) for p in summary.top],
        bottom=[_to_performer_out(p) for p in summary.bottom],
    )


def _wrap_summary(
    summaries: dict[int, WarehouseProductivity],
    *,
    period_start: date,
    period_end: date,
) -> ProductivitySummaryOut:
    """Bundle per-warehouse summaries with grand totals.

    Grand totals are computed from the same per-warehouse numbers, which
    keeps the "did the math agree?" question moot -- if the per-warehouse
    cards on the UI sum to one total, this endpoint reports that total.
    """
    warehouses = sorted(summaries.values(), key=lambda s: s.warehouse_id)
    total_pages = sum(s.total_pages for s in warehouses)
    total_hours = round(sum(s.total_hours for s in warehouses), 2)
    avg = (
        round(
            (total_pages / total_hours) * HOURS_PER_PRODUCTIVITY_DAY,
            2,
        )
        if total_hours > 0
        else 0.0
    )
    return ProductivitySummaryOut(
        period_start=period_start,
        period_end=period_end,
        warehouses=[_to_warehouse_out(s) for s in warehouses],
        total_pages=total_pages,
        total_hours=total_hours,
        avg_pages_per_day=avg,
    )


# --- entries ----------------------------------------------------------------


@router.get("/entries", response_model=list[ProductivityEntryOut])
def list_entries(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    employee_id: int | None = Query(default=None, ge=1),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
) -> list[ProductivityEntryOut]:
    stmt = select(ProductivityEntry)
    if warehouse_id is not None:
        stmt = stmt.where(ProductivityEntry.warehouse_id == warehouse_id)
    if employee_id is not None:
        stmt = stmt.where(ProductivityEntry.employee_id == employee_id)
    if from_date is not None:
        stmt = stmt.where(ProductivityEntry.entry_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(ProductivityEntry.entry_date <= to_date)
    stmt = apply_warehouse_filter(
        stmt, user, ProductivityEntry.warehouse_id
    ).order_by(
        ProductivityEntry.entry_date.desc(), ProductivityEntry.id.desc()
    )
    rows = db.scalars(stmt).all()
    return [ProductivityEntryOut.model_validate(e) for e in rows]


@router.post(
    "/entries",
    response_model=ProductivityEntryOut,
    status_code=status.HTTP_201_CREATED,
)
def upsert_entry(
    payload: ProductivityEntryCreate,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_operator)],
) -> ProductivityEntryOut:
    """Create or update a productivity entry for ``(employee, date)``.

    Operators routinely correct yesterday's numbers when a count was
    miscounted; a hard 409 on duplicates would force them to delete and
    re-create the row, which is annoying *and* loses the audit trail.
    Upserting on the unique constraint is much friendlier.
    """
    emp = db.get(Employee, payload.employee_id)
    if emp is None:
        raise HTTPException(status_code=404, detail="employee not found")
    if not can_access(user, emp.warehouse_id):
        raise HTTPException(status_code=404, detail="employee not found")
    if not emp.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot log productivity for an inactive employee",
        )

    existing = db.scalar(
        select(ProductivityEntry).where(
            ProductivityEntry.employee_id == payload.employee_id,
            ProductivityEntry.entry_date == payload.entry_date,
        )
    )
    if existing is None:
        entry = ProductivityEntry(
            employee_id=emp.id,
            warehouse_id=emp.warehouse_id,
            entry_date=payload.entry_date,
            pages=payload.pages,
            hours_worked=payload.hours_worked,
            note=payload.note,
            created_by_user_id=user.id,
        )
        db.add(entry)
    else:
        existing.pages = payload.pages
        existing.hours_worked = payload.hours_worked
        existing.note = payload.note
        existing.warehouse_id = emp.warehouse_id
        existing.created_by_user_id = user.id
        entry = existing
    db.commit()
    db.refresh(entry)
    return ProductivityEntryOut.model_validate(entry)


@router.delete(
    "/entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_entry(
    entry_id: int,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_operator)],
) -> Response:
    entry = db.get(ProductivityEntry, entry_id)
    if entry is None or not can_access(user, entry.warehouse_id):
        raise HTTPException(status_code=404, detail="entry not found")
    db.delete(entry)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- summaries --------------------------------------------------------------


@router.get("/summary", response_model=ProductivitySummaryOut)
def daily_summary_endpoint(
    db: DbSession,
    user: CurrentUser,
    on_date: date | None = Query(default=None, alias="date"),
    warehouse_id: int | None = Query(default=None, ge=1),
) -> ProductivitySummaryOut:
    """Per-warehouse productivity summary for a single day."""
    target = on_date or _today_in_app_tz()
    warehouse_ids = [warehouse_id] if warehouse_id is not None else None
    summaries = daily_summary(
        db,
        user=user,
        warehouse_ids=warehouse_ids,
        on_date=target,
    )
    return _wrap_summary(summaries, period_start=target, period_end=target)


@router.get("/summary/weekly", response_model=ProductivitySummaryOut)
def weekly_summary_endpoint(
    db: DbSession,
    user: CurrentUser,
    week_start: date | None = Query(default=None),
    warehouse_id: int | None = Query(default=None, ge=1),
) -> ProductivitySummaryOut:
    """Per-warehouse productivity summary for an ISO week."""
    anchor = week_start or _today_in_app_tz()
    warehouse_ids = [warehouse_id] if warehouse_id is not None else None
    summaries = weekly_summary(
        db,
        user=user,
        warehouse_ids=warehouse_ids,
        week_start=anchor,
    )
    monday, sunday = week_bounds(anchor)
    return _wrap_summary(summaries, period_start=monday, period_end=sunday)
