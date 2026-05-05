from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin
from app.models.warehouses import Warehouse
from app.schemas.warehouses import WarehouseCreate, WarehouseOut, WarehouseUpdate

router = APIRouter(prefix="/warehouses", tags=["warehouses"])


@router.get("", response_model=list[WarehouseOut])
def list_warehouses(db: DbSession, user: CurrentUser) -> list[WarehouseOut]:
    rows = db.scalars(select(Warehouse).order_by(Warehouse.id)).all()
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
    if payload.name is not None:
        wh.name = payload.name
    if payload.min_inventory is not None:
        wh.min_inventory = payload.min_inventory
    if payload.max_capacity is not None:
        wh.max_capacity = payload.max_capacity
    if wh.min_inventory >= wh.max_capacity:
        raise HTTPException(
            status_code=400,
            detail="min_inventory must be less than max_capacity",
        )
    db.commit()
    db.refresh(wh)
    return WarehouseOut.model_validate(wh)
