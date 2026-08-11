from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession, require_admin, require_operator
from app.events import bus
from app.models.boxes import Box, BoxStatus
from app.models.lots import Lot, LotEvent
from app.models.users import User
from app.models.warehouses import Warehouse
from app.routers._filters import (
    BoxFilters,
    BoxSort,
    apply_box_filters,
    apply_box_sort,
    parse_box_sort,
)
from app.schemas.boxes import BoxOut
from app.schemas.common import Page
from app.schemas.lots import (
    LotCreate,
    LotDetailOut,
    LotEventOut,
    LotOptionOut,
    LotProgressState,
    LotRename,
    LotSortField,
    LotSummaryOut,
)
from app.services.acl import apply_warehouse_filter
from app.services.lots import (
    LotAccessError,
    LotConflictError,
    LotNotFoundError,
    LotRuleError,
    LotVersionConflictError,
    get_or_create_lot_result,
    get_visible_lot_summary,
    list_lot_options,
    list_lot_summaries,
    lot_warehouse_ids,
    rename_lot,
)

router = APIRouter(prefix="/lots", tags=["lots"])


def _parse_lot_box_filters(
    warehouse_id: Annotated[int | None, Query(ge=1)] = None,
    status_filter: Annotated[BoxStatus | None, Query(alias="status")] = None,
    search: str | None = None,
    received_from: datetime | None = None,
    received_to: datetime | None = None,
    updated_from: datetime | None = None,
    updated_to: datetime | None = None,
) -> BoxFilters:
    return BoxFilters(
        warehouse_id=warehouse_id,
        status=status_filter,
        search=search,
        received_from=received_from,
        received_to=received_to,
        updated_from=updated_from,
        updated_to=updated_to,
    )


def _summary_out(summary) -> LotSummaryOut:
    return LotSummaryOut.model_validate(summary, from_attributes=True)


def _lot_error(exc: LotRuleError) -> HTTPException:
    if isinstance(exc, LotAccessError):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, LotNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, LotConflictError):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


async def _publish_for_lot(
    db: DbSession,
    event_type: str,
    lot_id: int,
    data: dict[str, object],
) -> None:
    warehouse_ids = lot_warehouse_ids(db, lot_id)
    if not warehouse_ids:
        await bus.publish(event_type, {**data, "warehouse_id": None})
        return
    for warehouse_id in warehouse_ids:
        await bus.publish(
            event_type,
            {**data, "warehouse_id": warehouse_id},
        )


@router.get("", response_model=Page[LotSummaryOut])
def list_lots(
    db: DbSession,
    user: CurrentUser,
    search: str | None = None,
    warehouse_id: int | None = Query(default=None, ge=1),
    progress_state: LotProgressState | None = None,
    sort_by: LotSortField = "last_activity",
    sort_dir: Literal["asc", "desc"] = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[LotSummaryOut]:
    rows, total = list_lot_summaries(
        db,
        user=user,
        search=search,
        warehouse_id=warehouse_id,
        progress_state=progress_state,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )
    return Page[LotSummaryOut](
        items=[_summary_out(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/options", response_model=Page[LotOptionOut])
def options(
    db: DbSession,
    user: CurrentUser,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=200),
) -> Page[LotOptionOut]:
    try:
        rows, total = list_lot_options(
            db,
            user=user,
            search=search,
            page=page,
            limit=limit,
        )
    except LotRuleError as exc:
        raise _lot_error(exc) from exc
    return Page[LotOptionOut](
        items=[
            LotOptionOut.model_validate(row, from_attributes=True) for row in rows
        ],
        total=total,
        page=page,
        page_size=limit,
    )


@router.post("", response_model=LotSummaryOut, status_code=status.HTTP_201_CREATED)
async def create_lot(
    payload: LotCreate,
    response: Response,
    db: DbSession,
    user: Annotated[User, Depends(require_operator)],
) -> LotSummaryOut:
    try:
        warehouse = db.get(Warehouse, payload.warehouse_id)
        if warehouse is None or not warehouse.is_active:
            raise LotRuleError("warehouse not found or archived")
        lot, created = get_or_create_lot_result(
            db,
            user=user,
            name=payload.name,
            warehouse_id=payload.warehouse_id,
        )
        db.commit()
        db.refresh(lot)
        summary = get_visible_lot_summary(
            db,
            user=user,
            lot_id=lot.id,
            allow_unrepresented=True,
        )
    except LotRuleError as exc:
        db.rollback()
        raise _lot_error(exc) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    else:
        await bus.publish(
            "lot.created",
            {
                "id": lot.id,
                "name": lot.name,
                "version": lot.version,
                "warehouse_id": payload.warehouse_id,
            },
        )
    return _summary_out(summary)


@router.get("/{lot_id}", response_model=LotDetailOut)
def get_lot_detail(
    lot_id: int,
    db: DbSession,
    user: CurrentUser,
) -> LotDetailOut:
    try:
        summary = get_visible_lot_summary(db, user=user, lot_id=lot_id)
    except LotRuleError as exc:
        raise _lot_error(exc) from exc
    history: list[LotEventOut] = []
    include_history = user.role.value == "admin"
    if include_history:
        events = db.scalars(
            select(LotEvent)
            .where(LotEvent.lot_id == lot_id)
            .order_by(LotEvent.occurred_at.desc(), LotEvent.id.desc())
        ).all()
        history = [LotEventOut.model_validate(event) for event in events]
    return LotDetailOut(
        **_summary_out(summary).model_dump(),
        audit_history=history,
        audit_history_included=include_history,
    )


@router.get("/{lot_id}/boxes", response_model=Page[BoxOut])
def list_lot_boxes(
    lot_id: int,
    db: DbSession,
    user: CurrentUser,
    filters: Annotated[BoxFilters, Depends(_parse_lot_box_filters)],
    sort: Annotated[BoxSort, Depends(parse_box_sort)],
    include_archived: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[BoxOut]:
    try:
        get_visible_lot_summary(db, user=user, lot_id=lot_id)
    except LotRuleError as exc:
        raise _lot_error(exc) from exc
    if include_archived and user.role.value != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="archived lot boxes require admin role",
        )
    stmt = select(Box).where(Box.lot_id == lot_id)
    stmt = apply_box_filters(stmt, filters, include_archived=include_archived)
    stmt = apply_warehouse_filter(stmt, user, Box.current_warehouse_id)
    stmt = apply_box_sort(stmt, sort)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    boxes = db.scalars(
        stmt.offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page[BoxOut](
        items=[BoxOut.model_validate(box) for box in boxes],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/{lot_id}/rename", response_model=LotSummaryOut)
async def rename(
    lot_id: int,
    payload: LotRename,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
) -> LotSummaryOut:
    try:
        lot = rename_lot(
            db,
            user=user,
            lot_id=lot_id,
            new_name=payload.new_name,
            reason=payload.reason,
            expected_version=payload.expected_version,
        )
    except (LotVersionConflictError, LotConflictError) as exc:
        db.rollback()
        current = db.get(Lot, lot_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": (
                    "version_conflict"
                    if isinstance(exc, LotVersionConflictError)
                    else "name_collision"
                ),
                "message": str(exc),
                "current": (
                    {
                        "id": current.id,
                        "name": current.name,
                        "normalized_name": current.normalized_name,
                        "version": current.version,
                        "updated_at": current.updated_at.isoformat(),
                    }
                    if current is not None
                    else None
                ),
            },
        ) from exc
    except LotRuleError as exc:
        db.rollback()
        raise _lot_error(exc) from exc
    summary = get_visible_lot_summary(db, user=user, lot_id=lot.id)
    await _publish_for_lot(
        db,
        "lot.renamed",
        lot.id,
        {"id": lot.id, "name": lot.name, "version": lot.version},
    )
    return _summary_out(summary)


__all__ = ["router"]
