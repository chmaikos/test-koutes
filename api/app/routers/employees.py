from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession, require_admin
from app.models.employees import Employee
from app.models.warehouses import Warehouse
from app.schemas.employees import (
    EmployeeCreate,
    EmployeeImportRequest,
    EmployeeImportResult,
    EmployeeImportSkip,
    EmployeeOut,
    EmployeePage,
    EmployeeUpdate,
)
from app.schemas.requests import XlsxPreviewOut, XlsxPreviewRow, XlsxPreviewSheet
from app.services.acl import apply_warehouse_filter, can_access
from app.services.boxes import BoxRuleError
from app.services.employee_imports import (
    EmployeeImportError,
    EmployeeImportRow,
    import_employees,
)
from app.services.warehouses import WarehouseRuleError, ensure_active_warehouse
from app.services.xlsx_preview import MAX_XLSX_BYTES, preview_xlsx

router = APIRouter(prefix="/employees", tags=["employees"])


def _ensure_warehouse(db: DbSession, warehouse_id: int) -> Warehouse:
    try:
        return ensure_active_warehouse(db, warehouse_id)
    except WarehouseRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("", response_model=EmployeePage)
def list_employees(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    include_inactive: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> EmployeePage:
    """List employees scoped to the caller's warehouse ACL.

    Operators and viewers see employees in the warehouses they have an
    explicit ACL row for; admins see everyone. ``include_inactive``
    surfaces soft-deleted employees so an admin can reactivate them.
    The response is paginated -- the SPA's Settings page reads the
    headline page, while dropdown callers (NewEntryForm, EntriesList
    name resolution) pass the max ``page_size`` to fetch the whole
    roster in a single request.
    """
    stmt = select(Employee)
    if warehouse_id is not None:
        stmt = stmt.where(Employee.warehouse_id == warehouse_id)
    if not include_inactive:
        stmt = stmt.where(Employee.is_active.is_(True))
    stmt = apply_warehouse_filter(stmt, user, Employee.warehouse_id)
    # Count BEFORE pagination so ``total`` reflects every row that
    # matches the filters, not just the current page.
    total = int(
        db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    )
    rows = db.scalars(
        stmt.order_by(Employee.full_name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return EmployeePage(
        items=[EmployeeOut.model_validate(e) for e in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/import-preview", response_model=XlsxPreviewOut)
async def preview_employee_import(
    user: Annotated[CurrentUser, Depends(require_admin)],
    file: Annotated[UploadFile, File(description="An XLSX employee workbook")],
) -> XlsxPreviewOut:
    del user
    filename = file.filename or "employees.xlsx"
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="only .xlsx files are supported")
    payload = await file.read(MAX_XLSX_BYTES + 1)
    try:
        sheets = preview_xlsx(payload)
    except BoxRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return XlsxPreviewOut(
        filename=filename,
        sheets=[
            XlsxPreviewSheet(
                name=sheet.name,
                max_columns=sheet.max_columns,
                rows=[
                    XlsxPreviewRow(row_number=row.row_number, cells=row.cells)
                    for row in sheet.rows
                ],
            )
            for sheet in sheets
        ],
    )


@router.post("/import-mapped", response_model=EmployeeImportResult)
def import_mapped_employees(
    payload: EmployeeImportRequest,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> EmployeeImportResult:
    del user
    try:
        outcome = import_employees(
            db,
            warehouse_id=payload.warehouse_id,
            rows=[
                EmployeeImportRow(
                    source_row=item.source_row,
                    full_name=item.full_name,
                    default_hours_per_day=item.default_hours_per_day,
                    is_active=item.is_active,
                    excluded_from_metrics=item.excluded_from_metrics,
                )
                for item in payload.items
            ],
        )
    except EmployeeImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EmployeeImportResult(
        created=[EmployeeOut.model_validate(item) for item in outcome.created],
        updated=[EmployeeOut.model_validate(item) for item in outcome.updated],
        skipped=[
            EmployeeImportSkip(
                row=item.row,
                full_name=item.full_name,
                reason=item.reason,
            )
            for item in outcome.skipped
        ],
    )


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
        default_hours_per_day=payload.default_hours_per_day,
        is_active=True,
        excluded_from_metrics=payload.excluded_from_metrics,
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
    _ensure_warehouse(db, emp.warehouse_id)
    if payload.warehouse_id is not None and payload.warehouse_id != emp.warehouse_id:
        _ensure_warehouse(db, payload.warehouse_id)
        emp.warehouse_id = payload.warehouse_id
    if payload.full_name is not None:
        emp.full_name = payload.full_name.strip()
    if payload.default_hours_per_day is not None:
        emp.default_hours_per_day = payload.default_hours_per_day
    if payload.is_active is not None:
        emp.is_active = payload.is_active
    if payload.excluded_from_metrics is not None:
        emp.excluded_from_metrics = payload.excluded_from_metrics
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
    _ensure_warehouse(db, emp.warehouse_id)
    emp.is_active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
