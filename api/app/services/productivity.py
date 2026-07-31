"""Per-warehouse productivity aggregations.

This module is the single source of truth for productivity math: the
dashboard, the standalone productivity page, and the end-of-day email
report all read from these helpers so the numbers can never disagree.

Two concrete decisions worth remembering:

* ``avg_pages_per_day`` is ``sum(pages) / sum(hours) * HOURS_PER_PRODUCTIVITY_DAY``
  over the period (same weighting as the old hourly rate, scaled to an
  8-hour workday). A picker who logs ``50 pages / 0.5h`` should not
  dilute the warehouse average alongside an 8-hour shift; weighting by
  hours keeps the ratio match the operator's intuition of "pages
  produced per standard workday across the whole crew".
* ``HOURS_PER_PRODUCTIVITY_DAY`` matches the default
  ``Employee.default_hours_per_day`` (8): "per day" means per that many
  logged work hours, not wall-clock calendar days.
* Top/bottom rankings only consider entries that belong to currently
  active employees. Inactive (offboarded) staff still contribute to
  totals so historical numbers stay accurate, but their names are
  hidden from leaderboards where appearing would just confuse the
  recipient.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.employees import Employee, ProductivityEntry
from app.models.users import User
from app.models.warehouses import Warehouse
from app.services.acl import allowed_warehouse_ids

# Aligns with ``Employee.default_hours_per_day`` default (8). All
# "pages per day" metrics scale logged hours to this standard day.
HOURS_PER_PRODUCTIVITY_DAY = 8.0


@dataclass(frozen=True)
class Performer:
    """A single ranked row in the top/bottom leaderboard."""

    employee_id: int
    employee_name: str
    pages: int
    hours: float
    pages_per_day: float


@dataclass(frozen=True)
class WarehouseProductivity:
    """Aggregated productivity for one warehouse over one period."""

    warehouse_id: int
    total_pages: int = 0
    total_hours: float = 0.0
    avg_pages_per_day: float = 0.0
    entry_count: int = 0
    active_employees: int = 0
    top: list[Performer] = field(default_factory=list)
    bottom: list[Performer] = field(default_factory=list)


@dataclass(frozen=True)
class EmployeePeriodAverage:
    """One employee's weighted productivity over a named date range."""

    period_start: date
    period_end: date
    total_pages: int = 0
    total_hours: float = 0.0
    entry_count: int = 0
    pages_per_day: float | None = None
    below_minimum: bool | None = None


@dataclass(frozen=True)
class EmployeeAverages:
    """Weekly, monthly, and rolling averages for one active employee."""

    employee_id: int
    employee_name: str
    excluded_from_metrics: bool
    weekly: EmployeePeriodAverage
    monthly: EmployeePeriodAverage
    three_month: EmployeePeriodAverage
    consistently_below_minimum: bool = False


def _round_hours(value: Decimal | float | int | None) -> float:
    """Return ``value`` as a 2dp float, defaulting to 0.0 when missing."""
    if value is None:
        return 0.0
    return round(float(value), 2)


def _round_ppd(pages: int, hours: float) -> float:
    """Pages per standard workday, rounded to 2dp. 0 hours -> 0."""
    if hours <= 0:
        return 0.0
    return round((pages / hours) * HOURS_PER_PRODUCTIVITY_DAY, 2)


def _scope_to_warehouses(
    stmt: Select, user: User | None, warehouse_id_col
) -> Select:
    """Apply the user's warehouse ACL when ``user`` is provided.

    A scheduler-driven caller (e.g. the daily report) passes ``user=None``
    to bypass the ACL: we want to email every warehouse regardless of who
    happens to be logged in. Web callers pass the requesting ``User`` and
    get the same behaviour as :func:`apply_warehouse_filter`.
    """
    if user is None:
        return stmt
    allowed = allowed_warehouse_ids(user)
    if allowed is None:
        return stmt
    return stmt.where(warehouse_id_col.in_(allowed))


def _rank_performers(
    rows: Iterable[tuple[int, str, bool, int, float]], *, n: int
) -> tuple[list[Performer], list[Performer]]:
    """Split ranked employees into top/bottom lists of length ``n``.

    Inputs are tuples of ``(employee_id, name, is_active, pages, hours)``.
    Tie-breaks (intentional, deterministic):

    * Ranking metric is pages/day desc (8h-scaled rate).
    * Ties on pages/day are broken by total pages desc -- a high rate
      over more pages outranks the same rate over fewer.
    * Final tie-break is name ascending so the order doesn't change
      when two employees happen to have identical numbers.
    """
    ranked: list[Performer] = []
    for employee_id, name, is_active, pages, hours in rows:
        if not is_active:
            # Inactive employees count toward totals (computed elsewhere)
            # but do not appear in leaderboards.
            continue
        if hours <= 0:
            continue
        ppd = _round_ppd(int(pages), float(hours))
        ranked.append(
            Performer(
                employee_id=int(employee_id),
                employee_name=name,
                pages=int(pages),
                hours=_round_hours(hours),
                pages_per_day=ppd,
            )
        )
    if not ranked:
        return [], []
    ranked.sort(
        key=lambda p: (-p.pages_per_day, -p.pages, p.employee_name.lower())
    )
    top = ranked[:n]
    # Bottom = lowest pages/day first. Reuse the sort, slice from the
    # tail, then reverse so the *worst* performer is at index 0 (which is
    # what UIs and email reports tend to show).
    bottom_pool = ranked[-n:][::-1]
    return top, bottom_pool


def _summary_for_range(
    db: Session,
    *,
    user: User | None,
    warehouse_ids: Sequence[int] | None,
    start: date,
    end: date,
    top_n: int,
) -> dict[int, WarehouseProductivity]:
    """Build a per-warehouse summary covering ``[start, end]`` (inclusive).

    Returns one ``WarehouseProductivity`` per warehouse that has at least
    one entry in the range; warehouses with no data are simply absent
    from the dict (callers fill in defaults as needed).
    """
    # Both totals and per-employee aggregates filter out rows where either
    # the entry itself or its owning employee is flagged out of the metrics.
    # Joining ``Employee`` in the totals query keeps the two queries on the
    # same row set so the numbers can't disagree -- the alternative (filter
    # entries only) would let a per-employee admin exclusion leak into
    # warehouse totals while keeping the leaderboard clean.
    base_stmt: Select = (
        select(
            ProductivityEntry.warehouse_id,
            func.coalesce(func.sum(ProductivityEntry.pages), 0),
            func.coalesce(func.sum(ProductivityEntry.hours_worked), 0),
            func.count(ProductivityEntry.id),
        )
        .join(Employee, Employee.id == ProductivityEntry.employee_id)
        .where(
            ProductivityEntry.entry_date >= start,
            ProductivityEntry.entry_date <= end,
            ProductivityEntry.excluded_from_metrics.is_(False),
            Employee.excluded_from_metrics.is_(False),
        )
    )
    if warehouse_ids:
        base_stmt = base_stmt.where(
            ProductivityEntry.warehouse_id.in_(list(warehouse_ids))
        )
    elif warehouse_ids is None:
        base_stmt = base_stmt.where(
            ProductivityEntry.warehouse_id.in_(
                select(Warehouse.id).where(Warehouse.is_active.is_(True))
            )
        )
    base_stmt = _scope_to_warehouses(
        base_stmt, user, ProductivityEntry.warehouse_id
    ).group_by(ProductivityEntry.warehouse_id)

    totals = db.execute(base_stmt).all()

    # Per-employee aggregates for ranking. We keep this as a single query
    # rather than N+1ing per warehouse; the result set is bounded by the
    # employee roster size and easily fits in memory.
    perf_stmt: Select = (
        select(
            ProductivityEntry.warehouse_id,
            Employee.id,
            Employee.full_name,
            Employee.is_active,
            func.coalesce(func.sum(ProductivityEntry.pages), 0),
            func.coalesce(func.sum(ProductivityEntry.hours_worked), 0),
        )
        .join(Employee, Employee.id == ProductivityEntry.employee_id)
        .where(
            ProductivityEntry.entry_date >= start,
            ProductivityEntry.entry_date <= end,
            ProductivityEntry.excluded_from_metrics.is_(False),
            Employee.excluded_from_metrics.is_(False),
        )
        .group_by(
            ProductivityEntry.warehouse_id,
            Employee.id,
            Employee.full_name,
            Employee.is_active,
        )
    )
    if warehouse_ids:
        perf_stmt = perf_stmt.where(
            ProductivityEntry.warehouse_id.in_(list(warehouse_ids))
        )
    elif warehouse_ids is None:
        perf_stmt = perf_stmt.where(
            ProductivityEntry.warehouse_id.in_(
                select(Warehouse.id).where(Warehouse.is_active.is_(True))
            )
        )
    perf_stmt = _scope_to_warehouses(
        perf_stmt, user, ProductivityEntry.warehouse_id
    )
    perf_rows = db.execute(perf_stmt).all()

    # Active employee counts (independent of whether they logged anything
    # in the period -- "active employees" is a roster metric, so the
    # excluded-from-metrics override is intentionally ignored: those
    # employees are still on the floor, just not in the math).
    active_stmt: Select = (
        select(Employee.warehouse_id, func.count(Employee.id))
        .where(Employee.is_active.is_(True))
        .group_by(Employee.warehouse_id)
    )
    if warehouse_ids:
        active_stmt = active_stmt.where(
            Employee.warehouse_id.in_(list(warehouse_ids))
        )
    elif warehouse_ids is None:
        active_stmt = active_stmt.where(
            Employee.warehouse_id.in_(
                select(Warehouse.id).where(Warehouse.is_active.is_(True))
            )
        )
    active_stmt = _scope_to_warehouses(active_stmt, user, Employee.warehouse_id)
    active_rows = {
        int(wid): int(count) for wid, count in db.execute(active_stmt).all()
    }

    by_warehouse: dict[
        int, list[tuple[int, str, bool, int, float]]
    ] = {}
    for wid, emp_id, name, is_active, pages, hours in perf_rows:
        by_warehouse.setdefault(int(wid), []).append(
            (
                int(emp_id),
                name,
                bool(is_active),
                int(pages),
                float(hours or 0),
            )
        )

    out: dict[int, WarehouseProductivity] = {}
    for wid, total_pages, total_hours, entry_count in totals:
        wid_int = int(wid)
        pages_int = int(total_pages or 0)
        hours_float = float(total_hours or 0)
        top, bottom = _rank_performers(
            by_warehouse.get(wid_int, ()), n=top_n
        )
        out[wid_int] = WarehouseProductivity(
            warehouse_id=wid_int,
            total_pages=pages_int,
            total_hours=_round_hours(hours_float),
            avg_pages_per_day=_round_ppd(pages_int, hours_float),
            entry_count=int(entry_count or 0),
            active_employees=active_rows.get(wid_int, 0),
            top=top,
            bottom=bottom,
        )
    return out


def daily_summary(
    db: Session,
    *,
    user: User | None = None,
    warehouse_ids: Sequence[int] | None = None,
    on_date: date,
    top_n: int = 1,
) -> dict[int, WarehouseProductivity]:
    """Per-warehouse productivity for a single day."""
    return _summary_for_range(
        db,
        user=user,
        warehouse_ids=warehouse_ids,
        start=on_date,
        end=on_date,
        top_n=top_n,
    )


def week_bounds(any_day_in_week: date) -> tuple[date, date]:
    """ISO week containing ``any_day_in_week``: Mon (start) -> Sun (end)."""
    monday = any_day_in_week - timedelta(days=any_day_in_week.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def month_bounds(any_day_in_month: date) -> tuple[date, date]:
    """Calendar month containing ``any_day_in_month``."""
    first = any_day_in_month.replace(day=1)
    if first.month == 12:
        next_month = first.replace(year=first.year + 1, month=1)
    else:
        next_month = first.replace(month=first.month + 1)
    return first, next_month - timedelta(days=1)


def rolling_90_day_bounds(anchor: date) -> tuple[date, date]:
    """Inclusive 90-day reporting window ending on ``anchor``."""
    return anchor - timedelta(days=89), anchor


def weekly_summary(
    db: Session,
    *,
    user: User | None = None,
    warehouse_ids: Sequence[int] | None = None,
    week_start: date,
    top_n: int = 1,
) -> dict[int, WarehouseProductivity]:
    """Per-warehouse productivity for the ISO week starting on ``week_start``.

    Callers pass any date and we snap to the Monday of its week, so
    ``weekly_summary(week_start=date.today())`` Just Works regardless of
    the day the request lands on.
    """
    monday, sunday = week_bounds(week_start)
    return _summary_for_range(
        db,
        user=user,
        warehouse_ids=warehouse_ids,
        start=monday,
        end=sunday,
        top_n=top_n,
    )


def monthly_summary(
    db: Session,
    *,
    user: User | None = None,
    warehouse_ids: Sequence[int] | None = None,
    month: date,
    top_n: int = 1,
) -> dict[int, WarehouseProductivity]:
    """Per-warehouse productivity for the calendar month containing ``month``."""
    first, last = month_bounds(month)
    return _summary_for_range(
        db,
        user=user,
        warehouse_ids=warehouse_ids,
        start=first,
        end=last,
        top_n=top_n,
    )


def rolling_90_day_summary(
    db: Session,
    *,
    user: User | None = None,
    warehouse_ids: Sequence[int] | None = None,
    anchor: date,
    top_n: int = 1,
) -> dict[int, WarehouseProductivity]:
    """Per-warehouse productivity for the 90 days ending on ``anchor``."""
    first, last = rolling_90_day_bounds(anchor)
    return _summary_for_range(
        db,
        user=user,
        warehouse_ids=warehouse_ids,
        start=first,
        end=last,
        top_n=top_n,
    )


def _employee_period_rows(
    db: Session,
    *,
    user: User | None,
    warehouse_id: int,
    start: date,
    end: date,
) -> dict[int, tuple[int, float, int]]:
    stmt: Select = (
        select(
            Employee.id,
            func.coalesce(func.sum(ProductivityEntry.pages), 0),
            func.coalesce(func.sum(ProductivityEntry.hours_worked), 0),
            func.count(ProductivityEntry.id),
        )
        .join(ProductivityEntry, ProductivityEntry.employee_id == Employee.id)
        .where(
            Employee.warehouse_id == warehouse_id,
            ProductivityEntry.warehouse_id == warehouse_id,
            Employee.is_active.is_(True),
            Employee.excluded_from_metrics.is_(False),
            ProductivityEntry.entry_date >= start,
            ProductivityEntry.entry_date <= end,
            ProductivityEntry.excluded_from_metrics.is_(False),
        )
        .group_by(Employee.id)
    )
    stmt = _scope_to_warehouses(stmt, user, Employee.warehouse_id)
    return {
        int(employee_id): (
            int(pages or 0),
            float(hours or 0),
            int(entry_count or 0),
        )
        for employee_id, pages, hours, entry_count in db.execute(stmt).all()
    }


def _period_average(
    rows: dict[int, tuple[int, float, int]],
    *,
    employee_id: int,
    start: date,
    end: date,
    minimum: int | None,
    excluded: bool,
) -> EmployeePeriodAverage:
    pages, hours, entry_count = rows.get(employee_id, (0, 0.0, 0))
    has_data = entry_count > 0 and hours > 0 and not excluded
    pages_per_day = _round_ppd(pages, hours) if has_data else None
    below = (
        pages_per_day < minimum
        if pages_per_day is not None and minimum is not None
        else None
    )
    return EmployeePeriodAverage(
        period_start=start,
        period_end=end,
        total_pages=pages,
        total_hours=_round_hours(hours),
        entry_count=entry_count,
        pages_per_day=pages_per_day,
        below_minimum=below,
    )


def employee_averages(
    db: Session,
    *,
    user: User | None,
    warehouse_id: int,
    anchor: date,
    minimum: int | None,
) -> list[EmployeeAverages]:
    """Return all active roster rows with three comparable reporting periods."""
    roster_stmt: Select = select(Employee).where(
        Employee.warehouse_id == warehouse_id,
        Employee.is_active.is_(True),
    )
    roster_stmt = _scope_to_warehouses(roster_stmt, user, Employee.warehouse_id)
    employees = db.scalars(roster_stmt.order_by(Employee.full_name)).all()

    week_start, week_end = week_bounds(anchor)
    month_start, month_end = month_bounds(anchor)
    three_start, three_end = rolling_90_day_bounds(anchor)
    weekly_rows = _employee_period_rows(
        db,
        user=user,
        warehouse_id=warehouse_id,
        start=week_start,
        end=week_end,
    )
    monthly_rows = _employee_period_rows(
        db,
        user=user,
        warehouse_id=warehouse_id,
        start=month_start,
        end=month_end,
    )
    three_month_rows = _employee_period_rows(
        db,
        user=user,
        warehouse_id=warehouse_id,
        start=three_start,
        end=three_end,
    )

    output: list[EmployeeAverages] = []
    for employee in employees:
        excluded = bool(employee.excluded_from_metrics)
        weekly = _period_average(
            weekly_rows,
            employee_id=employee.id,
            start=week_start,
            end=week_end,
            minimum=minimum,
            excluded=excluded,
        )
        monthly = _period_average(
            monthly_rows,
            employee_id=employee.id,
            start=month_start,
            end=month_end,
            minimum=minimum,
            excluded=excluded,
        )
        three_month = _period_average(
            three_month_rows,
            employee_id=employee.id,
            start=three_start,
            end=three_end,
            minimum=minimum,
            excluded=excluded,
        )
        below_values = (
            weekly.below_minimum,
            monthly.below_minimum,
            three_month.below_minimum,
        )
        output.append(
            EmployeeAverages(
                employee_id=employee.id,
                employee_name=employee.full_name,
                excluded_from_metrics=excluded,
                weekly=weekly,
                monthly=monthly,
                three_month=three_month,
                consistently_below_minimum=all(
                    value is True for value in below_values
                ),
            )
        )
    return output


def empty_summary(warehouse_id: int) -> WarehouseProductivity:
    """Zero-valued summary for a warehouse with no entries in the period.

    The dashboard router and the email dispatcher both want a "did this
    warehouse log anything today?" answer in a single place; producing
    a zero ``WarehouseProductivity`` instead of ``None`` lets the
    response schemas stay non-optional everywhere.
    """
    return WarehouseProductivity(warehouse_id=warehouse_id)


__all__ = [
    "HOURS_PER_PRODUCTIVITY_DAY",
    "EmployeeAverages",
    "EmployeePeriodAverage",
    "Performer",
    "WarehouseProductivity",
    "daily_summary",
    "employee_averages",
    "empty_summary",
    "month_bounds",
    "monthly_summary",
    "rolling_90_day_bounds",
    "rolling_90_day_summary",
    "week_bounds",
    "weekly_summary",
]
