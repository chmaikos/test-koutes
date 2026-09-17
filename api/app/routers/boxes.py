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
from app.events import bus, publish_notification_event
from app.models.boxes import Box, BoxEvent, BoxStatus
from app.models.notifications import RequestNotificationKind
from app.models.requests import BoxRequest, BoxRequestItem, BoxRequestOrigin
from app.models.users import User, UserRole
from app.models.warehouses import Warehouse
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
    BoxDeleteRequest,
    BoxDeleteResult,
    BoxEventOut,
    BoxOut,
    BoxUpdate,
    BoxUpdateResult,
    BulkBoxUpdate,
    BulkDeleteRequest,
    BulkDeleteResult,
    BulkResult,
    BulkSkip,
    QuarantineBulkAction,
    QuarantineBulkResult,
    StagedReceiptResult,
)
from app.schemas.common import Page
from app.schemas.lots import BoxLotReassignment
from app.schemas.requests import InboundBoxItem
from app.services.acl import apply_warehouse_filter, can_access
from app.services.alerts import evaluate_safe
from app.services.boxes import (
    BoxAccessError,
    BoxConflictError,
    BoxRuleError,
    DeleteOutcome,
    bulk_delete_boxes,
    bulk_update_boxes,
    create_box,
    delete_box,
    reassign_box_lot,
    update_box,
)
from app.services.lots import LotRuleError, resolve_lot
from app.services.request_notifications import enqueue_request_event
from app.services.requests import (
    RequestAccessError,
    RequestRuleError,
    create_completed_receipt,
    create_staged_receipt,
    receipt_requires_review,
)

router = APIRouter(prefix="/boxes", tags=["boxes"])


def _rule_error_to_http(exc: BoxRuleError | RequestRuleError) -> HTTPException:
    if isinstance(exc, (BoxAccessError, RequestAccessError)):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, BoxConflictError):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


async def _publish_request_update(db: DbSession, request_id: int) -> None:
    request = db.get(BoxRequest, request_id)
    if request is None:
        return
    await bus.publish(
        "request.updated",
        {
            "id": request.id,
            "warehouse_id": request.warehouse_id,
            "direction": request.direction.value,
            "status": request.status.value,
        },
    )


def _check_force(payload_force: bool, user: User, reason: str | None = None) -> None:
    """Force-override is admin-only; refuse with 403 otherwise."""
    if payload_force and user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="force override requires admin role",
        )
    if payload_force and not (reason or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="a reason is required for an admin override",
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


@router.post(
    "",
    response_model=BoxOut | StagedReceiptResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_new_box(
    payload: BoxCreate,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_operator)],
) -> BoxOut | StagedReceiptResult:
    try:
        warehouse = db.get(Warehouse, payload.warehouse_id)
        if warehouse is None or not warehouse.is_active:
            raise BoxRuleError("warehouse not found or archived")
        if receipt_requires_review(
            warehouse,
            BoxRequestOrigin.manual_entry,
            quantity=1,
        ):
            try:
                lot_record = resolve_lot(
                    db,
                    user=user,
                    lot=payload.lot,
                    lot_id=payload.lot_id,
                    warehouse_id=payload.warehouse_id,
                )
            except LotRuleError as exc:
                raise BoxRuleError(str(exc)) from exc
            staged = create_staged_receipt(
                db,
                user=user,
                warehouse_id=payload.warehouse_id,
                items=[
                    InboundBoxItem(
                        box_number=payload.box_number,
                        lot=lot_record.name,
                        pallet_number=payload.pallet_number,
                        pallet_id=payload.pallet_id,
                        contents=payload.contents,
                        files=payload.files,
                    )
                ],
                origin=BoxRequestOrigin.manual_entry,
            )
            await bus.publish(
                "request.created",
                {
                    "id": staged.id,
                    "warehouse_id": staged.warehouse_id,
                    "staged": True,
                },
            )
            await publish_notification_event(
                warehouse_id=staged.warehouse_id,
                request_id=staged.id,
            )
            return StagedReceiptResult(staged_receipt_id=staged.id)
        box = create_box(
            db,
            user=user,
            box_number=payload.box_number,
            lot=payload.lot,
            lot_id=payload.lot_id,
            pallet_number=payload.pallet_number,
            pallet_id=payload.pallet_id,
            contents=payload.contents,
            files=payload.files,
            warehouse_id=payload.warehouse_id,
            note=payload.note,
            commit=False,
        )
        receipt = create_completed_receipt(
            db,
            user=user,
            warehouse_id=payload.warehouse_id,
            boxes=[box],
            origin=BoxRequestOrigin.manual_entry,
            note="Completed automatically from manual box entry.",
        )
    except (BoxRuleError, RequestRuleError) as exc:
        db.rollback()
        raise _rule_error_to_http(exc) from exc
    await bus.publish(
        "box.updated",
        {"id": box.id, "warehouse_id": box.current_warehouse_id, "status": box.status.value},
    )
    if box.pallet_id is not None:
        await bus.publish(
            "pallet.updated",
            {"id": box.pallet_id, "warehouse_id": box.current_warehouse_id},
        )
    background.add_task(evaluate_safe, db)
    return BoxOut.model_validate(box).model_copy(
        update={"receipt_request_id": receipt.id}
    )


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
    _check_force(payload.force, user, payload.note)
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
    for wid in outcome.affected_warehouse_ids:
        await bus.publish("box.updated", {"warehouse_id": wid, "bulk": True})
    for pallet_id, warehouse_ids in sorted(
        outcome.affected_pallet_warehouse_ids.items()
    ):
        for warehouse_id in sorted(warehouse_ids):
            await bus.publish(
                "pallet.updated",
                {"id": pallet_id, "warehouse_id": warehouse_id},
            )
    for request_id in outcome.cancelled_request_ids:
        await _publish_request_update(db, request_id)
    if outcome.updated:
        background.add_task(evaluate_safe, db)

    return BulkResult(
        updated=[BoxOut.model_validate(b) for b in outcome.updated],
        skipped=[
            BulkSkip(box_id=s.box_id, box_number=s.box_number, reason=s.reason)
            for s in outcome.skipped
        ],
        cancelled_request_ids=sorted(outcome.cancelled_request_ids),
    )


def _quarantine_request_ids(db: DbSession, box_id: int) -> list[int]:
    return list(
        db.scalars(
            select(BoxRequestItem.request_id)
            .where(BoxRequestItem.box_id == box_id)
            .distinct()
        ).all()
    )


@router.post("/quarantine/release", response_model=QuarantineBulkResult)
async def release_quarantined_boxes(
    payload: QuarantineBulkAction,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> QuarantineBulkResult:
    updated: list[Box] = []
    skipped: list[BulkSkip] = []
    request_ids: set[int] = set()
    for box_id in dict.fromkeys(payload.box_ids):
        box = db.scalar(
            select(Box).where(Box.id == box_id).with_for_update(of=Box)
        )
        if box is None or box.archived_at is not None:
            skipped.append(BulkSkip(box_id=box_id, box_number="", reason="box not found"))
            continue
        if box.status != BoxStatus.quarantined:
            skipped.append(
                BulkSkip(
                    box_id=box.id,
                    box_number=box.box_number,
                    reason="box is not quarantined",
                )
            )
            continue
        linked = _quarantine_request_ids(db, box.id)
        try:
            update_box(
                db,
                user=user,
                box=box,
                new_status=BoxStatus.received,
                note=payload.reason,
                force=True,
                commit=False,
            )
        except BoxRuleError as exc:
            skipped.append(
                BulkSkip(box_id=box.id, box_number=box.box_number, reason=str(exc))
            )
            continue
        request_ids.update(linked)
        updated.append(box)
    for request_id in request_ids:
        request = db.get(BoxRequest, request_id)
        if request is not None:
            enqueue_request_event(
                db,
                request=request,
                kind=RequestNotificationKind.quarantine_released,
                event_token=f"quarantine-released:{','.join(map(str, sorted(payload.box_ids)))}",
                detail=payload.reason,
            )
    db.commit()
    for request_id in request_ids:
        request = db.get(BoxRequest, request_id)
        if request is not None:
            await publish_notification_event(
                warehouse_id=request.warehouse_id,
                request_id=request_id,
            )
    for warehouse_id in {box.current_warehouse_id for box in updated}:
        await bus.publish("box.updated", {"warehouse_id": warehouse_id, "bulk": True})
    if updated:
        background.add_task(evaluate_safe, db)
    return QuarantineBulkResult(
        updated=[BoxOut.model_validate(box) for box in updated],
        skipped=skipped,
        request_ids=sorted(request_ids),
    )


@router.post("/quarantine/reject", response_model=QuarantineBulkResult)
async def reject_quarantined_boxes(
    payload: QuarantineBulkAction,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> QuarantineBulkResult:
    archived_ids: list[int] = []
    skipped: list[BulkSkip] = []
    request_ids: set[int] = set()
    warehouse_ids: set[int] = set()
    for box_id in dict.fromkeys(payload.box_ids):
        box = db.scalar(
            select(Box).where(Box.id == box_id).with_for_update(of=Box)
        )
        if box is None or box.archived_at is not None:
            skipped.append(BulkSkip(box_id=box_id, box_number="", reason="box not found"))
            continue
        if box.status != BoxStatus.quarantined:
            skipped.append(
                BulkSkip(
                    box_id=box.id,
                    box_number=box.box_number,
                    reason="box is not quarantined",
                )
            )
            continue
        linked = _quarantine_request_ids(db, box.id)
        warehouse_ids.add(box.current_warehouse_id)
        result = delete_box(
            db,
            user=user,
            box=box,
            force=bool(linked),
            reason=payload.reason,
            commit=False,
        )
        request_ids.update(linked)
        archived_ids.append(result.box_id)
    for request_id in request_ids:
        request = db.get(BoxRequest, request_id)
        if request is not None:
            enqueue_request_event(
                db,
                request=request,
                kind=RequestNotificationKind.quarantine_rejected,
                event_token=f"quarantine-rejected:{','.join(map(str, sorted(payload.box_ids)))}",
                detail=payload.reason,
            )
    db.commit()
    for request_id in request_ids:
        request = db.get(BoxRequest, request_id)
        if request is not None:
            await publish_notification_event(
                warehouse_id=request.warehouse_id,
                request_id=request_id,
            )
    for warehouse_id in warehouse_ids:
        await bus.publish("box.deleted", {"warehouse_id": warehouse_id, "bulk": True})
    if archived_ids:
        background.add_task(evaluate_safe, db)
    return QuarantineBulkResult(
        archived_ids=archived_ids,
        skipped=skipped,
        request_ids=sorted(request_ids),
    )


@router.get("/{box_id}", response_model=BoxOut)
def get_box(box_id: int, db: DbSession, user: CurrentUser) -> BoxOut:
    box = db.get(Box, box_id)
    # 404 (not 403) when the caller can't see this warehouse, so we don't
    # leak the existence of boxes outside their ACL.
    if box is None or not can_access(user, box.current_warehouse_id):
        raise HTTPException(status_code=404, detail="not found")
    return BoxOut.model_validate(box)


@router.post("/{box_id}/reassign-lot", response_model=BoxOut)
async def reassign_lot(
    box_id: int,
    payload: BoxLotReassignment,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> BoxOut:
    box = db.get(Box, box_id)
    if box is None:
        raise HTTPException(status_code=404, detail="not found")
    source_lot_id = box.lot_id
    try:
        box = reassign_box_lot(
            db,
            user=user,
            box=box,
            lot_id=payload.lot_id,
            reason=payload.reason,
            expected_version=payload.expected_lot_version,
            target_pallet_id=payload.pallet_id,
            detach_pallet=payload.detach_pallet,
        )
    except BoxRuleError as exc:
        db.rollback()
        raise _rule_error_to_http(exc) from exc
    event_data = {
        "id": box.lot_id,
        "box_id": box.id,
        "from_lot_id": source_lot_id,
        "to_lot_id": box.lot_id,
        "warehouse_id": box.current_warehouse_id,
    }
    await bus.publish("lot.reassigned", event_data)
    await bus.publish(
        "box.updated",
        {
            "id": box.id,
            "warehouse_id": box.current_warehouse_id,
            "status": box.status.value,
        },
    )
    return BoxOut.model_validate(box)


@router.patch("/{box_id}", response_model=BoxUpdateResult)
async def patch_box(
    box_id: int,
    payload: BoxUpdate,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_operator)],
) -> BoxUpdateResult:
    box = db.get(Box, box_id)
    if box is None or not can_access(user, box.current_warehouse_id):
        raise HTTPException(status_code=404, detail="not found")
    source_warehouse_id = box.current_warehouse_id
    source_pallet_id = box.pallet_id
    _check_force(payload.force, user, payload.note)
    cancelled_request_ids: set[int] = set()
    try:
        box = update_box(
            db,
            user=user,
            box=box,
            new_status=payload.status,
            new_warehouse_id=payload.warehouse_id,
            new_contents=payload.contents,
            new_files=payload.files,
            note=payload.note,
            force=payload.force,
            cancelled_request_ids=cancelled_request_ids,
            target_pallet_id=payload.pallet_id,
            detach_pallet=payload.detach_pallet,
        )
    except BoxRuleError as exc:
        db.rollback()
        raise _rule_error_to_http(exc) from exc
    for warehouse_id in {source_warehouse_id, box.current_warehouse_id}:
        await bus.publish(
            "box.updated",
            {"id": box.id, "warehouse_id": warehouse_id, "status": box.status.value},
        )
    for pallet_id in {
        value for value in (source_pallet_id, box.pallet_id) if value is not None
    }:
        for warehouse_id in {source_warehouse_id, box.current_warehouse_id}:
            await bus.publish(
                "pallet.updated",
                {"id": pallet_id, "warehouse_id": warehouse_id},
            )
    for request_id in cancelled_request_ids:
        await _publish_request_update(db, request_id)
    background.add_task(evaluate_safe, db)
    return BoxUpdateResult.model_validate(box).model_copy(
        update={
            "cancelled_request_ids": sorted(cancelled_request_ids),
            "detached_pallet_id": (
                source_pallet_id
                if source_pallet_id is not None and box.pallet_id is None
                else None
            ),
        }
    )


async def _perform_single_delete(
    box_id: int,
    payload: BoxDeleteRequest,
    db: DbSession,
    background: BackgroundTasks,
    user: User,
) -> DeleteOutcome:
    box = db.get(Box, box_id)
    if box is None or not can_access(user, box.current_warehouse_id):
        raise HTTPException(status_code=404, detail="not found")
    _check_force(payload.force, user, payload.reason)
    try:
        outcome = delete_box(
            db,
            user=user,
            box=box,
            force=payload.force,
            reason=payload.reason,
        )
    except BoxRuleError as exc:
        raise _rule_error_to_http(exc) from exc
    await bus.publish(
        "box.deleted",
        {
            "id": box_id,
            "warehouse_id": outcome.warehouse_id,
            "archived": outcome.archived,
        },
    )
    for request_id in outcome.cancelled_request_ids:
        await _publish_request_update(db, request_id)
    background.add_task(evaluate_safe, db)
    return outcome


@router.post("/{box_id}/delete", response_model=BoxDeleteResult)
async def delete_single_box_action(
    box_id: int,
    payload: BoxDeleteRequest,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> BoxDeleteResult:
    """Delete/archive action that reliably carries force metadata in a body."""
    outcome = await _perform_single_delete(
        box_id,
        payload,
        db,
        background,
        user,
    )
    return BoxDeleteResult(
        box_id=outcome.box_id,
        archived=outcome.archived,
        cancelled_request_ids=sorted(outcome.cancelled_request_ids),
    )


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
    payload: BoxDeleteRequest | None = None,
) -> Response:
    # Backwards-compatible endpoint for clients that already send DELETE.
    await _perform_single_delete(
        box_id,
        payload or BoxDeleteRequest(),
        db,
        background,
        user,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/bulk-delete", response_model=BulkDeleteResult)
async def bulk_delete(
    payload: BulkDeleteRequest,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> BulkDeleteResult:
    _check_force(payload.force, user, payload.reason)
    try:
        outcome = bulk_delete_boxes(
            db,
            user=user,
            box_ids=payload.box_ids,
            force=payload.force,
            reason=payload.reason,
        )
    except BoxRuleError as exc:
        raise _rule_error_to_http(exc) from exc
    for wid in outcome.affected_warehouse_ids:
        await bus.publish("box.deleted", {"warehouse_id": wid, "bulk": True})
    for request_id in outcome.cancelled_request_ids:
        await _publish_request_update(db, request_id)
    if outcome.deleted_ids or outcome.archived_ids:
        background.add_task(evaluate_safe, db)
    return BulkDeleteResult(
        deleted_ids=outcome.deleted_ids,
        archived_ids=outcome.archived_ids,
        cancelled_request_ids=sorted(outcome.cancelled_request_ids),
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
