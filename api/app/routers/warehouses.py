from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin
from app.models.warehouses import Warehouse, WarehousePolicyEvent
from app.schemas.warehouses import (
    WarehouseCreate,
    WarehouseOut,
    WarehousePolicyEventOut,
    WarehouseUpdate,
)
from app.services.acl import apply_warehouse_filter
from app.services.warehouses import (
    WarehouseArchiveConflict,
    WarehouseRuleError,
    archive_warehouse,
    restore_warehouse,
)

router = APIRouter(prefix="/warehouses", tags=["warehouses"])


def _policy_snapshot(warehouse: Warehouse) -> dict[str, object]:
    return {
        "receipt_mode": warehouse.receipt_mode.value,
        "require_erp_document": warehouse.require_erp_document,
        "quarantine_imports": warehouse.quarantine_imports,
        "quarantine_manual_receipts": warehouse.quarantine_manual_receipts,
        "two_person_approval_threshold": warehouse.two_person_approval_threshold,
    }


@router.get("", response_model=list[WarehouseOut])
def list_warehouses(
    db: DbSession,
    user: CurrentUser,
    include_inactive: bool = False,
) -> list[WarehouseOut]:
    stmt = select(Warehouse)
    if not include_inactive:
        stmt = stmt.where(Warehouse.is_active.is_(True))
    stmt = apply_warehouse_filter(stmt, user, Warehouse.id).order_by(Warehouse.id)
    rows = db.scalars(stmt).all()
    return [WarehouseOut.model_validate(w) for w in rows]


@router.post("", response_model=WarehouseOut, status_code=201)
def create_warehouse(
    payload: WarehouseCreate,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> WarehouseOut:
    if payload.min_inventory >= payload.max_capacity:
        raise HTTPException(
            status_code=400,
            detail="min_inventory must be less than max_capacity",
        )
    wh = Warehouse(
        name=payload.name.strip(),
        min_inventory=payload.min_inventory,
        max_capacity=payload.max_capacity,
        min_pages_per_day=payload.min_pages_per_day,
        lead_time_days=payload.lead_time_days,
        safety_stock_percent=payload.safety_stock_percent,
        history_30_weight=payload.history_30_weight,
        history_90_weight=payload.history_90_weight,
        forecast_adjustment=payload.forecast_adjustment,
        receipt_mode=payload.receipt_mode,
        require_erp_document=payload.require_erp_document,
        quarantine_imports=payload.quarantine_imports,
        quarantine_manual_receipts=payload.quarantine_manual_receipts,
        two_person_approval_threshold=payload.two_person_approval_threshold,
    )
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return WarehouseOut.model_validate(wh)


@router.patch("/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> WarehouseOut:
    wh = db.get(Warehouse, warehouse_id)
    if wh is None:
        raise HTTPException(status_code=404, detail="warehouse not found")
    if not wh.is_active:
        raise HTTPException(status_code=409, detail="warehouse is archived")
    old_policy = _policy_snapshot(wh)
    next_min_inventory = (
        payload.min_inventory
        if payload.min_inventory is not None
        else wh.min_inventory
    )
    next_max_capacity = (
        payload.max_capacity
        if payload.max_capacity is not None
        else wh.max_capacity
    )
    next_history_30_weight = (
        payload.history_30_weight
        if payload.history_30_weight is not None
        else wh.history_30_weight
    )
    next_history_90_weight = (
        payload.history_90_weight
        if payload.history_90_weight is not None
        else wh.history_90_weight
    )
    if next_min_inventory >= next_max_capacity:
        raise HTTPException(
            status_code=400,
            detail="min_inventory must be less than max_capacity",
        )
    if next_history_30_weight + next_history_90_weight <= 0:
        raise HTTPException(
            status_code=400,
            detail="30-day and 90-day history weights cannot both be zero",
        )
    if payload.name is not None:
        wh.name = payload.name.strip()
    wh.min_inventory = next_min_inventory
    wh.max_capacity = next_max_capacity
    if "min_pages_per_day" in payload.model_fields_set:
        wh.min_pages_per_day = payload.min_pages_per_day
    if payload.lead_time_days is not None:
        wh.lead_time_days = payload.lead_time_days
    if payload.safety_stock_percent is not None:
        wh.safety_stock_percent = payload.safety_stock_percent
    wh.history_30_weight = next_history_30_weight
    wh.history_90_weight = next_history_90_weight
    if "forecast_adjustment" in payload.model_fields_set:
        wh.forecast_adjustment = payload.forecast_adjustment
    if payload.receipt_mode is not None:
        wh.receipt_mode = payload.receipt_mode
    if payload.require_erp_document is not None:
        wh.require_erp_document = payload.require_erp_document
    if payload.quarantine_imports is not None:
        wh.quarantine_imports = payload.quarantine_imports
    if payload.quarantine_manual_receipts is not None:
        wh.quarantine_manual_receipts = payload.quarantine_manual_receipts
    if "two_person_approval_threshold" in payload.model_fields_set:
        wh.two_person_approval_threshold = payload.two_person_approval_threshold
    new_policy = _policy_snapshot(wh)
    if old_policy != new_policy:
        db.add(
            WarehousePolicyEvent(
                warehouse_id=wh.id,
                changed_by_user_id=user.id,
                old_policy=old_policy,
                new_policy=new_policy,
            )
        )
    db.commit()
    db.refresh(wh)
    return WarehouseOut.model_validate(wh)


@router.get(
    "/{warehouse_id}/policy-events",
    response_model=list[WarehousePolicyEventOut],
)
def list_policy_events(
    warehouse_id: int,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> list[WarehousePolicyEventOut]:
    del user
    if db.get(Warehouse, warehouse_id) is None:
        raise HTTPException(status_code=404, detail="warehouse not found")
    events = db.scalars(
        select(WarehousePolicyEvent)
        .where(WarehousePolicyEvent.warehouse_id == warehouse_id)
        .order_by(
            WarehousePolicyEvent.changed_at.desc(),
            WarehousePolicyEvent.id.desc(),
        )
    ).all()
    return [WarehousePolicyEventOut.model_validate(event) for event in events]


@router.delete("/{warehouse_id}", response_model=WarehouseOut)
def delete_warehouse(
    warehouse_id: int,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> WarehouseOut:
    try:
        warehouse = archive_warehouse(
            db,
            warehouse_id=warehouse_id,
            user=user,
        )
    except WarehouseArchiveConflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except WarehouseRuleError as exc:
        status_code = 404 if "unknown warehouse_id" in str(exc) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return WarehouseOut.model_validate(warehouse)


@router.post("/{warehouse_id}/restore", response_model=WarehouseOut)
def restore_archived_warehouse(
    warehouse_id: int,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> WarehouseOut:
    del user
    try:
        warehouse = restore_warehouse(db, warehouse_id=warehouse_id)
    except WarehouseRuleError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WarehouseOut.model_validate(warehouse)
