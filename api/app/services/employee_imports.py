from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employees import Employee
from app.services.warehouses import WarehouseRuleError, ensure_active_warehouse


class EmployeeImportError(ValueError):
    pass


@dataclass(frozen=True)
class EmployeeImportRow:
    source_row: int
    full_name: str
    default_hours_per_day: Decimal | None = None
    is_active: bool | None = None
    excluded_from_metrics: bool | None = None


@dataclass(frozen=True)
class EmployeeImportSkipEntry:
    row: int
    full_name: str
    reason: str


@dataclass
class EmployeeImportOutcome:
    created: list[Employee] = field(default_factory=list)
    updated: list[Employee] = field(default_factory=list)
    skipped: list[EmployeeImportSkipEntry] = field(default_factory=list)


def _name_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def import_employees(
    db: Session,
    *,
    warehouse_id: int,
    rows: list[EmployeeImportRow],
) -> EmployeeImportOutcome:
    """Create or update mapped employees in one transaction."""
    try:
        ensure_active_warehouse(db, warehouse_id)
    except WarehouseRuleError as exc:
        raise EmployeeImportError(str(exc)) from exc

    existing = db.scalars(
        select(Employee).where(Employee.warehouse_id == warehouse_id)
    ).all()
    by_name: dict[str, list[Employee]] = {}
    for employee in existing:
        by_name.setdefault(_name_key(employee.full_name), []).append(employee)

    outcome = EmployeeImportOutcome()
    processed: set[str] = set()
    for row in rows:
        full_name = " ".join(row.full_name.split())
        key = _name_key(full_name)
        if not key:
            outcome.skipped.append(
                EmployeeImportSkipEntry(
                    row=row.source_row,
                    full_name=row.full_name,
                    reason="employee name is empty",
                )
            )
            continue
        if key in processed:
            outcome.skipped.append(
                EmployeeImportSkipEntry(
                    row=row.source_row,
                    full_name=full_name,
                    reason="duplicate employee in workbook",
                )
            )
            continue
        processed.add(key)

        matches = by_name.get(key, [])
        if len(matches) > 1:
            outcome.skipped.append(
                EmployeeImportSkipEntry(
                    row=row.source_row,
                    full_name=full_name,
                    reason="multiple existing employees have this name",
                )
            )
            continue

        if matches:
            employee = matches[0]
            employee.full_name = full_name
            if row.default_hours_per_day is not None:
                employee.default_hours_per_day = row.default_hours_per_day
            if row.is_active is not None:
                employee.is_active = row.is_active
            if row.excluded_from_metrics is not None:
                employee.excluded_from_metrics = row.excluded_from_metrics
            outcome.updated.append(employee)
            continue

        employee = Employee(
            warehouse_id=warehouse_id,
            full_name=full_name,
            default_hours_per_day=row.default_hours_per_day or Decimal("8.00"),
            is_active=row.is_active if row.is_active is not None else True,
            excluded_from_metrics=(
                row.excluded_from_metrics
                if row.excluded_from_metrics is not None
                else False
            ),
        )
        db.add(employee)
        by_name[key] = [employee]
        outcome.created.append(employee)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    for employee in outcome.created + outcome.updated:
        db.refresh(employee)
    return outcome


__all__ = [
    "EmployeeImportError",
    "EmployeeImportOutcome",
    "EmployeeImportRow",
    "import_employees",
]
