from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.boxes import AVAILABLE_STATUSES, Box, BoxStatus
from app.models.requests import (
    ACTIVE_REQUEST_STATUSES,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestDocument,
    BoxRequestDocumentType,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxRequestStatus,
)
from app.models.users import User, UserRole
from app.models.warehouses import Warehouse
from app.schemas.requests import InboundBoxItem
from app.services.acl import can_access
from app.services.boxes import (
    BoxRuleError,
    create_box,
    normalize_box_number,
    update_box,
)
from app.services.warehouses import WarehouseRuleError, ensure_active_warehouse


class RequestRuleError(Exception):
    pass


class RequestAccessError(RequestRuleError):
    pass


class RequestConflictError(RequestRuleError):
    pass


@dataclass(frozen=True)
class Suggestion:
    direction: BoxRequestDirection
    warehouse_id: int
    current_available: int
    min_inventory: int
    pending_inbound: int
    suggested_quantity: int
    eligible_return: int


@dataclass(frozen=True)
class ReturnSource:
    id: int
    warehouse_id: int
    completed_at: datetime
    origin: BoxRequestOrigin
    delivered_quantity: int
    eligible_quantity: int


def _warehouse(db: Session, warehouse_id: int, user: User) -> Warehouse:
    try:
        warehouse = ensure_active_warehouse(db, warehouse_id)
    except WarehouseRuleError as exc:
        raise RequestRuleError(str(exc)) from exc
    if not can_access(user, warehouse_id):
        raise RequestAccessError("no access to warehouse")
    return warehouse


def calculate_suggestion(
    db: Session,
    *,
    user: User,
    warehouse_id: int,
    direction: BoxRequestDirection,
) -> Suggestion:
    warehouse = _warehouse(db, warehouse_id, user)
    current_available = int(
        db.scalar(
            select(func.count(Box.id)).where(
                Box.current_warehouse_id == warehouse_id,
                Box.status.in_(AVAILABLE_STATUSES),
                Box.archived_at.is_(None),
            )
        )
        or 0
    )
    pending_inbound = int(
        db.scalar(
            select(func.coalesce(func.sum(BoxRequest.quantity), 0)).where(
                BoxRequest.warehouse_id == warehouse_id,
                BoxRequest.direction == BoxRequestDirection.inbound,
                BoxRequest.status.in_(ACTIVE_REQUEST_STATUSES),
            )
        )
        or 0
    )
    allocated_return_ids = (
        select(BoxRequestItem.box_id)
        .join(BoxRequest, BoxRequest.id == BoxRequestItem.request_id)
        .where(
            BoxRequest.warehouse_id == warehouse_id,
            BoxRequest.direction == BoxRequestDirection.return_,
            BoxRequest.status.in_(ACTIVE_REQUEST_STATUSES),
            BoxRequestItem.box_id.is_not(None),
        )
    )
    eligible_return = int(
        db.scalar(
            select(func.count(Box.id)).where(
                Box.current_warehouse_id == warehouse_id,
                Box.status == BoxStatus.ready_to_return,
                Box.archived_at.is_(None),
                Box.id.not_in(allocated_return_ids),
            )
        )
        or 0
    )
    suggested = (
        max(0, warehouse.min_inventory - current_available - pending_inbound)
        if direction == BoxRequestDirection.inbound
        else eligible_return
    )
    return Suggestion(
        direction=direction,
        warehouse_id=warehouse_id,
        current_available=current_available,
        min_inventory=warehouse.min_inventory,
        pending_inbound=pending_inbound,
        suggested_quantity=suggested,
        eligible_return=eligible_return,
    )


def _allocated_return_ids(warehouse_id: int):
    return (
        select(BoxRequestItem.box_id)
        .join(BoxRequest, BoxRequest.id == BoxRequestItem.request_id)
        .where(
            BoxRequest.warehouse_id == warehouse_id,
            BoxRequest.direction == BoxRequestDirection.return_,
            BoxRequest.status.in_(ACTIVE_REQUEST_STATUSES),
            BoxRequestItem.box_id.is_not(None),
        )
    )


def _source_inbound(
    db: Session,
    *,
    user: User,
    source_inbound_request_id: int,
    warehouse_id: int | None = None,
    lock: bool = False,
) -> BoxRequest:
    stmt = select(BoxRequest).where(BoxRequest.id == source_inbound_request_id)
    if lock:
        stmt = stmt.with_for_update()
    source = db.scalar(stmt)
    if source is None or not can_access(user, source.warehouse_id):
        raise RequestAccessError("request not found")
    try:
        ensure_active_warehouse(db, source.warehouse_id)
    except WarehouseRuleError as exc:
        raise RequestRuleError(str(exc)) from exc
    if source.direction != BoxRequestDirection.inbound:
        raise RequestRuleError("source request must be an inbound request")
    if source.status != BoxRequestStatus.completed:
        raise RequestConflictError("source inbound request must be completed")
    if warehouse_id is not None and source.warehouse_id != warehouse_id:
        raise RequestRuleError("source inbound request belongs to a different warehouse")
    return source


def _return_candidate_stmt(source: BoxRequest):
    source_box_ids = select(BoxRequestItem.box_id).where(
        BoxRequestItem.request_id == source.id,
        BoxRequestItem.box_id.is_not(None),
    )
    return select(Box).where(
        Box.id.in_(source_box_ids),
        Box.current_warehouse_id == source.warehouse_id,
        Box.status == BoxStatus.ready_to_return,
        Box.archived_at.is_(None),
        Box.id.not_in(_allocated_return_ids(source.warehouse_id)),
    )


def get_return_candidates(
    db: Session,
    *,
    user: User,
    source_inbound_request_id: int,
    lock: bool = False,
) -> list[Box]:
    source = _source_inbound(
        db,
        user=user,
        source_inbound_request_id=source_inbound_request_id,
        lock=lock,
    )
    stmt = _return_candidate_stmt(source).order_by(
        Box.lot.asc(), Box.box_number.asc(), Box.id.asc()
    )
    if lock:
        stmt = stmt.with_for_update()
    return list(db.scalars(stmt).all())


def list_return_sources(
    db: Session,
    *,
    user: User,
    warehouse_id: int,
) -> list[ReturnSource]:
    _warehouse(db, warehouse_id, user)
    sources = db.scalars(
        select(BoxRequest)
        .where(
            BoxRequest.warehouse_id == warehouse_id,
            BoxRequest.direction == BoxRequestDirection.inbound,
            BoxRequest.status == BoxRequestStatus.completed,
        )
        .order_by(BoxRequest.completed_at.desc(), BoxRequest.id.desc())
    ).all()
    results: list[ReturnSource] = []
    for source in sources:
        delivered_quantity = int(
            db.scalar(
                select(func.count(BoxRequestItem.id)).where(
                    BoxRequestItem.request_id == source.id,
                    BoxRequestItem.box_id.is_not(None),
                )
            )
            or 0
        )
        eligible_quantity = int(
            db.scalar(
                select(func.count()).select_from(_return_candidate_stmt(source).subquery())
            )
            or 0
        )
        if source.completed_at is None:
            continue
        results.append(
            ReturnSource(
                id=source.id,
                warehouse_id=source.warehouse_id,
                completed_at=source.completed_at,
                origin=source.origin,
                delivered_quantity=delivered_quantity,
                eligible_quantity=eligible_quantity,
            )
        )
    return results


def _event(
    request: BoxRequest,
    *,
    event_type: BoxRequestEventType,
    user: User,
    from_status: BoxRequestStatus | None,
    to_status: BoxRequestStatus | None,
    note: str | None = None,
    occurred_at: datetime | None = None,
) -> BoxRequestEvent:
    return BoxRequestEvent(
        request_id=request.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        user_id=user.id,
        note=note,
        occurred_at=occurred_at or datetime.now(UTC),
    )


def create_completed_receipt(
    db: Session,
    *,
    user: User,
    warehouse_id: int,
    boxes: list[Box],
    origin: BoxRequestOrigin,
    note: str,
    commit: bool = True,
) -> BoxRequest:
    if origin not in (
        BoxRequestOrigin.xlsx_import,
        BoxRequestOrigin.manual_entry,
    ):
        raise RequestRuleError("completed receipt origin must be import or manual entry")
    if not boxes:
        raise RequestRuleError("a completed receipt requires at least one box")
    if any(
        box.current_warehouse_id != warehouse_id or box.archived_at is not None
        for box in boxes
    ):
        raise RequestRuleError("receipt boxes must be active in one warehouse")
    snapshot = calculate_suggestion(
        db,
        user=user,
        warehouse_id=warehouse_id,
        direction=BoxRequestDirection.inbound,
    )
    now = datetime.now(UTC)
    request = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=warehouse_id,
        quantity=len(boxes),
        status=BoxRequestStatus.completed,
        requester_user_id=user.id,
        origin=origin,
        suggestion_quantity=snapshot.suggested_quantity,
        current_available=snapshot.current_available,
        min_inventory=snapshot.min_inventory,
        pending_inbound=snapshot.pending_inbound,
        eligible_return=snapshot.eligible_return,
        actual_received_quantity=len(boxes),
        variance_quantity=0,
        submitted_at=now,
        completed_at=now,
        completed_by_user_id=user.id,
        created_at=now,
        updated_at=now,
        version=1,
    )
    db.add(request)
    db.flush()
    for position, box in enumerate(boxes, start=1):
        db.add(
            BoxRequestItem(
                request_id=request.id,
                position=position,
                box_id=box.id,
                lot=box.lot,
                box_number=box.box_number,
                contents=box.contents,
            )
        )
    db.add(
        _event(
            request,
            event_type=BoxRequestEventType.completed,
            user=user,
            from_status=None,
            to_status=BoxRequestStatus.completed,
            note=note,
            occurred_at=now,
        )
    )
    if commit:
        db.commit()
        db.refresh(request)
    else:
        db.flush()
    return request


def create_request(
    db: Session,
    *,
    user: User,
    warehouse_id: int,
    direction: BoxRequestDirection,
    quantity: int,
    source_inbound_request_id: int | None = None,
    box_ids: list[int] | None = None,
) -> BoxRequest:
    selected_box_ids = box_ids or []
    if direction == BoxRequestDirection.inbound:
        if source_inbound_request_id is not None or selected_box_ids:
            raise RequestRuleError(
                "inbound requests cannot specify a source request or return boxes"
            )
    else:
        if source_inbound_request_id is None:
            raise RequestRuleError(
                "source_inbound_request_id is required for return requests"
            )
        if not selected_box_ids:
            raise RequestRuleError("select at least one box for return")
        if len(selected_box_ids) != len(set(selected_box_ids)):
            raise RequestRuleError("box_ids must not contain duplicates")
        if quantity != len(selected_box_ids):
            raise RequestRuleError(
                "return quantity must match the number of selected boxes"
            )

    # Serialize request creation per warehouse so two concurrent return
    # requests cannot reserve the same physical boxes and inbound suggestions
    # include any request committed immediately ahead of this one.
    warehouse = db.scalar(
        select(Warehouse).where(Warehouse.id == warehouse_id).with_for_update()
    )
    if warehouse is None:
        raise RequestRuleError("warehouse not found")
    if not warehouse.is_active:
        raise RequestRuleError("warehouse is archived")
    if not can_access(user, warehouse_id):
        raise RequestAccessError("no access to warehouse")
    snapshot = calculate_suggestion(
        db, user=user, warehouse_id=warehouse_id, direction=direction
    )
    return_boxes: list[Box] = []
    source_eligible_quantity = snapshot.eligible_return
    if direction == BoxRequestDirection.return_:
        source = _source_inbound(
            db,
            user=user,
            source_inbound_request_id=source_inbound_request_id,
            warehouse_id=warehouse_id,
            lock=True,
        )
        candidates = db.scalars(
            _return_candidate_stmt(source)
            .order_by(Box.lot.asc(), Box.box_number.asc(), Box.id.asc())
            .with_for_update()
        ).all()
        candidates_by_id = {box.id: box for box in candidates}
        source_eligible_quantity = len(candidates)
        if any(box_id not in candidates_by_id for box_id in selected_box_ids):
            raise RequestConflictError(
                "one or more selected boxes are not ready, do not belong to the "
                "source inbound request, or are already reserved"
            )
        return_boxes = [candidates_by_id[box_id] for box_id in selected_box_ids]

    now = datetime.now(UTC)
    request = BoxRequest(
        direction=direction,
        warehouse_id=warehouse_id,
        quantity=quantity,
        status=BoxRequestStatus.submitted,
        requester_user_id=user.id,
        source_inbound_request_id=source_inbound_request_id,
        origin=BoxRequestOrigin.workflow,
        suggestion_quantity=(
            source_eligible_quantity
            if direction == BoxRequestDirection.return_
            else snapshot.suggested_quantity
        ),
        current_available=snapshot.current_available,
        min_inventory=snapshot.min_inventory,
        pending_inbound=snapshot.pending_inbound,
        eligible_return=(
            source_eligible_quantity
            if direction == BoxRequestDirection.return_
            else snapshot.eligible_return
        ),
        submitted_at=now,
        created_at=now,
        updated_at=now,
        version=1,
    )
    db.add(request)
    db.flush()

    if direction == BoxRequestDirection.return_:
        for position, box in enumerate(return_boxes, start=1):
            db.add(
                BoxRequestItem(
                    request_id=request.id,
                    position=position,
                    box_id=box.id,
                    lot=box.lot,
                    box_number=box.box_number,
                    contents=box.contents,
                )
            )

    db.add(
        _event(
            request,
            event_type=BoxRequestEventType.submitted,
            user=user,
            from_status=None,
            to_status=BoxRequestStatus.submitted,
        )
    )
    db.commit()
    db.refresh(request)
    return request


def _locked_request(db: Session, request_id: int) -> BoxRequest:
    request = db.scalar(
        select(BoxRequest).where(BoxRequest.id == request_id).with_for_update()
    )
    if request is None:
        raise RequestRuleError("request not found")
    return request


def _check_common(
    request: BoxRequest,
    *,
    user: User,
    expected_version: int | None,
) -> None:
    if not can_access(user, request.warehouse_id):
        raise RequestAccessError("request not found")
    if expected_version is not None and expected_version != request.version:
        raise RequestConflictError("request changed; refresh and retry")


def _require_mover(user: User) -> None:
    if user.role not in (UserRole.admin, UserRole.warehouse_mover):
        raise RequestAccessError("warehouse mover role required")


def _transition(
    db: Session,
    request: BoxRequest,
    *,
    user: User,
    target: BoxRequestStatus,
    event_type: BoxRequestEventType,
    note: str | None = None,
    now: datetime | None = None,
) -> BoxRequest:
    occurred_at = now or datetime.now(UTC)
    old_status = request.status
    request.status = target
    request.version += 1
    request.updated_at = occurred_at
    db.add(
        _event(
            request,
            event_type=event_type,
            user=user,
            from_status=old_status,
            to_status=target,
            note=note,
            occurred_at=occurred_at,
        )
    )
    return request


def approve_request(
    db: Session, *, request_id: int, user: User, expected_version: int | None = None
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    if request.status != BoxRequestStatus.submitted:
        raise RequestConflictError("only submitted requests can be approved")
    now = datetime.now(UTC)
    request.approved_by_user_id = user.id
    request.approved_at = now
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.approved,
        event_type=BoxRequestEventType.approved,
        now=now,
    )
    db.commit()
    db.refresh(request)
    return request


def reject_request(
    db: Session,
    *,
    request_id: int,
    user: User,
    reason: str,
    expected_version: int | None = None,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    if request.status != BoxRequestStatus.submitted:
        raise RequestConflictError("only submitted requests can be rejected")
    request.rejection_reason = reason.strip()
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.rejected,
        event_type=BoxRequestEventType.rejected,
        note=request.rejection_reason,
    )
    db.commit()
    db.refresh(request)
    return request


def start_transit(
    db: Session, *, request_id: int, user: User, expected_version: int | None = None
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    if request.status != BoxRequestStatus.approved:
        raise RequestConflictError("only approved requests can start transit")
    required_type = (
        BoxRequestDocumentType.delivery_note
        if request.direction == BoxRequestDirection.inbound
        else BoxRequestDocumentType.return_note
    )
    has_note = db.scalar(
        select(BoxRequestDocument.id).where(
            BoxRequestDocument.request_id == request.id,
            BoxRequestDocument.document_type == required_type,
            BoxRequestDocument.is_current.is_(True),
        )
    )
    if has_note is None:
        raise RequestRuleError(f"a current {required_type.value} is required")
    now = datetime.now(UTC)
    request.in_transit_by_user_id = user.id
    request.in_transit_at = now
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.in_transit,
        event_type=BoxRequestEventType.in_transit,
        now=now,
    )
    db.commit()
    db.refresh(request)
    return request


def cancel_request(
    db: Session,
    *,
    request_id: int,
    user: User,
    reason: str | None = None,
    expected_version: int | None = None,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    if user.role != UserRole.admin and request.requester_user_id != user.id:
        raise RequestAccessError("only the requester can cancel this request")
    if request.status not in (BoxRequestStatus.submitted, BoxRequestStatus.approved):
        raise RequestConflictError("request can no longer be cancelled")
    request.cancellation_reason = reason.strip() if reason else None
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.cancelled,
        event_type=BoxRequestEventType.cancelled,
        note=request.cancellation_reason,
    )
    db.commit()
    db.refresh(request)
    return request


def merge_inbound_items(items: list[InboundBoxItem]) -> list[InboundBoxItem]:
    """Collapse spreadsheet rows that describe the same physical box.

    A box is unique within a lot, so rows are grouped by canonical
    ``(lot, box_number)``. Distinct contents are retained in source order and
    joined into the box's single contents field.
    """
    grouped: dict[tuple[str, str], list[str]] = {}
    for item in items:
        lot = item.lot.strip()
        if not lot:
            raise RequestRuleError("lot is required")
        try:
            box_number = normalize_box_number(item.box_number)
        except BoxRuleError as exc:
            raise RequestRuleError(str(exc)) from exc
        key = (lot, box_number)
        contents = grouped.setdefault(key, [])
        value = (item.contents or "").strip()
        if value and value not in contents:
            contents.append(value)

    merged: list[InboundBoxItem] = []
    for (lot, box_number), contents in grouped.items():
        combined = " | ".join(contents)
        if len(combined) > 200:
            raise RequestRuleError(
                f"combined contents for box {box_number!r} in lot {lot!r} "
                "exceed 200 characters"
            )
        merged.append(
            InboundBoxItem(
                lot=lot,
                box_number=box_number,
                contents=combined or None,
            )
        )
    return merged


def complete_request(
    db: Session,
    *,
    request_id: int,
    user: User,
    inbound_items: list[InboundBoxItem] | None = None,
    discrepancy_reason: str | None = None,
    expected_version: int | None = None,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    if request.status == BoxRequestStatus.completed and expected_version is None:
        if request.direction == BoxRequestDirection.inbound:
            if user.role != UserRole.admin and request.requester_user_id != user.id:
                raise RequestAccessError("only the original requester can accept delivery")
        else:
            _require_mover(user)
        return request
    if request.status != BoxRequestStatus.in_transit:
        raise RequestConflictError("only in-transit requests can be completed")

    if request.direction == BoxRequestDirection.inbound:
        if user.role != UserRole.admin and request.requester_user_id != user.id:
            raise RequestAccessError("only the original requester can accept delivery")
        rows = merge_inbound_items(inbound_items or [])
        if not rows:
            raise RequestRuleError(
                "at least one unique inbound box is required to accept a delivery"
            )
        actual_quantity = len(rows)
        variance = actual_quantity - request.quantity
        cleaned_reason = (discrepancy_reason or "").strip()
        if variance != 0 and not cleaned_reason:
            raise RequestRuleError(
                "discrepancy_reason is required when the received count "
                "does not match the ordered quantity"
            )
        request.actual_received_quantity = actual_quantity
        request.variance_quantity = variance
        request.discrepancy_reason = cleaned_reason or None
        if request.items:
            raise RequestConflictError("inbound items have already been recorded")
        try:
            for position, item in enumerate(rows, start=1):
                box = create_box(
                    db,
                    user=user,
                    box_number=item.box_number,
                    lot=item.lot,
                    contents=item.contents,
                    warehouse_id=request.warehouse_id,
                    note=f"Received through request #{request.id}",
                    commit=False,
                )
                db.add(
                    BoxRequestItem(
                        request_id=request.id,
                        position=position,
                        box_id=box.id,
                        lot=box.lot,
                        box_number=box.box_number,
                        contents=box.contents,
                    )
                )
        except BoxRuleError as exc:
            db.rollback()
            raise RequestConflictError(str(exc)) from exc
    else:
        _require_mover(user)
        if len(request.items) != request.quantity:
            raise RequestConflictError("return allocation is incomplete")
        return_boxes: list[Box] = []
        for item in request.items:
            box = db.get(Box, item.box_id)
            if (
                box is None
                or box.archived_at is not None
                or box.status != BoxStatus.ready_to_return
                or box.current_warehouse_id != request.warehouse_id
            ):
                raise RequestConflictError(
                    f"box {item.box_id} is no longer ready to return "
                    "from the request warehouse"
                )
            return_boxes.append(box)
        try:
            for box in return_boxes:
                update_box(
                    db,
                    user=user,
                    box=box,
                    new_status=BoxStatus.returned,
                    note=f"Returned through request #{request.id}",
                    commit=False,
                    skip_request_guards=True,
                )
        except BoxRuleError as exc:
            db.rollback()
            raise RequestConflictError(str(exc)) from exc

    now = datetime.now(UTC)
    request.completed_by_user_id = user.id
    request.completed_at = now
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.completed,
        event_type=BoxRequestEventType.completed,
        note=(
            (
                f"Ordered {request.quantity}; received "
                f"{request.actual_received_quantity}; variance "
                f"{request.variance_quantity:+d}. "
                f"Reason: {request.discrepancy_reason}"
            )
            if request.direction == BoxRequestDirection.inbound
            and request.variance_quantity
            else (
                f"Ordered {request.quantity}; received "
                f"{request.actual_received_quantity}; variance 0."
                if request.direction == BoxRequestDirection.inbound
                else None
            )
        ),
        now=now,
    )
    db.commit()
    db.refresh(request)
    return request
