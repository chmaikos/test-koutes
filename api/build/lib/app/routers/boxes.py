from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession, require_operator
from app.events import bus
from app.models.boxes import Box, BoxEvent
from app.routers._filters import BoxFilters, apply_box_filters, parse_box_filters
from app.schemas.boxes import BoxCreate, BoxEventOut, BoxOut, BoxUpdate
from app.schemas.common import Page
from app.services.alerts import evaluate_safe
from app.services.boxes import create_box, update_box

router = APIRouter(prefix="/boxes", tags=["boxes"])


@router.get("", response_model=Page[BoxOut])
def list_boxes(
    db: DbSession,
    user: CurrentUser,
    filters: Annotated[BoxFilters, Depends(parse_box_filters)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[BoxOut]:
    stmt = select(Box)
    stmt = apply_box_filters(stmt, filters).order_by(Box.updated_at.desc())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return Page[BoxOut](
        items=[BoxOut.model_validate(b) for b in items],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=BoxOut, status_code=status.HTTP_201_CREATED)
async def create_new_box(
    payload: BoxCreate,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_operator)],
) -> BoxOut:
    box = create_box(
        db,
        user=user,
        box_number=payload.box_number,
        owner=payload.owner,
        warehouse_id=payload.warehouse_id,
        note=payload.note,
    )
    await bus.publish(
        "box.updated",
        {"id": box.id, "warehouse_id": box.current_warehouse_id, "status": box.status.value},
    )
    background.add_task(evaluate_safe, db)
    return BoxOut.model_validate(box)


@router.get("/{box_id}", response_model=BoxOut)
def get_box(box_id: int, db: DbSession, user: CurrentUser) -> BoxOut:
    box = db.get(Box, box_id)
    if box is None:
        raise HTTPException(status_code=404, detail="not found")
    return BoxOut.model_validate(box)


@router.patch("/{box_id}", response_model=BoxOut)
async def patch_box(
    box_id: int,
    payload: BoxUpdate,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_operator)],
) -> BoxOut:
    box = db.get(Box, box_id)
    if box is None:
        raise HTTPException(status_code=404, detail="not found")
    box = update_box(
        db,
        user=user,
        box=box,
        new_status=payload.status,
        new_warehouse_id=payload.warehouse_id,
        new_owner=payload.owner,
        note=payload.note,
    )
    await bus.publish(
        "box.updated",
        {"id": box.id, "warehouse_id": box.current_warehouse_id, "status": box.status.value},
    )
    background.add_task(evaluate_safe, db)
    return BoxOut.model_validate(box)


@router.get("/{box_id}/events", response_model=list[BoxEventOut])
def list_box_events(
    box_id: int, db: DbSession, user: CurrentUser
) -> list[BoxEventOut]:
    if db.get(Box, box_id) is None:
        raise HTTPException(status_code=404, detail="not found")
    events = db.scalars(
        select(BoxEvent)
        .where(BoxEvent.box_id == box_id)
        .order_by(BoxEvent.occurred_at.desc(), BoxEvent.id.desc())
    ).all()
    return [BoxEventOut.model_validate(e) for e in events]
