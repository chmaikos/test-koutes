from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession, require_admin, require_operator
from app.events import bus
from app.models.boxes import Box, BoxEvent
from app.models.users import User, UserRole
from app.routers._filters import (
    BoxFilters,
    BoxSort,
    apply_box_filters,
    apply_box_sort,
    parse_box_filters,
    parse_box_sort,
)
from app.schemas.boxes import (
    BoxCreate,
    BoxEventOut,
    BoxOut,
    BoxUpdate,
    BulkBoxUpdate,
    BulkDeleteRequest,
    BulkDeleteResult,
    BulkResult,
    BulkSkip,
)
from app.schemas.common import Page
from app.services.acl import apply_warehouse_filter, can_access
from app.services.alerts import evaluate_safe
from app.services.boxes import (
    BoxAccessError,
    BoxConflictError,
    BoxRuleError,
    bulk_delete_boxes,
    bulk_update_boxes,
    create_box,
    delete_box,
    update_box,
)

router = APIRouter(prefix="/boxes", tags=["boxes"])


def _rule_error_to_http(exc: BoxRuleError) -> HTTPException:
    if isinstance(exc, BoxAccessError):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, BoxConflictError):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


def _check_force(payload_force: bool, user: User) -> None:
    """Force-override is admin-only; refuse with 403 otherwise."""
    if payload_force and user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="force override requires admin role",
        )


@router.get("", response_model=Page[BoxOut])
def list_boxes(
    db: DbSession,
    user: CurrentUser,
    filters: Annotated[BoxFilters, Depends(parse_box_filters)],
    sort: Annotated[BoxSort, Depends(parse_box_sort)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[BoxOut]:
    stmt = select(Box)
    stmt = apply_box_filters(stmt, filters)
    stmt = apply_warehouse_filter(stmt, user, Box.current_warehouse_id)
    stmt = apply_box_sort(stmt, sort)
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
    try:
        box = create_box(
            db,
            user=user,
            box_number=payload.box_number,
            lot=payload.lot,
            contents=payload.contents,
            warehouse_id=payload.warehouse_id,
            note=payload.note,
        )
    except BoxRuleError as exc:
        raise _rule_error_to_http(exc) from exc
    await bus.publish(
        "box.updated",
        {"id": box.id, "warehouse_id": box.current_warehouse_id, "status": box.status.value},
    )
    background.add_task(evaluate_safe, db)
    return BoxOut.model_validate(box)


@router.post("/bulk", response_model=BulkResult)
async def bulk_update(
    payload: BulkBoxUpdate,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_operator)],
) -> BulkResult:
    if payload.warehouse_id is None and payload.status is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="must specify warehouse_id or status",
        )
    _check_force(payload.force, user)
    try:
        outcome = bulk_update_boxes(
            db,
            user=user,
            box_ids=payload.box_ids,
            new_warehouse_id=payload.warehouse_id,
            new_status=payload.status,
            note=payload.note,
            force=payload.force,
        )
    except BoxRuleError as exc:
        raise _rule_error_to_http(exc) from exc

    # Single SSE event per affected warehouse keeps the dashboard live without
    # spamming subscribers with hundreds of identical box.updated messages.
    affected_warehouses = {b.current_warehouse_id for b in outcome.updated}
    for wid in affected_warehouses:
        await bus.publish("box.updated", {"warehouse_id": wid, "bulk": True})
    if outcome.updated:
        background.add_task(evaluate_safe, db)

    return BulkResult(
        updated=[BoxOut.model_validate(b) for b in outcome.updated],
        skipped=[
            BulkSkip(box_id=s.box_id, box_number=s.box_number, reason=s.reason)
            for s in outcome.skipped
        ],
    )


@router.get("/{box_id}", response_model=BoxOut)
def get_box(box_id: int, db: DbSession, user: CurrentUser) -> BoxOut:
    box = db.get(Box, box_id)
    # 404 (not 403) when the caller can't see this warehouse, so we don't
    # leak the existence of boxes outside their ACL.
    if box is None or not can_access(user, box.current_warehouse_id):
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
    if box is None or not can_access(user, box.current_warehouse_id):
        raise HTTPException(status_code=404, detail="not found")
    _check_force(payload.force, user)
    try:
        box = update_box(
            db,
            user=user,
            box=box,
            new_status=payload.status,
            new_warehouse_id=payload.warehouse_id,
            new_lot=payload.lot,
            new_contents=payload.contents,
            note=payload.note,
            force=payload.force,
        )
    except BoxRuleError as exc:
        raise _rule_error_to_http(exc) from exc
    await bus.publish(
        "box.updated",
        {"id": box.id, "warehouse_id": box.current_warehouse_id, "status": box.status.value},
    )
    background.add_task(evaluate_safe, db)
    return BoxOut.model_validate(box)


@router.delete(
    "/{box_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_single_box(
    box_id: int,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> Response:
    box = db.get(Box, box_id)
    # require_admin already gates this, but keep ACL consistent in case
    # the dependency is ever loosened.
    if box is None or not can_access(user, box.current_warehouse_id):
        raise HTTPException(status_code=404, detail="not found")
    warehouse_id = delete_box(db, box=box)
    await bus.publish("box.deleted", {"id": box_id, "warehouse_id": warehouse_id})
    background.add_task(evaluate_safe, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/bulk-delete", response_model=BulkDeleteResult)
async def bulk_delete(
    payload: BulkDeleteRequest,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> BulkDeleteResult:
    outcome = bulk_delete_boxes(db, box_ids=payload.box_ids)
    for wid in outcome.affected_warehouse_ids:
        await bus.publish("box.deleted", {"warehouse_id": wid, "bulk": True})
    if outcome.deleted_ids:
        background.add_task(evaluate_safe, db)
    return BulkDeleteResult(
        deleted_ids=outcome.deleted_ids,
        skipped=[
            BulkSkip(box_id=s.box_id, box_number=s.box_number, reason=s.reason)
            for s in outcome.skipped
        ],
    )


@router.get("/{box_id}/events", response_model=list[BoxEventOut])
def list_box_events(
    box_id: int, db: DbSession, user: CurrentUser
) -> list[BoxEventOut]:
    box = db.get(Box, box_id)
    if box is None or not can_access(user, box.current_warehouse_id):
        raise HTTPException(status_code=404, detail="not found")
    events = db.scalars(
        select(BoxEvent)
        .where(BoxEvent.box_id == box_id)
        .order_by(BoxEvent.occurred_at.desc(), BoxEvent.id.desc())
    ).all()
    return [BoxEventOut.model_validate(e) for e in events]
