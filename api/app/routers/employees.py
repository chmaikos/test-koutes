from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin
from app.models.employees import Employee
from app.models.warehouses import Warehouse
from app.schemas.employees import EmployeeCreate, EmployeeOut, EmployeeUpdate
from app.services.acl import apply_warehouse_filter, can_access

router = APIRouter(prefix="/employees", tags=["employees"])


def _ensure_warehouse(db: DbSession, warehouse_id: int) -> Warehouse:
    wh = db.get(Warehouse, warehouse_id)
    if wh is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown warehouse_id: {warehouse_id}",
        )
    return wh


@router.get("", response_model=list[EmployeeOut])
def list_employees(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    include_inactive: bool = Query(default=False),
) -> list[EmployeeOut]:
    """List employees scoped to the caller's warehouse ACL.

    Operators and viewers see employees in the warehouses they have an
    explicit ACL row for; admins see everyone. ``include_inactive``
    surfaces soft-deleted employees so an admin can reactivate them.
    """
    stmt = select(Employee)
    if warehouse_id is not None:
        stmt = stmt.where(Employee.warehouse_id == warehouse_id)
    if not include_inactive:
        stmt = stmt.where(Employee.is_active.is_(True))
    stmt = apply_warehouse_filter(stmt, user, Employee.warehouse_id).order_by(
        Employee.full_name
    )
    rows = db.scalars(stmt).all()
    return [EmployeeOut.model_validate(e) for e in rows]


@router.post("", response_model=EmployeeOut, status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: EmployeeCreate,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> EmployeeOut:
    _ensure_warehouse(db, payload.warehouse_id)
    emp = Employee(
        warehouse_id=payload.warehouse_id,
        full_name=payload.full_name.strip(),
        email=(payload.email or "").strip() or None,
        default_hours_per_day=payload.default_hours_per_day,
        is_active=True,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return EmployeeOut.model_validate(emp)


@router.patch("/{employee_id}", response_model=EmployeeOut)
def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> EmployeeOut:
    emp = db.get(Employee, employee_id)
    if emp is None:
        raise HTTPException(status_code=404, detail="employee not found")
    if payload.warehouse_id is not None and payload.warehouse_id != emp.warehouse_id:
        _ensure_warehouse(db, payload.warehouse_id)
        emp.warehouse_id = payload.warehouse_id
    if payload.full_name is not None:
        emp.full_name = payload.full_name.strip()
    if payload.email is not None:
        emp.email = payload.email.strip() or None
    if payload.default_hours_per_day is not None:
        emp.default_hours_per_day = payload.default_hours_per_day
    if payload.is_active is not None:
        emp.is_active = payload.is_active
    db.commit()
    db.refresh(emp)
    return EmployeeOut.model_validate(emp)


@router.delete(
    "/{employee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_employee(
    employee_id: int,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> Response:
    """Soft-delete: flip ``is_active`` to ``false``.

    A hard delete would cascade to ``productivity_entries`` and erase the
    historical record an offboarded employee leaves behind, which is the
    opposite of what admins usually want when cleaning up the roster.
    Admins who really want a hard delete can do it from the database
    directly; the API intentionally does not expose that path.
    """
    emp = db.get(Employee, employee_id)
    if emp is None:
        raise HTTPException(status_code=404, detail="employee not found")
    # ACL parity with the rest of the API: even admins are blocked from
    # touching warehouses outside their (always-empty for admins) filter,
    # which is a no-op today but stays consistent if the rule is tightened.
    if not can_access(user, emp.warehouse_id):
        raise HTTPException(status_code=404, detail="employee not found")
    emp.is_active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
