from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, require_warehouse_mover
from app.events import bus
from app.models.requests import (
    BoxRequest,
    BoxRequestDirection,
    BoxRequestDocument,
    BoxRequestDocumentType,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestOrigin,
    BoxRequestStatus,
)
from app.models.users import User, UserRole
from app.schemas.common import Page
from app.schemas.requests import (
    BoxRequestCreate,
    BoxRequestDocumentOut,
    BoxRequestEventOut,
    BoxRequestItemOut,
    BoxRequestOut,
    BoxRequestSuggestion,
    RequestAction,
    RequestCancel,
    RequestComplete,
    RequestReject,
    ReturnCandidateOut,
    ReturnSourceOut,
    XlsxPreviewOut,
    XlsxPreviewRow,
    XlsxPreviewSheet,
)
from app.services.acl import apply_warehouse_filter, can_access
from app.services.alerts import evaluate_safe
from app.services.boxes import BoxRuleError
from app.services.object_storage import (
    StorageUnavailableError,
    delete_document,
    get_document,
    put_document,
)
from app.services.requests import (
    RequestAccessError,
    RequestConflictError,
    RequestRuleError,
    approve_request,
    calculate_suggestion,
    cancel_request,
    complete_request,
    create_request,
    get_return_candidates,
    list_return_sources,
    reject_request,
    start_transit,
)
from app.services.xlsx_preview import MAX_XLSX_BYTES, preview_xlsx

router = APIRouter(prefix="/requests", tags=["requests"])
_ALLOWED_DOCUMENT_TYPES = {
    "application/pdf": {".pdf"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
}


def _valid_document_signature(content_type: str, content: bytes) -> bool:
    if content_type == "application/pdf":
        return content.startswith(b"%PDF-")
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    return False


def _error(exc: RequestRuleError) -> HTTPException:
    if isinstance(exc, RequestConflictError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, RequestAccessError):
        code = (
            status.HTTP_404_NOT_FOUND
            if str(exc) == "request not found"
            else status.HTTP_403_FORBIDDEN
        )
    elif str(exc) == "request not found":
        code = status.HTTP_404_NOT_FOUND
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


def _request_out(db: DbSession, request: BoxRequest) -> BoxRequestOut:
    requester = (
        db.get(User, request.requester_user_id)
        if request.requester_user_id is not None
        else None
    )
    documents = db.scalars(
        select(BoxRequestDocument)
        .where(BoxRequestDocument.request_id == request.id)
        .order_by(BoxRequestDocument.created_at.desc(), BoxRequestDocument.id.desc())
    ).all()
    return BoxRequestOut(
        id=request.id,
        direction=request.direction,
        warehouse_id=request.warehouse_id,
        quantity=request.quantity,
        status=request.status,
        requester_user_id=request.requester_user_id,
        requester_name=(
            (requester.display_name or requester.email)
            if requester is not None
            else "System receipt"
        ),
        source_inbound_request_id=request.source_inbound_request_id,
        origin=request.origin,
        suggestion_quantity=request.suggestion_quantity,
        current_available=request.current_available,
        min_inventory=request.min_inventory,
        pending_inbound=request.pending_inbound,
        eligible_return=request.eligible_return,
        actual_received_quantity=request.actual_received_quantity,
        variance_quantity=request.variance_quantity,
        rejection_reason=request.rejection_reason,
        cancellation_reason=request.cancellation_reason,
        discrepancy_reason=request.discrepancy_reason,
        submitted_at=request.submitted_at,
        approved_at=request.approved_at,
        in_transit_at=request.in_transit_at,
        completed_at=request.completed_at,
        created_at=request.created_at,
        updated_at=request.updated_at,
        version=request.version,
        items=[BoxRequestItemOut.model_validate(item) for item in request.items],
        documents=[
            BoxRequestDocumentOut.model_validate(document) for document in documents
        ],
    )


def _visible(db: DbSession, user: User, request_id: int) -> BoxRequest:
    request = db.get(BoxRequest, request_id)
    if request is None or not can_access(user, request.warehouse_id):
        raise HTTPException(status_code=404, detail="not found")
    return request


async def _publish(request: BoxRequest, event_type: str = "request.updated") -> None:
    await bus.publish(
        event_type,
        {
            "id": request.id,
            "warehouse_id": request.warehouse_id,
            "direction": request.direction.value,
            "status": request.status.value,
        },
    )


@router.get("", response_model=Page[BoxRequestOut])
def list_requests(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    direction: BoxRequestDirection | None = None,
    request_status: Annotated[
        BoxRequestStatus | None, Query(alias="status")
    ] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[BoxRequestOut]:
    stmt = select(BoxRequest)
    if warehouse_id is not None:
        stmt = stmt.where(BoxRequest.warehouse_id == warehouse_id)
    if direction is not None:
        stmt = stmt.where(BoxRequest.direction == direction)
    if request_status is not None:
        stmt = stmt.where(BoxRequest.status == request_status)
    stmt = apply_warehouse_filter(stmt, user, BoxRequest.warehouse_id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    requests = db.scalars(
        stmt.order_by(BoxRequest.created_at.desc(), BoxRequest.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page[BoxRequestOut](
        items=[_request_out(db, request) for request in requests],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/suggestion", response_model=BoxRequestSuggestion)
def get_suggestion(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: Annotated[int, Query(ge=1)],
    direction: Annotated[BoxRequestDirection, Query()],
) -> BoxRequestSuggestion:
    try:
        suggestion = calculate_suggestion(
            db, user=user, warehouse_id=warehouse_id, direction=direction
        )
    except RequestRuleError as exc:
        raise _error(exc) from exc
    return BoxRequestSuggestion(**suggestion.__dict__)


@router.get("/return-sources", response_model=list[ReturnSourceOut])
def get_return_sources(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: Annotated[int, Query(ge=1)],
) -> list[ReturnSourceOut]:
    try:
        sources = list_return_sources(db, user=user, warehouse_id=warehouse_id)
    except RequestRuleError as exc:
        raise _error(exc) from exc
    return [ReturnSourceOut(**source.__dict__) for source in sources]


@router.post("", response_model=BoxRequestOut, status_code=status.HTTP_201_CREATED)
async def submit_request(
    payload: BoxRequestCreate,
    db: DbSession,
    user: CurrentUser,
) -> BoxRequestOut:
    try:
        request = create_request(
            db,
            user=user,
            warehouse_id=payload.warehouse_id,
            direction=payload.direction,
            quantity=payload.quantity,
            source_inbound_request_id=payload.source_inbound_request_id,
            box_ids=payload.box_ids,
        )
    except RequestRuleError as exc:
        raise _error(exc) from exc
    await _publish(request, "request.created")
    return _request_out(db, request)


@router.get(
    "/{source_inbound_request_id}/return-candidates",
    response_model=list[ReturnCandidateOut],
)
def list_return_candidates(
    source_inbound_request_id: int,
    db: DbSession,
    user: CurrentUser,
) -> list[ReturnCandidateOut]:
    try:
        candidates = get_return_candidates(
            db,
            user=user,
            source_inbound_request_id=source_inbound_request_id,
        )
    except RequestRuleError as exc:
        raise _error(exc) from exc
    return [
        ReturnCandidateOut(
            box_id=box.id,
            box_number=box.box_number,
            lot=box.lot,
            contents=box.contents,
            status=box.status,
        )
        for box in candidates
    ]


@router.get("/{request_id}", response_model=BoxRequestOut)
def get_request(request_id: int, db: DbSession, user: CurrentUser) -> BoxRequestOut:
    return _request_out(db, _visible(db, user, request_id))


@router.post("/{request_id}/inbound-xlsx-preview", response_model=XlsxPreviewOut)
async def preview_inbound_workbook(
    request_id: int,
    db: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File(description="An XLSX workbook to map")],
) -> XlsxPreviewOut:
    request = _visible(db, user, request_id)
    if request.direction != BoxRequestDirection.inbound:
        raise HTTPException(status_code=409, detail="request is not an inbound order")
    if request.status != BoxRequestStatus.in_transit:
        raise HTTPException(
            status_code=409,
            detail="workbooks can only be mapped while accepting a delivery",
        )
    if user.role != UserRole.admin and request.requester_user_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="only the original requester can map the inbound workbook",
        )
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="only .xlsx files are supported")
    payload = await file.read(MAX_XLSX_BYTES + 1)
    try:
        sheets = preview_xlsx(payload)
    except BoxRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return XlsxPreviewOut(
        filename=filename,
        sheets=[
            XlsxPreviewSheet(
                name=sheet.name,
                max_columns=sheet.max_columns,
                rows=[
                    XlsxPreviewRow(
                        row_number=row.row_number,
                        cells=row.cells,
                    )
                    for row in sheet.rows
                ],
            )
            for sheet in sheets
        ],
    )


@router.get("/{request_id}/events", response_model=list[BoxRequestEventOut])
def request_events(
    request_id: int, db: DbSession, user: CurrentUser
) -> list[BoxRequestEventOut]:
    _visible(db, user, request_id)
    events = db.scalars(
        select(BoxRequestEvent)
        .where(BoxRequestEvent.request_id == request_id)
        .order_by(BoxRequestEvent.occurred_at.desc(), BoxRequestEvent.id.desc())
    ).all()
    user_ids = {event.user_id for event in events if event.user_id is not None}
    names = {
        row.id: row.display_name or row.email
        for row in db.scalars(select(User).where(User.id.in_(user_ids))).all()
    }
    return [
        BoxRequestEventOut(
            id=event.id,
            event_type=event.event_type,
            from_status=event.from_status,
            to_status=event.to_status,
            user_id=event.user_id,
            user_name=names.get(event.user_id),
            note=event.note,
            occurred_at=event.occurred_at,
        )
        for event in events
    ]


async def _run_action(db: DbSession, action, *args, **kwargs) -> BoxRequestOut:
    try:
        request = action(db, *args, **kwargs)
    except RequestRuleError as exc:
        db.rollback()
        raise _error(exc) from exc
    await _publish(request)
    return _request_out(db, request)


@router.post("/{request_id}/approve", response_model=BoxRequestOut)
async def approve(
    request_id: int,
    payload: RequestAction,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        approve_request,
        request_id=request_id,
        user=user,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/reject", response_model=BoxRequestOut)
async def reject(
    request_id: int,
    payload: RequestReject,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        reject_request,
        request_id=request_id,
        user=user,
        reason=payload.reason,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/start-transit", response_model=BoxRequestOut)
async def transit(
    request_id: int,
    payload: RequestAction,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        start_transit,
        request_id=request_id,
        user=user,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/cancel", response_model=BoxRequestOut)
async def cancel(
    request_id: int,
    payload: RequestCancel,
    db: DbSession,
    user: CurrentUser,
) -> BoxRequestOut:
    return await _run_action(
        db,
        cancel_request,
        request_id=request_id,
        user=user,
        reason=payload.reason,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/complete", response_model=BoxRequestOut)
async def complete(
    request_id: int,
    payload: RequestComplete,
    db: DbSession,
    background: BackgroundTasks,
    user: CurrentUser,
) -> BoxRequestOut:
    result = await _run_action(
        db,
        complete_request,
        request_id=request_id,
        user=user,
        inbound_items=payload.inbound_items,
        discrepancy_reason=payload.discrepancy_reason,
        expected_version=payload.expected_version,
    )
    await bus.publish(
        "box.updated",
        {"warehouse_id": result.warehouse_id, "request_id": result.id, "bulk": True},
    )
    background.add_task(evaluate_safe, db)
    return result


@router.get(
    "/{request_id}/documents", response_model=list[BoxRequestDocumentOut]
)
def list_documents(
    request_id: int, db: DbSession, user: CurrentUser
) -> list[BoxRequestDocumentOut]:
    _visible(db, user, request_id)
    documents = db.scalars(
        select(BoxRequestDocument)
        .where(BoxRequestDocument.request_id == request_id)
        .order_by(BoxRequestDocument.created_at.desc(), BoxRequestDocument.id.desc())
    ).all()
    return [BoxRequestDocumentOut.model_validate(document) for document in documents]


@router.post(
    "/{request_id}/documents",
    response_model=BoxRequestDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    request_id: int,
    db: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    document_type: Annotated[BoxRequestDocumentType, Form()],
    erp_reference: Annotated[str, Form(min_length=1, max_length=120)],
) -> BoxRequestDocumentOut:
    request = _visible(db, user, request_id)
    is_self_receipt = request.origin in (
        BoxRequestOrigin.xlsx_import,
        BoxRequestOrigin.manual_entry,
    )
    can_upload_self_receipt = (
        is_self_receipt
        and request.status == BoxRequestStatus.completed
        and (
            request.requester_user_id == user.id
            or user.role == UserRole.admin
        )
    )
    can_upload_workflow_document = (
        user.role in (UserRole.admin, UserRole.warehouse_mover)
        and request.status
        in (BoxRequestStatus.approved, BoxRequestStatus.in_transit)
    )
    if not can_upload_self_receipt and not can_upload_workflow_document:
        if (
            is_self_receipt
            and request.status == BoxRequestStatus.completed
        ) or user.role not in (UserRole.admin, UserRole.warehouse_mover):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only the receipt owner or an admin can upload documents",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="documents can only be added after approval or to a self receipt",
        )
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    content_type = (file.content_type or "").lower()
    if (
        content_type not in _ALLOWED_DOCUMENT_TYPES
        or suffix not in _ALLOWED_DOCUMENT_TYPES[content_type]
    ):
        raise HTTPException(
            status_code=415, detail="only PDF, JPEG, and PNG documents are accepted"
        )
    settings = get_settings()
    content = await file.read(settings.document_max_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail="document is empty")
    if len(content) > settings.document_max_bytes:
        raise HTTPException(status_code=413, detail="document exceeds configured size limit")
    if not _valid_document_signature(content_type, content):
        raise HTTPException(
            status_code=415, detail="document content does not match its declared type"
        )
    digest = hashlib.sha256(content).hexdigest()
    object_key = (
        f"requests/{request.id}/{document_type.value}/"
        f"{uuid.uuid4().hex}{suffix}"
    )
    try:
        put_document(
            object_key=object_key,
            content=content,
            content_type=content_type,
            metadata={
                "request-id": str(request.id),
                "sha256": digest,
            },
        )
    except StorageUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        current = db.scalars(
            select(BoxRequestDocument).where(
                BoxRequestDocument.request_id == request.id,
                BoxRequestDocument.document_type == document_type,
                BoxRequestDocument.is_current.is_(True),
            )
        ).all()
        for old in current:
            old.is_current = False
        document = BoxRequestDocument(
            request_id=request.id,
            document_type=document_type,
            erp_reference=erp_reference.strip(),
            object_key=object_key,
            original_filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            sha256=digest,
            uploaded_by_user_id=user.id,
            is_current=True,
        )
        db.add(document)
        db.flush()
        request.version += 1
        db.add(
            BoxRequestEvent(
                request_id=request.id,
                event_type=BoxRequestEventType.document_uploaded,
                from_status=request.status,
                to_status=request.status,
                user_id=user.id,
                note=f"{document_type.value}: {erp_reference.strip()} ({filename})",
            )
        )
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        delete_document(object_key)
        raise
    await _publish(request)
    return BoxRequestDocumentOut.model_validate(document)


@router.get("/{request_id}/documents/{document_id}/download")
@router.post("/{request_id}/documents/{document_id}/download")
def download_document(
    request_id: int,
    document_id: int,
    db: DbSession,
    user: CurrentUser,
) -> Response:
    _visible(db, user, request_id)
    document = db.get(BoxRequestDocument, document_id)
    if document is None or document.request_id != request_id:
        raise HTTPException(status_code=404, detail="document not found")
    try:
        body, length = get_document(document.object_key)
    except StorageUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        content = body.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="document download was interrupted",
        ) from exc
    finally:
        body.close()
    if length and len(content) != length:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="stored document size does not match object metadata",
        )
    if hashlib.sha256(content).hexdigest() != document.sha256:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="stored document failed its integrity check",
        )

    encoded_name = quote(document.original_filename)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
        "Content-Length": str(len(content)),
        "Cache-Control": "private, no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(
        content=content,
        media_type=document.content_type,
        headers=headers,
    )
