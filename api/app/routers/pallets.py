from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.deps import CurrentUser, DbSession, require_admin, require_operator
from app.events import bus
from app.models.pallets import Pallet
from app.models.requests import BoxRequest
from app.models.users import User
from app.schemas.common import Page
from app.schemas.pallets import (
    PalletBoxMutation,
    PalletBoxMutationOut,
    PalletConflictResponse,
    PalletCreate,
    PalletDetailOut,
    PalletEventOut,
    PalletIntegrityOut,
    PalletMove,
    PalletMoveOut,
    PalletOptionOut,
    PalletProgressState,
    PalletRename,
    PalletSortField,
    PalletStateChange,
    PalletSummaryOut,
)
from app.services.pallets import (
    PalletAccessError,
    PalletArchiveConflictError,
    PalletConflictError,
    PalletNotFoundError,
    PalletRuleError,
    archive_pallet,
    create_pallet,
    get_visible_pallet_summary,
    list_pallet_events,
    list_pallet_options,
    list_pallet_summaries,
    move_pallet,
    mutate_pallet_boxes,
    pallet_integrity_report,
    rename_pallet,
    restore_pallet,
)

router = APIRouter(prefix="/pallets", tags=["pallets"])


def _summary_out(summary) -> PalletSummaryOut:
    return PalletSummaryOut.model_validate(summary, from_attributes=True)


def _rule_error(exc: PalletRuleError) -> HTTPException:
    if isinstance(exc, PalletAccessError):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, PalletNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, PalletConflictError):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


def _conflict_detail(
    db: DbSession,
    pallet_id: int,
    exc: PalletConflictError,
) -> dict[str, object]:
    current = db.get(Pallet, pallet_id)
    return {
        "code": exc.code,
        "message": str(exc),
        "current": (
            {
                "id": current.id,
                "pallet_number": current.pallet_number,
                "normalized_pallet_number": current.normalized_pallet_number,
                "version": current.version,
                "is_active": current.is_active,
                "updated_at": current.updated_at.isoformat(),
            }
            if current is not None
            else None
        ),
        "box_count": (
            exc.box_count if isinstance(exc, PalletArchiveConflictError) else None
        ),
    }


@router.get("", response_model=Page[PalletSummaryOut])
def list_pallets(
    db: DbSession,
    user: CurrentUser,
    search: str | None = None,
    warehouse_id: int | None = Query(default=None, ge=1),
    lot_id: int | None = Query(default=None, ge=1),
    progress_state: PalletProgressState | None = None,
    include_inactive: bool = False,
    sort_by: PalletSortField = "latest_activity",
    sort_dir: Literal["asc", "desc"] = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[PalletSummaryOut]:
    try:
        rows, total = list_pallet_summaries(
            db,
            user=user,
            search=search,
            warehouse_id=warehouse_id,
            lot_id=lot_id,
            progress_state=progress_state,
            include_inactive=include_inactive,
            sort_by=sort_by,
            sort_dir=sort_dir,
            page=page,
            page_size=page_size,
        )
    except PalletRuleError as exc:
        raise _rule_error(exc) from exc
    return Page[PalletSummaryOut](
        items=[_summary_out(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/options", response_model=Page[PalletOptionOut])
def options(
    db: DbSession,
    user: CurrentUser,
    search: str | None = None,
    warehouse_id: int | None = Query(default=None, ge=1),
    lot_id: int | None = Query(default=None, ge=1),
    include_inactive: bool = False,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=200),
) -> Page[PalletOptionOut]:
    try:
        rows, total = list_pallet_options(
            db,
            user=user,
            search=search,
            warehouse_id=warehouse_id,
            lot_id=lot_id,
            include_inactive=include_inactive,
            page=page,
            limit=limit,
        )
    except PalletRuleError as exc:
        raise _rule_error(exc) from exc
    return Page[PalletOptionOut](
        items=[
            PalletOptionOut.model_validate(row, from_attributes=True) for row in rows
        ],
        total=total,
        page=page,
        page_size=limit,
    )


@router.post(
    "",
    response_model=PalletSummaryOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": PalletConflictResponse}},
)
async def create(
    payload: PalletCreate,
    db: DbSession,
    user: Annotated[User, Depends(require_operator)],
) -> PalletSummaryOut:
    try:
        pallet = create_pallet(
            db,
            user=user,
            lot_id=payload.lot_id,
            warehouse_id=payload.warehouse_id,
            pallet_number=payload.pallet_number,
        )
        summary = get_visible_pallet_summary(db, user=user, pallet_id=pallet.id)
    except PalletConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc), "current": None},
        ) from exc
    except PalletRuleError as exc:
        db.rollback()
        raise _rule_error(exc) from exc
    await bus.publish(
        "pallet.created",
        {
            "id": pallet.id,
            "pallet_number": pallet.pallet_number,
            "lot_id": pallet.lot_id,
            "warehouse_id": pallet.current_warehouse_id,
            "version": pallet.version,
        },
    )
    return _summary_out(summary)


@router.get("/integrity", response_model=PalletIntegrityOut)
def integrity_report(
    db: DbSession,
    _user: Annotated[User, Depends(require_admin)],
) -> PalletIntegrityOut:
    return PalletIntegrityOut.model_validate(pallet_integrity_report(db))


@router.get("/{pallet_id}", response_model=PalletDetailOut)
def detail(
    pallet_id: int,
    db: DbSession,
    user: CurrentUser,
    include_inactive: bool = False,
) -> PalletDetailOut:
    try:
        summary = get_visible_pallet_summary(
            db,
            user=user,
            pallet_id=pallet_id,
            include_inactive=include_inactive,
        )
    except PalletRuleError as exc:
        raise _rule_error(exc) from exc
    return PalletDetailOut(**_summary_out(summary).model_dump())


@router.get("/{pallet_id}/events", response_model=list[PalletEventOut])
def events(
    pallet_id: int,
    db: DbSession,
    user: CurrentUser,
    include_inactive: bool = False,
) -> list[PalletEventOut]:
    try:
        rows = list_pallet_events(
            db,
            user=user,
            pallet_id=pallet_id,
            include_inactive=include_inactive,
        )
    except PalletRuleError as exc:
        raise _rule_error(exc) from exc
    return [PalletEventOut.model_validate(row) for row in rows]


async def _publish_pallet_box_mutation(
    db: DbSession,
    *,
    pallet_id: int,
    warehouse_ids: set[int],
    box_ids: list[int],
    cancelled_request_ids: list[int],
) -> None:
    for warehouse_id in warehouse_ids:
        await bus.publish(
            "pallet.updated",
            {"id": pallet_id, "warehouse_id": warehouse_id},
        )
        await bus.publish(
            "box.updated",
            {"warehouse_id": warehouse_id, "box_ids": box_ids, "bulk": True},
        )
    for request_id in cancelled_request_ids:
        request = db.get(BoxRequest, request_id)
        if request is not None:
            await bus.publish(
                "request.updated",
                {
                    "id": request.id,
                    "warehouse_id": request.warehouse_id,
                    "status": request.status.value,
                },
            )


@router.post(
    "/{pallet_id}/boxes/assign",
    response_model=PalletBoxMutationOut,
)
async def assign_boxes(
    pallet_id: int,
    payload: PalletBoxMutation,
    db: DbSession,
    user: Annotated[User, Depends(require_operator)],
) -> PalletBoxMutationOut:
    try:
        result = mutate_pallet_boxes(
            db,
            user=user,
            pallet_id=pallet_id,
            box_ids=payload.box_ids,
            reason=payload.reason,
        )
    except PalletRuleError as exc:
        db.rollback()
        raise _rule_error(exc) from exc
    pallet = db.get(Pallet, pallet_id)
    assert pallet is not None
    await _publish_pallet_box_mutation(
        db,
        pallet_id=pallet_id,
        warehouse_ids={pallet.current_warehouse_id},
        box_ids=result.updated_box_ids,
        cancelled_request_ids=result.cancelled_request_ids,
    )
    return PalletBoxMutationOut.model_validate(result, from_attributes=True)


@router.post(
    "/{pallet_id}/boxes/detach",
    response_model=PalletBoxMutationOut,
)
async def detach_boxes(
    pallet_id: int,
    payload: PalletBoxMutation,
    db: DbSession,
    user: Annotated[User, Depends(require_operator)],
) -> PalletBoxMutationOut:
    try:
        result = mutate_pallet_boxes(
            db,
            user=user,
            pallet_id=pallet_id,
            box_ids=payload.box_ids,
            reason=payload.reason,
            detach=True,
        )
    except PalletRuleError as exc:
        db.rollback()
        raise _rule_error(exc) from exc
    pallet = db.get(Pallet, pallet_id)
    assert pallet is not None
    await _publish_pallet_box_mutation(
        db,
        pallet_id=pallet_id,
        warehouse_ids={pallet.current_warehouse_id},
        box_ids=result.updated_box_ids,
        cancelled_request_ids=result.cancelled_request_ids,
    )
    return PalletBoxMutationOut.model_validate(result, from_attributes=True)


@router.post("/{pallet_id}/move", response_model=PalletMoveOut)
async def move(
    pallet_id: int,
    payload: PalletMove,
    db: DbSession,
    user: Annotated[User, Depends(require_operator)],
) -> PalletMoveOut:
    try:
        result = move_pallet(
            db,
            user=user,
            pallet_id=pallet_id,
            target_warehouse_id=payload.warehouse_id,
            expected_version=payload.expected_version,
            force=payload.force,
            reason=payload.reason,
        )
    except PalletRuleError as exc:
        db.rollback()
        raise _rule_error(exc) from exc
    await _publish_pallet_box_mutation(
        db,
        pallet_id=pallet_id,
        warehouse_ids={result.from_warehouse_id, result.to_warehouse_id},
        box_ids=result.box_ids,
        cancelled_request_ids=result.cancelled_request_ids,
    )
    return PalletMoveOut.model_validate(result, from_attributes=True)


@router.patch(
    "/{pallet_id}/rename",
    response_model=PalletSummaryOut,
    responses={409: {"model": PalletConflictResponse}},
)
async def rename(
    pallet_id: int,
    payload: PalletRename,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
) -> PalletSummaryOut:
    try:
        pallet = rename_pallet(
            db,
            user=user,
            pallet_id=pallet_id,
            new_pallet_number=payload.new_pallet_number,
            reason=payload.reason,
            expected_version=payload.expected_version,
        )
        summary = get_visible_pallet_summary(db, user=user, pallet_id=pallet.id)
    except PalletConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_detail(db, pallet_id, exc),
        ) from exc
    except PalletRuleError as exc:
        db.rollback()
        raise _rule_error(exc) from exc
    await bus.publish(
        "pallet.renamed",
        {
            "id": pallet.id,
            "pallet_number": pallet.pallet_number,
            "lot_id": pallet.lot_id,
            "warehouse_id": pallet.current_warehouse_id,
            "version": pallet.version,
        },
    )
    return _summary_out(summary)


@router.post(
    "/{pallet_id}/archive",
    response_model=PalletSummaryOut,
    responses={409: {"model": PalletConflictResponse}},
)
async def archive(
    pallet_id: int,
    payload: PalletStateChange,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
) -> PalletSummaryOut:
    try:
        pallet = archive_pallet(
            db,
            user=user,
            pallet_id=pallet_id,
            reason=payload.reason,
            expected_version=payload.expected_version,
        )
        summary = get_visible_pallet_summary(
            db, user=user, pallet_id=pallet.id, include_inactive=True
        )
    except PalletConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_detail(db, pallet_id, exc),
        ) from exc
    except PalletRuleError as exc:
        db.rollback()
        raise _rule_error(exc) from exc
    await bus.publish(
        "pallet.archived",
        {
            "id": pallet.id,
            "lot_id": pallet.lot_id,
            "warehouse_id": pallet.current_warehouse_id,
            "version": pallet.version,
        },
    )
    return _summary_out(summary)


@router.post(
    "/{pallet_id}/restore",
    response_model=PalletSummaryOut,
    responses={409: {"model": PalletConflictResponse}},
)
async def restore(
    pallet_id: int,
    payload: PalletStateChange,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
) -> PalletSummaryOut:
    try:
        pallet = restore_pallet(
            db,
            user=user,
            pallet_id=pallet_id,
            reason=payload.reason,
            expected_version=payload.expected_version,
        )
        summary = get_visible_pallet_summary(db, user=user, pallet_id=pallet.id)
    except PalletConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_detail(db, pallet_id, exc),
        ) from exc
    except PalletRuleError as exc:
        db.rollback()
        raise _rule_error(exc) from exc
    await bus.publish(
        "pallet.restored",
        {
            "id": pallet.id,
            "lot_id": pallet.lot_id,
            "warehouse_id": pallet.current_warehouse_id,
            "version": pallet.version,
        },
    )
    return _summary_out(summary)


__all__ = ["router"]
