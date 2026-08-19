from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Literal
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
from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.deps import CurrentUser, DbSession, require_warehouse_mover
from app.events import bus, publish_notification_event
from app.models.notifications import RequestNotificationKind
from app.models.requests import (
    BoxRequest,
    BoxRequestAttachment,
    BoxRequestComment,
    BoxRequestDirection,
    BoxRequestDiscrepancy,
    BoxRequestDiscrepancyPhoto,
    BoxRequestDocument,
    BoxRequestDocumentType,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestExceptionKind,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxRequestPriority,
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
    InboundCompletionPreviewOut,
    InboundCompletionPreviewRequest,
    RequestAction,
    RequestAnalyticsOut,
    RequestAssigneeOut,
    RequestAttachmentOut,
    RequestCancel,
    RequestCommentCreate,
    RequestCommentOut,
    RequestComplete,
    RequestConflictOut,
    RequestCoordinationUpdate,
    RequestDiscrepancyOut,
    RequestDiscrepancyPhotoOut,
    RequestDraftSubmit,
    RequestExceptionOut,
    RequestFailedDelivery,
    RequestHold,
    RequestPermissionsOut,
    RequestReconciliationOut,
    RequestReject,
    RequestReschedule,
    RequestResume,
    RequestRetry,
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
from app.services.request_notifications import enqueue_request_event
from app.services.request_reporting import (
    RequestReportFilters,
    build_reconciliation,
    build_request_analytics,
)
from app.services.requests import (
    RequestAccessError,
    RequestConflictError,
    RequestRuleError,
    add_request_comment,
    approve_request,
    calculate_suggestion,
    cancel_request,
    complete_request,
    create_request,
    get_return_candidates,
    hold_request,
    list_return_sources,
    mark_awaiting_confirmation,
    mark_ready_for_transport,
    preview_inbound_completion,
    reject_request,
    report_failed_delivery,
    reschedule_request,
    resume_request,
    retry_transport,
    start_preparation,
    start_transit,
    submit_follow_up_draft,
    update_request_coordination,
)
from app.services.xlsx_preview import MAX_XLSX_BYTES, preview_xlsx

router = APIRouter(prefix="/requests", tags=["requests"])
_ALLOWED_DOCUMENT_TYPES = {
    "application/pdf": {".pdf"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
}
_ALLOWED_SUPPORT_TYPES = {
    **_ALLOWED_DOCUMENT_TYPES,
    "text/plain": {".txt"},
    "text/csv": {".csv"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
}


def _valid_document_signature(content_type: str, content: bytes) -> bool:
    if content_type == "application/pdf":
        return content.startswith(b"%PDF-")
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    return False


def _valid_support_signature(content_type: str, content: bytes) -> bool:
    if content_type in _ALLOWED_DOCUMENT_TYPES:
        return _valid_document_signature(content_type, content)
    if content_type in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        return content.startswith(b"PK\x03\x04")
    if content_type in ("text/plain", "text/csv"):
        return b"\x00" not in content[:4096]
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


def _is_coordinator(user: User, request: BoxRequest) -> bool:
    return user.role in (UserRole.admin, UserRole.warehouse_mover) and can_access(
        user, request.warehouse_id
    )


def _can_attach(user: User, request: BoxRequest) -> bool:
    return _is_coordinator(user, request) or request.requester_user_id == user.id


def _request_out(
    db: DbSession,
    request: BoxRequest,
    user: User,
    *,
    users: dict[int, User] | None = None,
    child_ids_by_parent: dict[int, list[int]] | None = None,
) -> BoxRequestOut:
    documents = sorted(
        request.documents,
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    comments = sorted(
        request.comments,
        key=lambda item: (item.created_at, item.id),
    )
    attachments = sorted(
        request.attachments,
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    user_ids = {
        user_id
        for user_id in (
            request.requester_user_id,
            request.assigned_mover_user_id,
        )
        if user_id is not None
    } | {
        comment.author_user_id
        for comment in comments
        if comment.author_user_id is not None
    }
    if users is None:
        users = {
            row.id: row
            for row in db.scalars(select(User).where(User.id.in_(user_ids))).all()
        }
    requester = users.get(request.requester_user_id)
    assignee = users.get(request.assigned_mover_user_id)
    comment_names = {
        user_id: row.display_name or row.email for user_id, row in users.items()
    }
    child_request_ids = (
        child_ids_by_parent.get(request.id, [])
        if child_ids_by_parent is not None
        else list(
            db.scalars(
                select(BoxRequest.id)
                .where(BoxRequest.parent_request_id == request.id)
                .order_by(BoxRequest.id.asc())
            ).all()
        )
    )
    operational_exceptions = sorted(
        request.operational_exceptions,
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    current_exception = next(
        (item for item in operational_exceptions if item.resolved_at is None),
        None,
    )
    is_coordinator = _is_coordinator(user, request)
    is_requester = request.requester_user_id == user.id
    can_confirm = request.status == BoxRequestStatus.awaiting_confirmation and (
        (
            request.direction == BoxRequestDirection.inbound
            and (is_requester or user.role == UserRole.admin)
        )
        or (
            request.direction == BoxRequestDirection.return_
            and is_coordinator
        )
    )
    return BoxRequestOut(
        id=request.id,
        direction=request.direction,
        warehouse_id=request.warehouse_id,
        target_warehouse_id=request.target_warehouse_id,
        quantity=request.quantity,
        status=request.status,
        requester_user_id=request.requester_user_id,
        requester_name=(
            (requester.display_name or requester.email)
            if requester is not None
            else "System receipt"
        ),
        priority=request.priority,
        requested_date=request.requested_date,
        scheduled_window_start=request.scheduled_window_start,
        scheduled_window_end=request.scheduled_window_end,
        sla_deadline=request.sla_deadline,
        assigned_mover_user_id=request.assigned_mover_user_id,
        assigned_mover_name=(
            assignee.display_name or assignee.email if assignee is not None else None
        ),
        destination_contact=request.destination_contact,
        internal_location=request.internal_location,
        special_handling_instructions=request.special_handling_instructions,
        source_inbound_request_id=request.source_inbound_request_id,
        parent_request_id=request.parent_request_id,
        root_request_id=request.root_request_id,
        child_request_ids=child_request_ids,
        origin=request.origin,
        suggestion_quantity=request.suggestion_quantity,
        current_available=request.current_available,
        min_inventory=request.min_inventory,
        pending_inbound=request.pending_inbound,
        eligible_return=request.eligible_return,
        recommendation_snapshot=request.recommendation_snapshot,
        actual_received_quantity=request.actual_received_quantity,
        variance_quantity=request.variance_quantity,
        rejection_reason=request.rejection_reason,
        cancellation_reason=request.cancellation_reason,
        discrepancy_reason=request.discrepancy_reason,
        receipt_restore_archived=request.receipt_restore_archived,
        receipt_quarantine=request.receipt_quarantine,
        receipt_document_required=request.receipt_document_required,
        submitted_at=request.submitted_at,
        approved_at=request.approved_at,
        approved_by_user_id=request.approved_by_user_id,
        preparing_at=request.preparing_at,
        preparing_by_user_id=request.preparing_by_user_id,
        ready_for_transport_at=request.ready_for_transport_at,
        ready_for_transport_by_user_id=request.ready_for_transport_by_user_id,
        in_transit_at=request.in_transit_at,
        in_transit_by_user_id=request.in_transit_by_user_id,
        awaiting_confirmation_at=request.awaiting_confirmation_at,
        awaiting_confirmation_by_user_id=request.awaiting_confirmation_by_user_id,
        completed_at=request.completed_at,
        completed_by_user_id=request.completed_by_user_id,
        created_at=request.created_at,
        updated_at=request.updated_at,
        version=request.version,
        items=[BoxRequestItemOut.model_validate(item) for item in request.items],
        documents=[
            BoxRequestDocumentOut.model_validate(document) for document in documents
        ],
        discrepancies=[
            RequestDiscrepancyOut.model_validate(discrepancy)
            for discrepancy in request.discrepancies
        ],
        comments=[
            RequestCommentOut(
                id=comment.id,
                author_user_id=comment.author_user_id,
                author_name=comment_names.get(comment.author_user_id, "Former user"),
                body=comment.body,
                created_at=comment.created_at,
            )
            for comment in comments
        ],
        attachments=[
            RequestAttachmentOut.model_validate(attachment)
            for attachment in attachments
        ],
        operational_exceptions=[
            RequestExceptionOut.model_validate(item)
            for item in operational_exceptions
        ],
        current_exception=(
            RequestExceptionOut.model_validate(current_exception)
            if current_exception is not None
            else None
        ),
        permissions=RequestPermissionsOut(
            can_assign=is_coordinator,
            can_schedule=is_coordinator,
            can_comment=can_access(user, request.warehouse_id),
            can_attach=_can_attach(user, request),
            can_prepare=(
                is_coordinator
                and current_exception is None
                and request.status == BoxRequestStatus.approved
            ),
            can_mark_ready=(
                is_coordinator
                and current_exception is None
                and request.status == BoxRequestStatus.preparing
            ),
            can_start_transit=(
                is_coordinator
                and current_exception is None
                and request.status == BoxRequestStatus.ready_for_transport
            ),
            can_mark_arrived=(
                is_coordinator
                and current_exception is None
                and request.status == BoxRequestStatus.in_transit
            ),
            can_confirm=current_exception is None and can_confirm,
            can_hold=(
                is_coordinator
                and current_exception is None
                and request.status
                in (
                    BoxRequestStatus.submitted,
                    BoxRequestStatus.approved,
                    BoxRequestStatus.preparing,
                    BoxRequestStatus.ready_for_transport,
                    BoxRequestStatus.in_transit,
                    BoxRequestStatus.awaiting_confirmation,
                )
            ),
            can_resume=(
                is_coordinator
                and current_exception is not None
                and current_exception.exception_kind == BoxRequestExceptionKind.hold
            ),
            can_reschedule=(
                is_coordinator
                and current_exception is None
                and request.status
                in (
                    BoxRequestStatus.submitted,
                    BoxRequestStatus.approved,
                    BoxRequestStatus.preparing,
                    BoxRequestStatus.ready_for_transport,
                    BoxRequestStatus.in_transit,
                    BoxRequestStatus.awaiting_confirmation,
                )
            ),
            can_report_failed=(
                is_coordinator
                and current_exception is None
                and request.status == BoxRequestStatus.in_transit
            ),
            can_retry=(
                is_coordinator
                and current_exception is not None
                and current_exception.exception_kind
                == BoxRequestExceptionKind.failed_delivery
            ),
        ),
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
            "source_warehouse_id": request.warehouse_id,
            "target_warehouse_id": request.target_warehouse_id,
            "request_id": request.id,
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
    priority: BoxRequestPriority | None = None,
    origin: BoxRequestOrigin | None = None,
    assigned_mover_user_id: int | None = Query(default=None, ge=1),
    queue: Literal[
        "unassigned",
        "due_today",
        "overdue",
        "ready_for_transport",
        "awaiting_confirmation",
        "awaiting_acceptance",
        "pending_receipt_review",
    ]
    | None = None,
    search: str | None = Query(default=None, max_length=200),
    sort_by: Literal[
        "created_at",
        "updated_at",
        "priority",
        "requested_date",
        "sla_deadline",
        "scheduled_window_start",
        "status",
        "assignment",
    ] = "created_at",
    sort_dir: Literal["asc", "desc"] = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[BoxRequestOut]:
    requester = aliased(User)
    assignee = aliased(User)
    stmt = (
        select(BoxRequest)
        .join(requester, requester.id == BoxRequest.requester_user_id, isouter=True)
        .join(assignee, assignee.id == BoxRequest.assigned_mover_user_id, isouter=True)
    )
    if warehouse_id is not None:
        stmt = stmt.where(BoxRequest.warehouse_id == warehouse_id)
    if direction is not None:
        stmt = stmt.where(BoxRequest.direction == direction)
    if request_status is not None:
        stmt = stmt.where(BoxRequest.status == request_status)
    if priority is not None:
        stmt = stmt.where(BoxRequest.priority == priority)
    if origin is not None:
        stmt = stmt.where(BoxRequest.origin == origin)
    if assigned_mover_user_id is not None:
        stmt = stmt.where(
            BoxRequest.assigned_mover_user_id == assigned_mover_user_id
        )
    now = datetime.now(UTC)
    active = BoxRequest.status.in_(
        (
            BoxRequestStatus.draft,
            BoxRequestStatus.submitted,
            BoxRequestStatus.approved,
            BoxRequestStatus.preparing,
            BoxRequestStatus.ready_for_transport,
            BoxRequestStatus.in_transit,
            BoxRequestStatus.awaiting_confirmation,
        )
    )
    if queue == "unassigned":
        stmt = stmt.where(active, BoxRequest.assigned_mover_user_id.is_(None))
    elif queue == "due_today":
        day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
        stmt = stmt.where(
            active,
            or_(
                BoxRequest.requested_date == now.date(),
                and_(
                    BoxRequest.sla_deadline >= day_start,
                    BoxRequest.sla_deadline < day_start + timedelta(days=1),
                ),
            ),
        )
    elif queue == "overdue":
        stmt = stmt.where(active, BoxRequest.sla_deadline < now)
    elif queue == "ready_for_transport":
        stmt = stmt.where(BoxRequest.status == BoxRequestStatus.ready_for_transport)
    elif queue in ("awaiting_confirmation", "awaiting_acceptance"):
        stmt = stmt.where(
            BoxRequest.status == BoxRequestStatus.awaiting_confirmation
        )
    elif queue == "pending_receipt_review":
        stmt = stmt.where(
            BoxRequest.status == BoxRequestStatus.submitted,
            BoxRequest.origin.in_(
                (BoxRequestOrigin.xlsx_import, BoxRequestOrigin.manual_entry)
            ),
        )
    if search and search.strip():
        needle = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                cast(BoxRequest.id, String).ilike(needle),
                requester.display_name.ilike(needle),
                requester.email.ilike(needle),
                assignee.display_name.ilike(needle),
                assignee.email.ilike(needle),
                BoxRequest.destination_contact.ilike(needle),
                BoxRequest.internal_location.ilike(needle),
                BoxRequest.special_handling_instructions.ilike(needle),
                BoxRequest.id.in_(
                    select(BoxRequestItem.request_id).where(
                        or_(
                            BoxRequestItem.pallet.ilike(needle),
                            cast(BoxRequestItem.pallet_id, String).ilike(needle),
                        )
                    )
                ),
            )
        )
    stmt = apply_warehouse_filter(stmt, user, BoxRequest.warehouse_id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    priority_order = case(
        (BoxRequest.priority == BoxRequestPriority.urgent, 4),
        (BoxRequest.priority == BoxRequestPriority.high, 3),
        (BoxRequest.priority == BoxRequestPriority.normal, 2),
        else_=1,
    )
    sort_columns = {
        "created_at": BoxRequest.created_at,
        "updated_at": BoxRequest.updated_at,
        "priority": priority_order,
        "requested_date": BoxRequest.requested_date,
        "sla_deadline": BoxRequest.sla_deadline,
        "scheduled_window_start": BoxRequest.scheduled_window_start,
        "status": BoxRequest.status,
        "assignment": BoxRequest.assigned_mover_user_id,
    }
    sort_column = sort_columns[sort_by]
    ordering = sort_column.asc() if sort_dir == "asc" else sort_column.desc()
    requests = db.scalars(
        stmt.order_by(ordering, BoxRequest.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    request_ids = [request.id for request in requests]
    related_user_ids = {
        user_id
        for request in requests
        for user_id in (
            request.requester_user_id,
            request.assigned_mover_user_id,
            *(comment.author_user_id for comment in request.comments),
        )
        if user_id is not None
    }
    users = {
        row.id: row
        for row in db.scalars(select(User).where(User.id.in_(related_user_ids))).all()
    }
    child_ids_by_parent: dict[int, list[int]] = {}
    for child_id, parent_id in db.execute(
        select(BoxRequest.id, BoxRequest.parent_request_id)
        .where(BoxRequest.parent_request_id.in_(request_ids))
        .order_by(BoxRequest.id)
    ).all():
        if parent_id is not None:
            child_ids_by_parent.setdefault(parent_id, []).append(child_id)
    return Page[BoxRequestOut](
        items=[
            _request_out(
                db,
                request,
                user,
                users=users,
                child_ids_by_parent=child_ids_by_parent,
            )
            for request in requests
        ],
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


@router.get("/assignees", response_model=list[RequestAssigneeOut])
def list_request_assignees(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: Annotated[int, Query(ge=1)],
) -> list[RequestAssigneeOut]:
    if user.role not in (UserRole.admin, UserRole.warehouse_mover) or not can_access(
        user, warehouse_id
    ):
        raise HTTPException(status_code=403, detail="request coordinator role required")
    candidates = db.scalars(
        select(User)
        .where(
            User.is_active.is_(True),
            User.role.in_((UserRole.admin, UserRole.warehouse_mover)),
        )
        .order_by(User.display_name, User.email)
    ).all()
    return [
        RequestAssigneeOut(
            id=candidate.id,
            display_name=candidate.display_name or candidate.email,
            email=candidate.email,
            role=candidate.role.value,
        )
        for candidate in candidates
        if can_access(candidate, warehouse_id)
    ]


def _report_filters(
    warehouse_id: int | None,
    assigned_mover_user_id: int | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> RequestReportFilters:
    if from_at is not None and to_at is not None and to_at <= from_at:
        raise HTTPException(status_code=400, detail="to_at must be after from_at")
    return RequestReportFilters(
        warehouse_id=warehouse_id,
        assigned_mover_user_id=assigned_mover_user_id,
        from_at=from_at,
        to_at=to_at,
    )


@router.get("/reconciliation", response_model=RequestReconciliationOut)
def request_reconciliation(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    assigned_mover_user_id: int | None = Query(default=None, ge=1),
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    severity: Literal["critical", "high", "medium", "low"] | None = None,
    issue_type: str | None = Query(default=None, max_length=80),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> RequestReconciliationOut:
    return build_reconciliation(
        db,
        user=user,
        filters=_report_filters(
            warehouse_id, assigned_mover_user_id, from_at, to_at
        ),
        severity=severity,
        issue_type=issue_type,
        page=page,
        page_size=page_size,
    )


@router.get("/analytics", response_model=RequestAnalyticsOut)
def request_analytics(
    db: DbSession,
    user: CurrentUser,
    warehouse_id: int | None = Query(default=None, ge=1),
    assigned_mover_user_id: int | None = Query(default=None, ge=1),
    from_at: datetime | None = None,
    to_at: datetime | None = None,
) -> RequestAnalyticsOut:
    return build_request_analytics(
        db,
        user=user,
        filters=_report_filters(
            warehouse_id, assigned_mover_user_id, from_at, to_at
        ),
    )


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
            target_warehouse_id=payload.target_warehouse_id,
            direction=payload.direction,
            quantity=payload.quantity,
            source_inbound_request_id=payload.source_inbound_request_id,
            box_ids=payload.box_ids,
            priority=payload.priority,
            requested_date=payload.requested_date,
            sla_deadline=payload.sla_deadline,
            destination_contact=payload.destination_contact,
            internal_location=payload.internal_location,
            special_handling_instructions=payload.special_handling_instructions,
        )
    except RequestRuleError as exc:
        raise _error(exc) from exc
    await _publish(request, "request.created")
    await publish_notification_event(
        warehouse_id=request.warehouse_id, request_id=request.id
    )
    return _request_out(db, request, user)


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
            lot_id=box.lot_id,
            pallet_id=box.pallet_id,
            pallet_number=box.pallet_number,
            contents=box.contents,
            status=box.status,
        )
        for box in candidates
    ]


@router.get("/{request_id}", response_model=BoxRequestOut)
def get_request(request_id: int, db: DbSession, user: CurrentUser) -> BoxRequestOut:
    return _request_out(db, _visible(db, user, request_id), user)


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
    if request.status != BoxRequestStatus.awaiting_confirmation:
        raise HTTPException(
            status_code=409,
            detail="workbooks can only be mapped while confirming a delivered request",
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
            metadata=event.event_metadata or {},
            occurred_at=event.occurred_at,
        )
        for event in events
    ]


@router.patch("/{request_id}/coordination", response_model=BoxRequestOut)
async def coordinate_request(
    request_id: int,
    payload: RequestCoordinationUpdate,
    db: DbSession,
    user: CurrentUser,
) -> BoxRequestOut:
    changes = payload.model_dump(
        exclude={"expected_version"},
        include=payload.model_fields_set - {"expected_version"},
    )
    return await _run_action(
        db,
        update_request_coordination,
        request_id=request_id,
        user=user,
        expected_version=payload.expected_version,
        changes=changes,
    )


@router.get("/{request_id}/comments", response_model=list[RequestCommentOut])
def list_request_comments(
    request_id: int, db: DbSession, user: CurrentUser
) -> list[RequestCommentOut]:
    _visible(db, user, request_id)
    comments = db.scalars(
        select(BoxRequestComment)
        .where(BoxRequestComment.request_id == request_id)
        .order_by(BoxRequestComment.created_at, BoxRequestComment.id)
    ).all()
    author_ids = {
        comment.author_user_id
        for comment in comments
        if comment.author_user_id is not None
    }
    names = {
        row.id: row.display_name or row.email
        for row in db.scalars(select(User).where(User.id.in_(author_ids))).all()
    }
    return [
        RequestCommentOut(
            id=comment.id,
            author_user_id=comment.author_user_id,
            author_name=names.get(comment.author_user_id, "Former user"),
            body=comment.body,
            created_at=comment.created_at,
        )
        for comment in comments
    ]


@router.post(
    "/{request_id}/comments",
    response_model=RequestCommentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_request_comment(
    request_id: int,
    payload: RequestCommentCreate,
    db: DbSession,
    user: CurrentUser,
) -> RequestCommentOut:
    try:
        comment = add_request_comment(
            db,
            request_id=request_id,
            user=user,
            expected_version=payload.expected_version,
            body=payload.body,
        )
    except RequestRuleError as exc:
        db.rollback()
        if isinstance(exc, RequestConflictError):
            latest = db.get(BoxRequest, request_id)
            if latest is not None:
                raise HTTPException(
                    status_code=409,
                    detail=_conflict_payload(db, latest, str(exc)).model_dump(
                        mode="json"
                    ),
                ) from exc
        raise _error(exc) from exc
    await _publish(_visible(db, user, request_id))
    await publish_notification_event(
        warehouse_id=_visible(db, user, request_id).warehouse_id,
        request_id=request_id,
    )
    return RequestCommentOut(
        id=comment.id,
        author_user_id=comment.author_user_id,
        author_name=user.display_name or user.email,
        body=comment.body,
        created_at=comment.created_at,
    )


def _conflict_payload(
    db: DbSession, request: BoxRequest, message: str
) -> RequestConflictOut:
    events = db.scalars(
        select(BoxRequestEvent)
        .where(BoxRequestEvent.request_id == request.id)
        .order_by(BoxRequestEvent.occurred_at.desc(), BoxRequestEvent.id.desc())
        .limit(5)
    ).all()
    user_ids = {event.user_id for event in events if event.user_id is not None}
    names = {
        row.id: row.display_name or row.email
        for row in db.scalars(select(User).where(User.id.in_(user_ids))).all()
    }
    return RequestConflictOut(
        message=message,
        request_id=request.id,
        latest_version=request.version,
        latest_status=request.status,
        relevant_events=[
            BoxRequestEventOut(
                id=event.id,
                event_type=event.event_type,
                from_status=event.from_status,
                to_status=event.to_status,
                user_id=event.user_id,
                user_name=names.get(event.user_id),
                note=event.note,
                metadata=event.event_metadata or {},
                occurred_at=event.occurred_at,
            )
            for event in events
        ],
    )


async def _run_action(db: DbSession, action, *args, **kwargs) -> BoxRequestOut:
    try:
        request = action(db, *args, **kwargs)
    except RequestRuleError as exc:
        db.rollback()
        if isinstance(exc, RequestConflictError) and "request_id" in kwargs:
            request_id = int(kwargs["request_id"])
            latest = db.get(BoxRequest, request_id)
            if latest is not None:
                detail = _conflict_payload(db, latest, str(exc))
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=detail.model_dump(mode="json"),
                ) from exc
        raise _error(exc) from exc
    await _publish(request)
    await publish_notification_event(
        warehouse_id=request.warehouse_id, request_id=request.id
    )
    return _request_out(db, request, kwargs["user"])


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


@router.post("/{request_id}/prepare", response_model=BoxRequestOut)
async def prepare(
    request_id: int,
    payload: RequestAction,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        start_preparation,
        request_id=request_id,
        user=user,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/mark-ready", response_model=BoxRequestOut)
async def mark_ready(
    request_id: int,
    payload: RequestAction,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        mark_ready_for_transport,
        request_id=request_id,
        user=user,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/mark-arrived", response_model=BoxRequestOut)
async def mark_arrived(
    request_id: int,
    payload: RequestAction,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        mark_awaiting_confirmation,
        request_id=request_id,
        user=user,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/hold", response_model=BoxRequestOut)
async def hold(
    request_id: int,
    payload: RequestHold,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        hold_request,
        request_id=request_id,
        user=user,
        reason=payload.reason,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/resume", response_model=BoxRequestOut)
async def resume(
    request_id: int,
    payload: RequestResume,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        resume_request,
        request_id=request_id,
        user=user,
        resolution=payload.resolution,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/reschedule", response_model=BoxRequestOut)
async def reschedule(
    request_id: int,
    payload: RequestReschedule,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        reschedule_request,
        request_id=request_id,
        user=user,
        reason=payload.reason,
        revised_window_start=payload.revised_window_start,
        revised_window_end=payload.revised_window_end,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/report-failed-delivery", response_model=BoxRequestOut)
async def report_failure(
    request_id: int,
    payload: RequestFailedDelivery,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        report_failed_delivery,
        request_id=request_id,
        user=user,
        reason=payload.reason,
        revised_window_start=payload.revised_window_start,
        revised_window_end=payload.revised_window_end,
        expected_version=payload.expected_version,
    )


@router.post("/{request_id}/retry-transport", response_model=BoxRequestOut)
async def retry(
    request_id: int,
    payload: RequestRetry,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_warehouse_mover)],
) -> BoxRequestOut:
    return await _run_action(
        db,
        retry_transport,
        request_id=request_id,
        user=user,
        resolution=payload.resolution,
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


@router.post(
    "/{request_id}/inbound-completion-preview",
    response_model=InboundCompletionPreviewOut,
)
def inbound_completion_preview(
    request_id: int,
    payload: InboundCompletionPreviewRequest,
    db: DbSession,
    user: CurrentUser,
) -> InboundCompletionPreviewOut:
    try:
        return preview_inbound_completion(
            db,
            request_id=request_id,
            user=user,
            inbound_items=payload.inbound_items,
        )
    except RequestRuleError as exc:
        raise _error(exc) from exc


@router.post("/{request_id}/complete", response_model=BoxRequestOut)
async def complete(
    request_id: int,
    payload: RequestComplete,
    db: DbSession,
    background: BackgroundTasks,
    user: CurrentUser,
) -> BoxRequestOut:
    affected_warehouse_ids: set[int] = set()
    affected_pallet_warehouse_ids: dict[int, set[int]] = {}
    result = await _run_action(
        db,
        complete_request,
        request_id=request_id,
        user=user,
        inbound_items=payload.inbound_items,
        accept_existing_received_boxes=payload.accept_existing_received_boxes,
        inbound_impact_signature=payload.inbound_impact_signature,
        collected_box_ids=payload.collected_box_ids,
        discrepancies=payload.discrepancies,
        discrepancy_reason=payload.discrepancy_reason,
        expected_version=payload.expected_version,
        idempotency_key=payload.idempotency_key,
        affected_warehouse_ids=affected_warehouse_ids,
        affected_pallet_warehouse_ids=affected_pallet_warehouse_ids,
    )
    inbound_target_id = (
        result.warehouse_id
        if result.direction == BoxRequestDirection.inbound
        else None
    )
    inbound_source_ids = sorted(
        affected_warehouse_ids - {inbound_target_id}
        if inbound_target_id is not None
        else set()
    )
    for warehouse_id in sorted(affected_warehouse_ids):
        source_warehouse_id = (
            (
                warehouse_id
                if warehouse_id in inbound_source_ids
                else inbound_source_ids[0] if len(inbound_source_ids) == 1 else None
            )
            if result.direction == BoxRequestDirection.inbound
            else result.warehouse_id
        )
        await bus.publish(
            "box.updated",
            {
                "warehouse_id": warehouse_id,
                "source_warehouse_id": source_warehouse_id,
                "source_warehouse_ids": inbound_source_ids,
                "target_warehouse_id": (
                    inbound_target_id
                    if inbound_target_id is not None
                    else result.target_warehouse_id
                ),
                "request_id": result.id,
                "bulk": True,
            },
        )
    if (
        not affected_pallet_warehouse_ids
        and result.direction != BoxRequestDirection.inbound
    ):
        for pallet_id in {
            item.pallet_id for item in result.items if item.pallet_id is not None
        }:
            affected_pallet_warehouse_ids[pallet_id] = set(
                affected_warehouse_ids
            )
    for pallet_id, warehouse_ids in sorted(
        affected_pallet_warehouse_ids.items()
    ):
        for warehouse_id in sorted(warehouse_ids):
            await bus.publish(
                "pallet.updated",
                {
                    "id": pallet_id,
                    "warehouse_id": warehouse_id,
                    "request_id": result.id,
                },
            )
    background.add_task(evaluate_safe, db)
    return result


@router.post("/{request_id}/submit-draft", response_model=BoxRequestOut)
async def submit_draft(
    request_id: int,
    payload: RequestDraftSubmit,
    db: DbSession,
    user: CurrentUser,
) -> BoxRequestOut:
    return await _run_action(
        db,
        submit_follow_up_draft,
        request_id=request_id,
        user=user,
        box_ids=payload.box_ids,
        expected_version=payload.expected_version,
    )


@router.get(
    "/{request_id}/discrepancies", response_model=list[RequestDiscrepancyOut]
)
def list_discrepancies(
    request_id: int, db: DbSession, user: CurrentUser
) -> list[RequestDiscrepancyOut]:
    _visible(db, user, request_id)
    discrepancies = db.scalars(
        select(BoxRequestDiscrepancy)
        .where(BoxRequestDiscrepancy.request_id == request_id)
        .order_by(BoxRequestDiscrepancy.created_at.asc(), BoxRequestDiscrepancy.id.asc())
    ).all()
    return [RequestDiscrepancyOut.model_validate(item) for item in discrepancies]


@router.post(
    "/{request_id}/discrepancies/{discrepancy_id}/photos",
    response_model=RequestDiscrepancyPhotoOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_discrepancy_photo(
    request_id: int,
    discrepancy_id: int,
    db: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    expected_version: Annotated[int, Form(ge=1)],
) -> RequestDiscrepancyPhotoOut:
    request = db.scalar(
        select(BoxRequest).where(BoxRequest.id == request_id).with_for_update()
    )
    if request is None or not can_access(user, request.warehouse_id):
        raise HTTPException(status_code=404, detail="not found")
    discrepancy = db.get(BoxRequestDiscrepancy, discrepancy_id)
    if discrepancy is None or discrepancy.request_id != request.id:
        raise HTTPException(status_code=404, detail="discrepancy not found")
    if request.version != expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_payload(
                db, request, "request changed; refresh and retry"
            ).model_dump(mode="json"),
        )
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    content_type = (file.content_type or "").lower()
    allowed = {"image/jpeg": {".jpg", ".jpeg"}, "image/png": {".png"}}
    if content_type not in allowed or suffix not in allowed[content_type]:
        raise HTTPException(status_code=415, detail="only JPEG and PNG photos are accepted")
    settings = get_settings()
    content = await file.read(settings.document_max_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail="photo is empty")
    if len(content) > settings.document_max_bytes:
        raise HTTPException(status_code=413, detail="photo exceeds configured size limit")
    if not _valid_document_signature(content_type, content):
        raise HTTPException(
            status_code=415, detail="photo content does not match its declared type"
        )
    digest = hashlib.sha256(content).hexdigest()
    object_key = (
        f"requests/{request.id}/discrepancies/{discrepancy.id}/"
        f"{uuid.uuid4().hex}{suffix}"
    )
    try:
        put_document(
            object_key=object_key,
            content=content,
            content_type=content_type,
            metadata={
                "request-id": str(request.id),
                "discrepancy-id": str(discrepancy.id),
                "sha256": digest,
            },
        )
    except StorageUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        photo = BoxRequestDiscrepancyPhoto(
            discrepancy_id=discrepancy.id,
            object_key=object_key,
            original_filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            sha256=digest,
            uploaded_by_user_id=user.id,
        )
        db.add(photo)
        db.flush()
        request.version += 1
        db.add(
            BoxRequestEvent(
                request_id=request.id,
                event_type=BoxRequestEventType.discrepancy_photo_uploaded,
                from_status=request.status,
                to_status=request.status,
                user_id=user.id,
                note=f"Photo attached to {discrepancy.discrepancy_type.value} discrepancy.",
                event_metadata={
                    "discrepancy_id": discrepancy.id,
                    "discrepancy_type": discrepancy.discrepancy_type.value,
                    "photo_id": photo.id,
                },
            )
        )
        db.commit()
        db.refresh(photo)
    except Exception:
        db.rollback()
        delete_document(object_key)
        raise
    await _publish(request)
    return RequestDiscrepancyPhotoOut.model_validate(photo)


@router.get(
    "/{request_id}/discrepancies/{discrepancy_id}/photos/{photo_id}/download"
)
def download_discrepancy_photo(
    request_id: int,
    discrepancy_id: int,
    photo_id: int,
    db: DbSession,
    user: CurrentUser,
) -> Response:
    _visible(db, user, request_id)
    discrepancy = db.get(BoxRequestDiscrepancy, discrepancy_id)
    photo = db.get(BoxRequestDiscrepancyPhoto, photo_id)
    if (
        discrepancy is None
        or discrepancy.request_id != request_id
        or photo is None
        or photo.discrepancy_id != discrepancy_id
    ):
        raise HTTPException(status_code=404, detail="photo not found")
    try:
        body, length = get_document(photo.object_key)
        content = body.read()
    except StorageUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if "body" in locals():
            body.close()
    if (length and len(content) != length) or hashlib.sha256(content).hexdigest() != photo.sha256:
        raise HTTPException(status_code=502, detail="stored photo failed its integrity check")
    return Response(
        content=content,
        media_type=photo.content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(photo.original_filename)}",
            "Content-Length": str(len(content)),
            "Cache-Control": "private, no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/{request_id}/attachments", response_model=list[RequestAttachmentOut]
)
def list_request_attachments(
    request_id: int, db: DbSession, user: CurrentUser
) -> list[RequestAttachmentOut]:
    _visible(db, user, request_id)
    rows = db.scalars(
        select(BoxRequestAttachment)
        .where(BoxRequestAttachment.request_id == request_id)
        .order_by(
            BoxRequestAttachment.created_at.desc(),
            BoxRequestAttachment.id.desc(),
        )
    ).all()
    return [RequestAttachmentOut.model_validate(row) for row in rows]


@router.post(
    "/{request_id}/attachments",
    response_model=RequestAttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_request_attachment(
    request_id: int,
    db: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    expected_version: Annotated[int, Form(ge=1)],
) -> RequestAttachmentOut:
    request = db.scalar(
        select(BoxRequest).where(BoxRequest.id == request_id).with_for_update()
    )
    if request is None or not can_access(user, request.warehouse_id):
        raise HTTPException(status_code=404, detail="not found")
    if not _can_attach(user, request):
        raise HTTPException(
            status_code=403,
            detail="only the requester or a request coordinator can add attachments",
        )
    if request.version != expected_version:
        raise HTTPException(
            status_code=409,
            detail=_conflict_payload(
                db, request, "request changed; refresh and retry"
            ).model_dump(mode="json"),
        )
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    content_type = (file.content_type or "").lower()
    if (
        content_type not in _ALLOWED_SUPPORT_TYPES
        or suffix not in _ALLOWED_SUPPORT_TYPES[content_type]
    ):
        raise HTTPException(
            status_code=415,
            detail="unsupported attachment type",
        )
    settings = get_settings()
    content = await file.read(settings.document_max_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail="attachment is empty")
    if len(content) > settings.document_max_bytes:
        raise HTTPException(status_code=413, detail="attachment exceeds size limit")
    if not _valid_support_signature(content_type, content):
        raise HTTPException(
            status_code=415,
            detail="attachment content does not match its declared type",
        )
    digest = hashlib.sha256(content).hexdigest()
    object_key = f"requests/{request.id}/support/{uuid.uuid4().hex}{suffix}"
    try:
        put_document(
            object_key=object_key,
            content=content,
            content_type=content_type,
            metadata={"request-id": str(request.id), "sha256": digest},
        )
    except StorageUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        attachment = BoxRequestAttachment(
            request_id=request.id,
            object_key=object_key,
            original_filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            sha256=digest,
            uploaded_by_user_id=user.id,
        )
        db.add(attachment)
        db.flush()
        request.version += 1
        request.updated_at = datetime.now(UTC)
        db.add(
            BoxRequestEvent(
                request_id=request.id,
                event_type=BoxRequestEventType.attachment_uploaded,
                from_status=request.status,
                to_status=request.status,
                user_id=user.id,
                note=filename,
                event_metadata={
                    "attachment_id": attachment.id,
                    "filename": filename,
                },
            )
        )
        enqueue_request_event(
            db,
            request=request,
            kind=RequestNotificationKind.attachment_uploaded,
            event_token=f"attachment:{request.version}",
            detail=filename,
            exclude_user_id=user.id,
        )
        db.commit()
        db.refresh(attachment)
    except Exception:
        db.rollback()
        delete_document(object_key)
        raise
    await _publish(request)
    await publish_notification_event(
        warehouse_id=request.warehouse_id, request_id=request.id
    )
    return RequestAttachmentOut.model_validate(attachment)


@router.get("/{request_id}/attachments/{attachment_id}/download")
def download_request_attachment(
    request_id: int,
    attachment_id: int,
    db: DbSession,
    user: CurrentUser,
) -> Response:
    _visible(db, user, request_id)
    attachment = db.get(BoxRequestAttachment, attachment_id)
    if attachment is None or attachment.request_id != request_id:
        raise HTTPException(status_code=404, detail="attachment not found")
    try:
        body, length = get_document(attachment.object_key)
        content = body.read()
    except StorageUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if "body" in locals():
            body.close()
    if (length and len(content) != length) or hashlib.sha256(
        content
    ).hexdigest() != attachment.sha256:
        raise HTTPException(status_code=502, detail="stored attachment failed integrity check")
    return Response(
        content=content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(attachment.original_filename)}"
            ),
            "Content-Length": str(len(content)),
            "Cache-Control": "private, no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
    expected_version: Annotated[int, Form(ge=1)],
) -> BoxRequestDocumentOut:
    request = db.scalar(
        select(BoxRequest).where(BoxRequest.id == request_id).with_for_update()
    )
    if request is None or not can_access(user, request.warehouse_id):
        raise HTTPException(status_code=404, detail="not found")
    if request.version != expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_payload(
                db, request, "request changed; refresh and retry"
            ).model_dump(mode="json"),
        )
    is_self_receipt = request.origin in (
        BoxRequestOrigin.xlsx_import,
        BoxRequestOrigin.manual_entry,
    )
    can_upload_self_receipt = (
        is_self_receipt
        and request.status in (BoxRequestStatus.submitted, BoxRequestStatus.completed)
        and (
            request.requester_user_id == user.id
            or user.role == UserRole.admin
        )
    )
    can_upload_workflow_document = (
        user.role in (UserRole.admin, UserRole.warehouse_mover)
        and request.status
        in (
            BoxRequestStatus.approved,
            BoxRequestStatus.preparing,
            BoxRequestStatus.ready_for_transport,
            BoxRequestStatus.in_transit,
            BoxRequestStatus.awaiting_confirmation,
        )
    )
    if not can_upload_self_receipt and not can_upload_workflow_document:
        if (
            is_self_receipt
            and request.status in (BoxRequestStatus.submitted, BoxRequestStatus.completed)
        ) or user.role not in (UserRole.admin, UserRole.warehouse_mover):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only the receipt owner or an admin can upload documents",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="documents can only be added after approval or to a self receipt",
        )
    if is_self_receipt and document_type != BoxRequestDocumentType.delivery_note:
        raise HTTPException(
            status_code=400,
            detail="self receipts require a delivery_note ERP document",
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
                event_metadata={
                    "document_id": document.id,
                    "document_type": document_type.value,
                    "erp_reference": erp_reference.strip(),
                    "superseded_document_ids": [old.id for old in current],
                    "filename": filename,
                },
            )
        )
        enqueue_request_event(
            db,
            request=request,
            kind=RequestNotificationKind.document_uploaded,
            event_token=f"document:{request.version}",
            detail=f"{document_type.value}: {filename}",
            exclude_user_id=user.id,
        )
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        delete_document(object_key)
        raise
    await _publish(request)
    await publish_notification_event(
        warehouse_id=request.warehouse_id, request_id=request.id
    )
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
