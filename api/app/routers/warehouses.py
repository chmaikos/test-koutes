from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin
from app.models.warehouses import Warehouse
from app.schemas.warehouses import WarehouseCreate, WarehouseOut, WarehouseUpdate
from app.services.acl import apply_warehouse_filter
from app.services.warehouses import (
    WarehouseArchiveConflict,
    WarehouseRuleError,
    archive_warehouse,
    restore_warehouse,
)

router = APIRouter(prefix="/warehouses", tags=["warehouses"])


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
    if payload.name is not None:
        wh.name = payload.name
    if payload.min_inventory is not None:
        wh.min_inventory = payload.min_inventory
    if payload.max_capacity is not None:
        wh.max_capacity = payload.max_capacity
    if "min_pages_per_day" in payload.model_fields_set:
        wh.min_pages_per_day = payload.min_pages_per_day
    if wh.min_inventory >= wh.max_capacity:
        raise HTTPException(
            status_code=400,
            detail="min_inventory must be less than max_capacity",
        )
    db.commit()
    db.refresh(wh)
    return WarehouseOut.model_validate(wh)


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
