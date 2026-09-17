from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.deps import CurrentUser, DbSession, require_admin, require_operator
from app.events import bus
from app.models.boxes import Box, BoxStatus
from app.models.lots import Lot, LotEvent, LotPurgeEvent
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
    LotForcePurge,
    LotForcePurgeConflictOut,
    LotForcePurgeConflictResponse,
    LotForcePurgePreviewOut,
    LotForcePurgeResultOut,
    LotIdentityOut,
    LotMerge,
    LotMergeCandidateOut,
    LotMergeConflictResponse,
    LotMergeOut,
    LotOptionOut,
    LotProgressState,
    LotPurge,
    LotPurgeCleanupOut,
    LotPurgeConflictOut,
    LotPurgeConflictResponse,
    LotPurgePreviewOut,
    LotPurgeResultOut,
    LotRename,
    LotRenameConflictResponse,
    LotSortField,
    LotSummaryOut,
    MergedLotOut,
)
from app.services.acl import apply_warehouse_filter
from app.services.lots import (
    LotAccessError,
    LotConflictError,
    LotMergeCandidate,
    LotMergeConflictError,
    LotNameCollisionError,
    LotNotFoundError,
    LotPurgeConflictError,
    LotPurgeNotFoundError,
    LotRuleError,
    LotVersionConflictError,
    analyze_lot_force_purge_impact,
    analyze_lot_purge_eligibility,
    cleanup_lot_purge_objects,
    force_purge_lot,
    get_or_create_lot_result,
    get_visible_lot_summary,
    list_lot_options,
    list_lot_summaries,
    lot_warehouse_ids,
    merge_lots,
    purge_lot,
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


def _purge_preview_out(preview) -> LotPurgePreviewOut:
    return LotPurgePreviewOut.model_validate(preview, from_attributes=True)


def _force_purge_preview_out(preview) -> LotForcePurgePreviewOut:
    return LotForcePurgePreviewOut.model_validate(preview, from_attributes=True)


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


def _lot_identity(lot: Lot | None) -> dict[str, object] | None:
    if lot is None:
        return None
    return {"id": lot.id, "name": lot.name, "version": lot.version}


def _candidate_detail(candidate: LotMergeCandidate) -> dict[str, object]:
    return LotMergeCandidateOut(
        source=LotIdentityOut(
            id=candidate.source_id,
            name=candidate.source_name,
            version=candidate.source_version,
        ),
        target=LotIdentityOut(
            id=candidate.target_id,
            name=candidate.target_name,
            version=candidate.target_version,
        ),
        merge_allowed=candidate.merge_allowed,
        overlapping_box_numbers=candidate.overlapping_box_numbers,
        overlapping_box_count=candidate.overlapping_box_count,
        overlap_list_truncated=candidate.overlap_list_truncated,
        resolvable_archived_collisions=candidate.resolvable_archived_collisions,
        resolvable_archived_collision_count=(
            candidate.resolvable_archived_collision_count
        ),
        resolvable_archived_collisions_truncated=(
            candidate.resolvable_archived_collisions_truncated
        ),
        hard_overlaps=candidate.hard_overlaps,
        hard_overlap_count=candidate.hard_overlap_count,
        hard_overlaps_truncated=candidate.hard_overlaps_truncated,
        pallet_collisions=candidate.pallet_collisions,
        pallet_collision_count=candidate.pallet_collision_count,
        pallet_collisions_truncated=candidate.pallet_collisions_truncated,
        pallet_actions=candidate.pallet_actions,
        pallet_action_count=candidate.pallet_action_count,
        pallet_actions_truncated=candidate.pallet_actions_truncated,
        file_reference_collisions=candidate.file_reference_collisions,
        file_reference_collision_count=(
            candidate.file_reference_collision_count
        ),
        file_reference_collisions_truncated=(
            candidate.file_reference_collisions_truncated
        ),
        active_file_reference_collision_count=(
            candidate.active_file_reference_collision_count
        ),
        archived_file_reference_collision_count=(
            candidate.archived_file_reference_collision_count
        ),
        merge_allowed_with_archived_overwrite=(
            candidate.merge_allowed_with_archived_overwrite
        ),
        requires_explicit_overwrite=candidate.requires_explicit_overwrite,
        collision_signature=candidate.collision_signature,
    ).model_dump(mode="json")


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


@router.get("/{lot_id}/purge-preview", response_model=LotPurgePreviewOut)
def purge_preview(
    lot_id: int,
    db: DbSession,
    _user: Annotated[User, Depends(require_admin)],
) -> LotPurgePreviewOut:
    try:
        preview = analyze_lot_purge_eligibility(db, lot_id=lot_id)
    except LotPurgeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _purge_preview_out(preview)


@router.get(
    "/{lot_id}/force-purge-preview",
    response_model=LotForcePurgePreviewOut,
)
def force_purge_preview(
    lot_id: int,
    db: DbSession,
    _user: Annotated[User, Depends(require_admin)],
) -> LotForcePurgePreviewOut:
    try:
        preview = analyze_lot_force_purge_impact(db, lot_id=lot_id)
    except LotPurgeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _force_purge_preview_out(preview)


@router.post(
    "/{lot_id}/force-purge",
    response_model=LotForcePurgeResultOut,
    responses={409: {"model": LotForcePurgeConflictResponse}},
)
async def force_purge(
    lot_id: int,
    payload: LotForcePurge,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
) -> LotForcePurgeResultOut:
    try:
        result = force_purge_lot(
            db,
            user=user,
            lot_id=lot_id,
            confirmation_name=payload.confirmation_name,
            confirmation_phrase=payload.confirmation_phrase,
            reason=payload.reason,
            expected_version=payload.expected_version,
            expected_graph_signature=payload.expected_graph_signature,
            acknowledged_blocker_codes=payload.acknowledged_blocker_codes,
        )
    except LotPurgeNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LotPurgeConflictError as exc:
        db.rollback()
        detail = LotForcePurgeConflictOut(
            code=exc.code,
            message=str(exc),
            current_preview=(
                _force_purge_preview_out(exc.preview)
                if exc.preview is not None
                else None
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail.model_dump(mode="json"),
        ) from exc
    except IntegrityError as exc:
        db.rollback()
        try:
            current = analyze_lot_force_purge_impact(db, lot_id=lot_id)
        except LotPurgeNotFoundError:
            current = None
        detail = LotForcePurgeConflictOut(
            code="force_purge_integrity_conflict",
            message="the lot graph changed during force purge; review the latest preview",
            current_preview=(
                _force_purge_preview_out(current) if current is not None else None
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail.model_dump(mode="json"),
        ) from exc
    except LotRuleError as exc:
        db.rollback()
        raise _lot_error(exc) from exc

    try:
        cleanup = cleanup_lot_purge_objects(
            db,
            user=user,
            audit_id=result.audit_id,
        )
        cleanup_status = cleanup.status
        cleanup_failure_count = len(cleanup.failures)
    except Exception:
        db.rollback()
        audit = db.get(LotPurgeEvent, result.audit_id)
        cleanup_status = (
            audit.object_cleanup_status
            if audit is not None
            else result.object_cleanup_status
        )
        cleanup_failure_count = (
            len(audit.object_cleanup_failures)
            if audit is not None
            else len(result.object_cleanup_failures)
        )

    event_data = {
        "id": result.lot_id,
        "lot_id": result.lot_id,
        "lot_name": result.lot_name,
        "lot_version": result.lot_version,
        "purge_mode": "force",
        "active_box_count": result.active_box_count,
        "archived_box_count": result.archived_box_count,
        "pallet_count": result.pallet_count,
        "touched_request_count": result.touched_request_count,
        "rewritten_request_count": result.rewritten_request_count,
        "deleted_request_count": result.deleted_request_count,
        "lineage_detachment_count": result.lineage_detachment_count,
        "deletable_object_count": result.deletable_object_count,
        "skipped_object_count": result.skipped_object_count,
        "purge_audit_id": result.audit_id,
    }
    if result.warehouse_ids:
        for warehouse_id in result.warehouse_ids:
            await bus.publish(
                "lot.purged",
                {**event_data, "warehouse_id": warehouse_id},
            )
    else:
        await bus.publish("lot.purged", {**event_data, "warehouse_id": None})

    return LotForcePurgeResultOut(
        purge_audit_id=result.audit_id,
        deleted_lot=LotIdentityOut(
            id=result.lot_id,
            name=result.lot_name,
            version=result.lot_version,
        ),
        active_box_count=result.active_box_count,
        archived_box_count=result.archived_box_count,
        pallet_count=result.pallet_count,
        touched_request_count=result.touched_request_count,
        rewritten_request_count=result.rewritten_request_count,
        deleted_request_count=result.deleted_request_count,
        lineage_detachment_count=result.lineage_detachment_count,
        deletable_object_count=result.deletable_object_count,
        skipped_object_count=result.skipped_object_count,
        object_cleanup_status=cleanup_status,
        object_cleanup_failure_count=cleanup_failure_count,
        file_count=result.file_count,
        file_event_count=result.file_event_count,
        detached_file_snapshot_count=result.detached_file_snapshot_count,
    )


@router.post(
    "/{lot_id}/purge",
    response_model=LotPurgeResultOut,
    responses={409: {"model": LotPurgeConflictResponse}},
)
async def purge(
    lot_id: int,
    payload: LotPurge,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
) -> LotPurgeResultOut:
    try:
        result = purge_lot(
            db,
            user=user,
            lot_id=lot_id,
            confirmation_name=payload.confirmation_name,
            reason=payload.reason,
            expected_version=payload.expected_version,
            expected_graph_signature=payload.expected_graph_signature,
        )
    except LotPurgeNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LotPurgeConflictError as exc:
        db.rollback()
        detail = LotPurgeConflictOut(
            code=exc.code,
            message=str(exc),
            current_preview=(
                _purge_preview_out(exc.preview) if exc.preview is not None else None
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail.model_dump(mode="json"),
        ) from exc
    except IntegrityError as exc:
        db.rollback()
        try:
            current = analyze_lot_purge_eligibility(db, lot_id=lot_id)
        except LotPurgeNotFoundError:
            current = None
        detail = LotPurgeConflictOut(
            code="purge_integrity_conflict",
            message="the lot graph changed during purge; review the latest preview",
            current_preview=(
                _purge_preview_out(current) if current is not None else None
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail.model_dump(mode="json"),
        ) from exc
    except LotRuleError as exc:
        db.rollback()
        raise _lot_error(exc) from exc

    try:
        cleanup = cleanup_lot_purge_objects(
            db,
            user=user,
            audit_id=result.audit_id,
        )
        cleanup_status = cleanup.status
        cleanup_failures = cleanup.failures
    except Exception:
        # The lot and its ledger are already committed. Never turn a
        # post-commit cleanup bookkeeping outage into an apparent DB rollback.
        db.rollback()
        audit = db.get(LotPurgeEvent, result.audit_id)
        cleanup_status = (
            audit.object_cleanup_status
            if audit is not None
            else result.object_cleanup_status
        )
        cleanup_failures = (
            list(audit.object_cleanup_failures)
            if audit is not None
            else result.object_cleanup_failures
        )

    event_data = {
        "id": result.lot_id,
        "lot_id": result.lot_id,
        "lot_name": result.lot_name,
        "lot_version": result.lot_version,
        "archived_box_count": result.archived_box_count,
        "pallet_count": result.pallet_count,
        "receipt_count": result.receipt_count,
        "object_key_count": result.object_key_count,
        "purge_audit_id": result.audit_id,
    }
    if result.warehouse_ids:
        for warehouse_id in result.warehouse_ids:
            await bus.publish(
                "lot.purged",
                {**event_data, "warehouse_id": warehouse_id},
            )
    else:
        await bus.publish("lot.purged", {**event_data, "warehouse_id": None})

    return LotPurgeResultOut(
        purge_audit_id=result.audit_id,
        deleted_lot=LotIdentityOut(
            id=result.lot_id,
            name=result.lot_name,
            version=result.lot_version,
        ),
        archived_box_count=result.archived_box_count,
        pallet_count=result.pallet_count,
        receipt_count=result.receipt_count,
        object_key_count=result.object_key_count,
        object_cleanup_status=cleanup_status,
        object_cleanup_failures=cleanup_failures,
        file_count=result.file_count,
        file_event_count=result.file_event_count,
        detached_file_snapshot_count=result.detached_file_snapshot_count,
    )


@router.post(
    "/purge-audits/{audit_id}/cleanup-retry",
    response_model=LotPurgeCleanupOut,
)
def retry_purge_cleanup(
    audit_id: int,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
) -> LotPurgeCleanupOut:
    try:
        cleanup = cleanup_lot_purge_objects(
            db,
            user=user,
            audit_id=audit_id,
        )
    except LotPurgeNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return LotPurgeCleanupOut(
        purge_audit_id=cleanup.audit_id,
        object_cleanup_status=cleanup.status,
        object_cleanup_failures=cleanup.failures,
    )


@router.get("/{lot_id}", response_model=LotDetailOut | MergedLotOut)
def get_lot_detail(
    lot_id: int,
    db: DbSession,
    user: CurrentUser,
) -> LotDetailOut | MergedLotOut:
    stored = db.get(Lot, lot_id)
    if stored is not None and stored.merged_into_lot_id is not None:
        target = stored
        visited = {stored.id}
        while target.merged_into_lot_id is not None:
            if target.merged_into_lot_id in visited:
                target = None
                break
            visited.add(target.merged_into_lot_id)
            target = db.get(Lot, target.merged_into_lot_id)
            if target is None:
                break
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "broken_merge_target",
                    "message": "merged lot target is unavailable",
                },
            )
        try:
            get_visible_lot_summary(db, user=user, lot_id=target.id)
        except LotRuleError as exc:
            raise _lot_error(exc) from exc
        assert stored.merged_at is not None
        return MergedLotOut(
            id=stored.id,
            name=stored.name,
            version=stored.version,
            merged_at=stored.merged_at,
            merged_by_user_id=stored.merged_by_user_id,
            merged_into=LotIdentityOut(
                id=target.id,
                name=target.name,
                version=target.version,
            ),
        )
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


@router.patch(
    "/{lot_id}/rename",
    response_model=LotSummaryOut,
    responses={409: {"model": LotRenameConflictResponse}},
)
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
        candidate = (
            _candidate_detail(exc.candidate)
            if isinstance(exc, LotNameCollisionError)
            else None
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": (
                    "version_conflict"
                    if isinstance(exc, LotVersionConflictError)
                    else "name_collision"
                ),
                "message": str(exc),
                "merge_candidate": candidate,
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


@router.post(
    "/{source_id}/merge",
    response_model=LotMergeOut,
    responses={409: {"model": LotMergeConflictResponse}},
)
async def merge(
    source_id: int,
    payload: LotMerge,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
) -> LotMergeOut:
    try:
        result = merge_lots(
            db,
            user=user,
            source_lot_id=source_id,
            target_lot_id=payload.target_lot_id,
            reason=payload.reason,
            expected_source_version=payload.expected_source_version,
            expected_target_version=payload.expected_target_version,
            overwrite_archived_collisions=payload.overwrite_archived_collisions,
            expected_collision_signature=payload.expected_collision_signature,
        )
    except LotMergeConflictError as exc:
        db.rollback()
        source = db.get(Lot, source_id)
        target = db.get(Lot, payload.target_lot_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "message": str(exc),
                "source": _lot_identity(source),
                "target": _lot_identity(target),
                "merge_candidate": (
                    _candidate_detail(exc.candidate)
                    if exc.candidate is not None
                    else None
                ),
            },
        ) from exc
    except LotRuleError as exc:
        db.rollback()
        raise _lot_error(exc) from exc
    except Exception:
        db.rollback()
        raise

    event_data = {
        "id": result.target.id,
        "source_lot_id": result.source.id,
        "source_lot_name": result.source.name,
        "source_version": result.source.version,
        "target_lot_id": result.target.id,
        "target_lot_name": result.target.name,
        "target_version": result.target.version,
        "moved_box_count": result.moved_box_count,
        "moved_request_item_count": result.moved_request_item_count,
        "overwritten_archived_box_count": result.overwritten_archived_box_count,
        "relinked_request_item_count": result.relinked_request_item_count,
        "relinked_discrepancy_count": result.relinked_discrepancy_count,
        "deleted_box_event_count": result.deleted_box_event_count,
        "combined_pallet_count": result.combined_pallet_count,
        "moved_pallet_count": result.moved_pallet_count,
        "absorbed_pallet_ids": result.absorbed_pallet_ids,
        "moved_pallet_ids": result.moved_pallet_ids,
        "survivor_box_ids": result.survivor_box_ids,
        "removed_box_ids": result.removed_box_ids,
    }
    if result.warehouse_ids:
        for warehouse_id in result.warehouse_ids:
            await bus.publish(
                "lot.merged",
                {**event_data, "warehouse_id": warehouse_id},
            )
    else:
        await bus.publish("lot.merged", {**event_data, "warehouse_id": None})
    return LotMergeOut(
        source=LotIdentityOut(
            id=result.source.id,
            name=result.source.name,
            version=result.source.version,
        ),
        target=LotIdentityOut(
            id=result.target.id,
            name=result.target.name,
            version=result.target.version,
        ),
        moved_box_count=result.moved_box_count,
        moved_request_item_count=result.moved_request_item_count,
        overwritten_archived_box_count=result.overwritten_archived_box_count,
        relinked_request_item_count=result.relinked_request_item_count,
        relinked_discrepancy_count=result.relinked_discrepancy_count,
        deleted_box_event_count=result.deleted_box_event_count,
        combined_pallet_count=result.combined_pallet_count,
        moved_pallet_count=result.moved_pallet_count,
        absorbed_pallet_ids=result.absorbed_pallet_ids,
        moved_pallet_ids=result.moved_pallet_ids,
        moved_file_count=result.moved_file_count,
        overwritten_archived_file_count=(
            result.overwritten_archived_file_count
        ),
        deleted_file_event_count=result.deleted_file_event_count,
    )


__all__ = ["router"]
