from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin, require_operator
from app.events import bus
from app.models.box_files import BoxFile
from app.models.boxes import Box, BoxStatus
from app.models.requests import (
    ACTIVE_REQUEST_STATUSES,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestItem,
)
from app.models.users import User, UserRole
from app.schemas.box_files import (
    FileActivity,
    FileArchive,
    FileCreate,
    FileDetailOut,
    FileEventOut,
    FileIntegrityOut,
    FileListOut,
    FileMove,
    FileRestore,
    FileSortField,
    FileUpdate,
)
from app.schemas.common import Page
from app.services.box_files import (
    BoxFileAccessError,
    BoxFileConflictError,
    BoxFileNotFoundError,
    BoxFileRuleError,
    archive_box_file,
    create_box_file,
    file_integrity_report,
    get_box_file,
    list_box_file_events,
    list_box_files,
    move_box_file,
    restore_box_file,
    update_box_file,
)

router = APIRouter(prefix="/files", tags=["files"])


def _to_http(exc: BoxFileRuleError) -> HTTPException:
    if isinstance(exc, BoxFileAccessError):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, BoxFileNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, BoxFileConflictError):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


async def _publish_file_change(
    db: DbSession,
    *,
    file_id: int,
    topic: str,
    source_box_id: int | None = None,
    source_warehouse_id: int | None = None,
) -> None:
    view = get_box_file(
        db,
        user=_UnrestrictedReadUser(),
        file_id=file_id,
        include_inactive=True,
        include_archived=True,
    )
    await bus.publish(
        topic,
        {
            "id": file_id,
            "file_id": file_id,
            "lot_id": view["lot_id"],
            "pallet_id": view["pallet_id"],
            "box_id": view["box_id"],
            "warehouse_id": view["warehouse_id"],
            "source_box_id": source_box_id,
            "source_warehouse_id": source_warehouse_id,
            "status": (
                view["status"].value
                if hasattr(view["status"], "value")
                else view["status"]
            ),
            "is_active": view["is_active"],
            "invalidate": ["files", "boxes", "pallets", "lots", "warehouses"],
        },
    )


class _UnrestrictedReadUser:
    """Minimal admin-shaped principal used only to render committed event payloads."""

    role = UserRole.admin


def _active_return_ids(db: DbSession, file_id: int) -> list[int]:
    return list(
        db.scalars(
            select(BoxRequest.id)
            .join(BoxRequestItem, BoxRequestItem.request_id == BoxRequest.id)
            .join(BoxFile, BoxFile.box_id == BoxRequestItem.box_id)
            .where(
                BoxFile.id == file_id,
                BoxRequest.direction == BoxRequestDirection.return_,
                BoxRequest.status.in_(ACTIVE_REQUEST_STATUSES),
            )
            .distinct()
        ).all()
    )


async def _publish_request_changes(db: DbSession, request_ids: list[int]) -> None:
    for request_id in request_ids:
        request = db.get(BoxRequest, request_id)
        if request is not None:
            await bus.publish(
                "request.updated",
                {
                    "id": request.id,
                    "warehouse_id": request.warehouse_id,
                    "direction": request.direction.value,
                    "status": request.status.value,
                },
            )


@router.get("", response_model=Page[FileListOut])
def list_files(
    db: DbSession,
    user: CurrentUser,
    search: str | None = Query(default=None, max_length=255),
    warehouse_id: int | None = Query(default=None, ge=1),
    lot_id: int | None = Query(default=None, ge=1),
    pallet_id: int | None = Query(default=None, ge=1),
    box_id: int | None = Query(default=None, ge=1),
    status_filter: Annotated[
        BoxStatus | None, Query(alias="status")
    ] = None,
    activity: FileActivity | None = None,
    include_inactive: bool = False,
    sort_by: FileSortField | None = None,
    sort_dir: Literal["asc", "desc"] = "asc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[FileListOut]:
    try:
        items, total = list_box_files(
            db,
            user=user,
            search=search,
            warehouse_id=warehouse_id,
            lot_id=lot_id,
            pallet_id=pallet_id,
            box_id=box_id,
            status=status_filter,
            activity=activity,
            include_inactive=include_inactive,
            sort_by=sort_by,
            sort_dir=sort_dir,
            page=page,
            page_size=page_size,
        )
    except BoxFileRuleError as exc:
        raise _to_http(exc) from exc
    return Page[FileListOut](
        items=[FileListOut.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/integrity", response_model=FileIntegrityOut)
def integrity_report(
    db: DbSession,
    _user: Annotated[User, Depends(require_admin)],
) -> FileIntegrityOut:
    return FileIntegrityOut.model_validate(file_integrity_report(db))


@router.post("", response_model=FileDetailOut, status_code=status.HTTP_201_CREATED)
async def create_file(
    payload: FileCreate,
    db: DbSession,
    user: Annotated[User, Depends(require_operator)],
) -> FileDetailOut:
    try:
        file = create_box_file(db, user=user, **payload.model_dump())
        view = get_box_file(db, user=user, file_id=file.id)
    except BoxFileRuleError as exc:
        db.rollback()
        raise _to_http(exc) from exc
    await _publish_file_change(db, file_id=file.id, topic="file.created")
    return FileDetailOut.model_validate(view)


@router.get("/{file_id}", response_model=FileDetailOut)
def get_file(
    file_id: int,
    db: DbSession,
    user: CurrentUser,
    include_inactive: bool = False,
    include_archived: bool = False,
) -> FileDetailOut:
    try:
        return FileDetailOut.model_validate(
            get_box_file(
                db,
                user=user,
                file_id=file_id,
                include_inactive=include_inactive,
                include_archived=include_archived,
            )
        )
    except BoxFileRuleError as exc:
        raise _to_http(exc) from exc


@router.get("/{file_id}/events", response_model=list[FileEventOut])
def get_file_events(
    file_id: int,
    db: DbSession,
    user: CurrentUser,
) -> list[FileEventOut]:
    try:
        events = list_box_file_events(db, user=user, file_id=file_id)
    except BoxFileRuleError as exc:
        raise _to_http(exc) from exc
    return [FileEventOut.model_validate(event) for event in events]


@router.patch("/{file_id}", response_model=FileDetailOut)
async def patch_file(
    file_id: int,
    payload: FileUpdate,
    db: DbSession,
    user: Annotated[User, Depends(require_operator)],
) -> FileDetailOut:
    values = payload.model_dump(
        exclude={"expected_version", "force", "reason"},
        exclude_unset=True,
    )
    affected_requests = (
        _active_return_ids(db, file_id)
        if payload.force and "reference" in values
        else []
    )
    try:
        file = update_box_file(
            db,
            user=user,
            file_id=file_id,
            expected_version=payload.expected_version,
            values=values,
            force=payload.force,
            reason=payload.reason,
        )
        view = get_box_file(db, user=user, file_id=file.id)
    except BoxFileRuleError as exc:
        db.rollback()
        raise _to_http(exc) from exc
    await _publish_file_change(db, file_id=file.id, topic="file.updated")
    await _publish_request_changes(db, affected_requests)
    return FileDetailOut.model_validate(view)


@router.post("/{file_id}/move", response_model=FileDetailOut)
async def move_file(
    file_id: int,
    payload: FileMove,
    db: DbSession,
    user: Annotated[User, Depends(require_operator)],
) -> FileDetailOut:
    affected_requests = _active_return_ids(db, file_id) if payload.force else []
    source = db.scalar(
        select(Box).join(BoxFile, BoxFile.box_id == Box.id).where(BoxFile.id == file_id)
    )
    source_box_id = source.id if source is not None else None
    source_warehouse_id = source.current_warehouse_id if source is not None else None
    try:
        file = move_box_file(
            db,
            user=user,
            file_id=file_id,
            target_box_id=payload.box_id,
            expected_version=payload.expected_version,
            position=payload.position,
            force=payload.force,
            reason=payload.reason,
        )
        view = get_box_file(db, user=user, file_id=file.id)
    except BoxFileRuleError as exc:
        db.rollback()
        raise _to_http(exc) from exc
    await _publish_file_change(
        db,
        file_id=file.id,
        topic="file.moved",
        source_box_id=source_box_id,
        source_warehouse_id=source_warehouse_id,
    )
    await _publish_request_changes(db, affected_requests)
    return FileDetailOut.model_validate(view)


@router.post("/{file_id}/archive", response_model=FileDetailOut)
async def archive_file(
    file_id: int,
    payload: FileArchive,
    db: DbSession,
    user: Annotated[User, Depends(require_operator)],
) -> FileDetailOut:
    affected_requests = _active_return_ids(db, file_id) if payload.force else []
    try:
        file = archive_box_file(db, user=user, file_id=file_id, **payload.model_dump())
        view = get_box_file(
            db,
            user=_UnrestrictedReadUser(),
            file_id=file.id,
            include_inactive=True,
            include_archived=True,
        )
    except BoxFileRuleError as exc:
        db.rollback()
        raise _to_http(exc) from exc
    await _publish_file_change(db, file_id=file.id, topic="file.archived")
    await _publish_request_changes(db, affected_requests)
    return FileDetailOut.model_validate(view)


@router.post("/{file_id}/restore", response_model=FileDetailOut)
async def restore_file(
    file_id: int,
    payload: FileRestore,
    db: DbSession,
    user: Annotated[User, Depends(require_operator)],
) -> FileDetailOut:
    affected_requests = _active_return_ids(db, file_id) if payload.force else []
    try:
        file = restore_box_file(db, user=user, file_id=file_id, **payload.model_dump())
        view = get_box_file(db, user=user, file_id=file.id)
    except BoxFileRuleError as exc:
        db.rollback()
        raise _to_http(exc) from exc
    await _publish_file_change(db, file_id=file.id, topic="file.restored")
    await _publish_request_changes(db, affected_requests)
    return FileDetailOut.model_validate(view)
