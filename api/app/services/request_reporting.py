from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased, noload

from app.models.boxes import BoxEvent
from app.models.requests import (
    BoxRequest,
    BoxRequestDiscrepancy,
    BoxRequestDiscrepancyType,
    BoxRequestDocument,
    BoxRequestDocumentType,
    BoxRequestEvent,
    BoxRequestException,
    BoxRequestExceptionKind,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxRequestStatus,
)
from app.models.users import User
from app.models.warehouses import Warehouse
from app.schemas.requests import (
    RequestAnalyticsOut,
    RequestDurationMetric,
    RequestRateMetric,
    RequestReasonCount,
    RequestReconciliationIssue,
    RequestReconciliationOut,
    RequestReconciliationSummary,
    RequestThroughput,
)
from app.services.acl import apply_warehouse_filter

REQUEST_RECONCILIATION_ISSUE_TYPES = (
    "shortage",
    "excess",
    "typed_discrepancy",
    "open_follow_up",
    "missing_erp_document",
    "superseded_erp_document",
    "admin_override",
    "cancelled_reservation",
    "awaiting_confirmation",
    "sla_breach",
    "review_pending_self_receipt",
    "operational_exception",
)


@dataclass(frozen=True)
class RequestReportFilters:
    warehouse_id: int | None = None
    assigned_mover_user_id: int | None = None
    from_at: datetime | None = None
    to_at: datetime | None = None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _request_rows(
    db: Session,
    *,
    user: User,
    filters: RequestReportFilters,
    apply_date: bool = True,
) -> list[tuple[BoxRequest, str, str | None, str | None]]:
    assignee = aliased(User)
    target_warehouse = aliased(Warehouse)
    stmt = (
        select(
            BoxRequest,
            Warehouse.name,
            target_warehouse.name,
            assignee.display_name,
            assignee.email,
        )
        .options(
            noload(BoxRequest.items),
            noload(BoxRequest.documents),
            noload(BoxRequest.discrepancies),
            noload(BoxRequest.comments),
            noload(BoxRequest.attachments),
            noload(BoxRequest.operational_exceptions),
        )
        .join(Warehouse, Warehouse.id == BoxRequest.warehouse_id)
        .join(
            target_warehouse,
            target_warehouse.id == BoxRequest.target_warehouse_id,
            isouter=True,
        )
        .join(assignee, assignee.id == BoxRequest.assigned_mover_user_id, isouter=True)
    )
    if filters.warehouse_id is not None:
        stmt = stmt.where(BoxRequest.warehouse_id == filters.warehouse_id)
    if filters.assigned_mover_user_id is not None:
        stmt = stmt.where(
            BoxRequest.assigned_mover_user_id == filters.assigned_mover_user_id
        )
    if apply_date and filters.from_at is not None:
        stmt = stmt.where(BoxRequest.submitted_at >= filters.from_at)
    if apply_date and filters.to_at is not None:
        stmt = stmt.where(BoxRequest.submitted_at < filters.to_at)
    stmt = apply_warehouse_filter(stmt, user, BoxRequest.warehouse_id)
    return [
        (request, warehouse_name, target_name, display_name or email)
        for request, warehouse_name, target_name, display_name, email in db.execute(
            stmt
        ).all()
    ]


def _rate(numerator: int, denominator: int) -> RequestRateMetric:
    return RequestRateMetric(
        numerator=numerator,
        denominator=denominator,
        rate=round(numerator / denominator, 4) if denominator else None,
    )


def _duration(values: list[float], *, supported: bool = True) -> RequestDurationMetric:
    return RequestDurationMetric(
        supported=supported,
        average_seconds=round(fmean(values), 2) if values else None,
        sample_size=len(values),
    )


def _metadata_bool(metadata: dict[str, object] | None, key: str) -> bool:
    return bool((metadata or {}).get(key))


def build_reconciliation(
    db: Session,
    *,
    user: User,
    filters: RequestReportFilters,
    severity: str | None = None,
    issue_type: str | None = None,
    page: int = 1,
    page_size: int = 100,
    now: datetime | None = None,
) -> RequestReconciliationOut:
    generated_at = _aware(now or datetime.now(UTC))
    rows = _request_rows(db, user=user, filters=filters, apply_date=False)
    requests = {request.id: request for request, _, _, _ in rows}
    warehouse_names = {
        request.id: warehouse for request, warehouse, _, _ in rows
    }
    target_warehouse_names = {
        request.id: target for request, _, target, _ in rows
    }
    assignee_names = {request.id: assignee for request, _, _, assignee in rows}
    request_ids = list(requests)
    if not request_ids:
        return RequestReconciliationOut(
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            generated_at=generated_at,
            summary=RequestReconciliationSummary(
                total=0,
                critical=0,
                high=0,
                medium=0,
                low=0,
                by_type={kind: 0 for kind in REQUEST_RECONCILIATION_ISSUE_TYPES},
            ),
        )

    discrepancies = db.scalars(
        select(BoxRequestDiscrepancy)
        .options(noload(BoxRequestDiscrepancy.photos))
        .where(BoxRequestDiscrepancy.request_id.in_(request_ids))
    ).all()
    documents = db.scalars(
        select(BoxRequestDocument).where(BoxRequestDocument.request_id.in_(request_ids))
    ).all()
    request_events = db.scalars(
        select(BoxRequestEvent).where(BoxRequestEvent.request_id.in_(request_ids))
    ).all()
    operational_exceptions = db.scalars(
        select(BoxRequestException).where(
            BoxRequestException.request_id.in_(request_ids)
        )
    ).all()
    request_item_event_rows = db.execute(
        select(BoxRequestItem, BoxEvent)
        .outerjoin(
            BoxEvent,
            BoxEvent.box_id == BoxRequestItem.box_id,
        )
        .where(
            BoxRequestItem.request_id.in_(request_ids),
            BoxRequestItem.box_id.is_not(None),
        )
    ).all()
    item_snapshots: dict[tuple[int, int | None], BoxRequestItem] = {}
    item_snapshots_by_id: dict[int, BoxRequestItem] = {}

    discrepancies_by_request: dict[int, list[BoxRequestDiscrepancy]] = defaultdict(list)
    for discrepancy in discrepancies:
        discrepancies_by_request[discrepancy.request_id].append(discrepancy)
    documents_by_request: dict[int, list[BoxRequestDocument]] = defaultdict(list)
    for document in documents:
        documents_by_request[document.request_id].append(document)
    events_by_request: dict[int, list[BoxRequestEvent]] = defaultdict(list)
    for event in request_events:
        events_by_request[event.request_id].append(event)
    exceptions_by_request: dict[int, list[BoxRequestException]] = defaultdict(list)
    for exception in operational_exceptions:
        exceptions_by_request[exception.request_id].append(exception)
    box_events_by_request: dict[int, list[BoxEvent]] = defaultdict(list)
    for item, event in request_item_event_rows:
        item_snapshots[(item.request_id, item.box_id)] = item
        item_snapshots_by_id[item.id] = item
        if event is not None:
            box_events_by_request[item.request_id].append(event)

    issues: list[RequestReconciliationIssue] = []

    def add(
        request: BoxRequest,
        *,
        key: str,
        kind: str,
        level: str,
        title: str,
        detail: str,
        occurred_at: datetime,
        due_at: datetime | None = None,
        box_id: int | None = None,
        request_item_id: int | None = None,
    ) -> None:
        issue_time = _aware(occurred_at)
        if filters.from_at is not None and issue_time < _aware(filters.from_at):
            return
        if filters.to_at is not None and issue_time >= _aware(filters.to_at):
            return
        snapshot = (
            item_snapshots_by_id.get(request_item_id)
            if request_item_id is not None
            else item_snapshots.get((request.id, box_id))
        )
        issues.append(
            RequestReconciliationIssue(
                issue_key=f"{request.id}:{key}",
                issue_type=kind,
                severity=level,
                request_id=request.id,
                box_id=box_id,
                pallet_id=snapshot.pallet_id if snapshot is not None else None,
                pallet_number=snapshot.pallet if snapshot is not None else None,
                warehouse_id=request.warehouse_id,
                warehouse_name=warehouse_names[request.id],
                target_warehouse_id=request.target_warehouse_id,
                target_warehouse_name=target_warehouse_names[request.id],
                assigned_mover_user_id=request.assigned_mover_user_id,
                assigned_mover_name=assignee_names[request.id],
                request_status=request.status,
                title=title,
                detail=detail,
                occurred_at=issue_time,
                due_at=_aware(due_at) if due_at is not None else None,
                request_path=f"/requests/{request.id}",
                box_path=f"/boxes/{box_id}" if box_id is not None else None,
            )
        )

    for request in requests.values():
        request_discrepancies = discrepancies_by_request[request.id]
        discrepancy_counts: Counter[BoxRequestDiscrepancyType] = Counter()
        for discrepancy in request_discrepancies:
            discrepancy_counts[discrepancy.discrepancy_type] += discrepancy.quantity or 1

        shortage = max(
            -int(request.variance_quantity or 0),
            discrepancy_counts[BoxRequestDiscrepancyType.missing],
        )
        excess = max(
            int(request.variance_quantity or 0),
            discrepancy_counts[BoxRequestDiscrepancyType.unexpected],
        )
        completion_time = request.completed_at or request.updated_at
        if shortage:
            add(
                request,
                key="shortage",
                kind="shortage",
                level="high",
                title=f"Shortage of {shortage} box(es)",
                detail="Received or collected quantity is below the requested quantity.",
                occurred_at=completion_time,
            )
        if excess:
            add(
                request,
                key="excess",
                kind="excess",
                level="medium",
                title=f"Excess of {excess} box(es)",
                detail="Received quantity is above the requested quantity.",
                occurred_at=completion_time,
            )
        for discrepancy_type, count in discrepancy_counts.items():
            if discrepancy_type in (
                BoxRequestDiscrepancyType.missing,
                BoxRequestDiscrepancyType.unexpected,
            ):
                continue
            matching = [
                item
                for item in request_discrepancies
                if item.discrepancy_type == discrepancy_type
            ]
            representative = next(
                (
                    item
                    for item in matching
                    if item.request_item_id is not None or item.box_id is not None
                ),
                None,
            )
            add(
                request,
                key=f"discrepancy:{discrepancy_type.value}",
                kind="typed_discrepancy",
                level=(
                    "high"
                    if discrepancy_type
                    in (
                        BoxRequestDiscrepancyType.damaged,
                        BoxRequestDiscrepancyType.rejected,
                    )
                    else "medium"
                ),
                title=f"{discrepancy_type.value.replace('_', ' ').title()} discrepancy",
                detail=f"{count} affected box(es).",
                occurred_at=max(item.created_at for item in matching),
                box_id=representative.box_id if representative is not None else None,
                request_item_id=(
                    representative.request_item_id
                    if representative is not None
                    else None
                ),
            )

        if request.origin in (
            BoxRequestOrigin.backorder,
            BoxRequestOrigin.return_reselection,
        ) and request.status not in (
            BoxRequestStatus.completed,
            BoxRequestStatus.rejected,
            BoxRequestStatus.cancelled,
        ):
            add(
                request,
                key="open-follow-up",
                kind="open_follow_up",
                level="high" if request.origin == BoxRequestOrigin.backorder else "medium",
                title=(
                    "Open backorder"
                    if request.origin == BoxRequestOrigin.backorder
                    else "Open return follow-up"
                ),
                detail="This generated follow-up has not been resolved.",
                occurred_at=request.created_at,
                due_at=request.sla_deadline,
            )

        request_documents = documents_by_request[request.id]
        is_self_receipt = request.origin in (
            BoxRequestOrigin.xlsx_import,
            BoxRequestOrigin.manual_entry,
        )
        required_type = (
            BoxRequestDocumentType.delivery_note
            if request.direction.value == "inbound"
            else BoxRequestDocumentType.return_note
        )
        requires_document = (
            request.receipt_document_required
            if is_self_receipt
            else request.status
            in (
                BoxRequestStatus.ready_for_transport,
                BoxRequestStatus.in_transit,
                BoxRequestStatus.awaiting_confirmation,
                BoxRequestStatus.completed,
            )
        )
        if requires_document and not any(
            document.document_type == required_type and document.is_current
            for document in request_documents
        ):
            add(
                request,
                key=f"missing-document:{required_type.value}",
                kind="missing_erp_document",
                level="high",
                title=f"Missing current {required_type.value.replace('_', ' ')}",
                detail="No current ERP document is linked to this request.",
                occurred_at=request.approved_at or request.completed_at or request.updated_at,
            )
        if is_self_receipt and request.status == BoxRequestStatus.submitted:
            add(
                request,
                key="review-pending",
                kind="review_pending_self_receipt",
                level="high",
                title="Self receipt awaiting administrator review",
                detail=(
                    f"{request.quantity} mapped box(es) are staged and have not "
                    "changed physical inventory."
                ),
                occurred_at=request.submitted_at,
            )
        superseded = [document for document in request_documents if not document.is_current]
        if superseded:
            add(
                request,
                key="superseded-documents",
                kind="superseded_erp_document",
                level="low",
                title="Superseded ERP document retained",
                detail=f"{len(superseded)} superseded document version(s) remain in history.",
                occurred_at=max(document.created_at for document in superseded),
            )

        if request.status == BoxRequestStatus.awaiting_confirmation:
            add(
                request,
                key="awaiting-acceptance",
                kind="awaiting_confirmation",
                level="medium",
                title="Awaiting acceptance",
                detail="Transport has started and destination acceptance is pending.",
                occurred_at=request.awaiting_confirmation_at or request.updated_at,
                due_at=request.sla_deadline,
            )
        for exception in exceptions_by_request[request.id]:
            if exception.resolved_at is not None:
                continue
            add(
                request,
                key=f"operational-exception:{exception.id}",
                kind="operational_exception",
                level=(
                    "critical"
                    if exception.exception_kind
                    == BoxRequestExceptionKind.failed_delivery
                    else "high"
                ),
                title=(
                    "Failed transport requires retry"
                    if exception.exception_kind
                    == BoxRequestExceptionKind.failed_delivery
                    else "Request is on operational hold"
                ),
                detail=exception.reason,
                occurred_at=exception.created_at,
                due_at=request.sla_deadline,
            )
        if (
            request.sla_deadline is not None
            and _aware(request.sla_deadline) < generated_at
            and request.status
            not in (
                BoxRequestStatus.completed,
                BoxRequestStatus.rejected,
                BoxRequestStatus.cancelled,
            )
        ):
            add(
                request,
                key="sla-breach",
                kind="sla_breach",
                level="critical",
                title="SLA deadline breached",
                detail="The request remains open after its SLA deadline.",
                occurred_at=request.sla_deadline,
                due_at=request.sla_deadline,
            )

        for event in events_by_request[request.id]:
            metadata = event.event_metadata or {}
            if _metadata_bool(metadata, "cancelled_reservation"):
                add(
                    request,
                    key=f"cancelled-reservation:{event.id}",
                    kind="cancelled_reservation",
                    level="high",
                    title="Reservation cancelled by override",
                    detail=event.note or "An active box reservation was cancelled.",
                    occurred_at=event.occurred_at,
                    box_id=(
                        int(metadata["box_id"])
                        if isinstance(metadata.get("box_id"), int)
                        else None
                    ),
                )
            if _metadata_bool(metadata, "admin_override"):
                add(
                    request,
                    key=f"request-override:{event.id}",
                    kind="admin_override",
                    level="high",
                    title="Administrative override",
                    detail=event.note or "Request workflow was overridden.",
                    occurred_at=event.occurred_at,
                )
        seen_box_events: set[int] = set()
        for event in box_events_by_request[request.id]:
            if event.id in seen_box_events or not _metadata_bool(
                event.event_metadata, "admin_override"
            ):
                continue
            seen_box_events.add(event.id)
            add(
                request,
                key=f"box-override:{event.id}",
                kind="admin_override",
                level="high",
                title="Linked box administrative override",
                detail=event.note or "A linked box was changed by administrative override.",
                occurred_at=event.occurred_at,
                box_id=event.box_id,
            )

    if severity is not None:
        issues = [item for item in issues if item.severity == severity]
    if issue_type is not None:
        issues = [item for item in issues if item.issue_type == issue_type]
    issues.sort(
        key=lambda item: (
            {"critical": 4, "high": 3, "medium": 2, "low": 1}[item.severity],
            item.occurred_at,
            item.issue_key,
        ),
        reverse=True,
    )
    severity_counts = Counter(item.severity for item in issues)
    type_counts = Counter(item.issue_type for item in issues)
    total = len(issues)
    start = (page - 1) * page_size
    return RequestReconciliationOut(
        items=issues[start : start + page_size],
        total=total,
        page=page,
        page_size=page_size,
        generated_at=generated_at,
        summary=RequestReconciliationSummary(
            total=total,
            critical=severity_counts["critical"],
            high=severity_counts["high"],
            medium=severity_counts["medium"],
            low=severity_counts["low"],
            by_type={
                kind: type_counts[kind]
                for kind in REQUEST_RECONCILIATION_ISSUE_TYPES
            },
        ),
    )


def build_request_analytics(
    db: Session,
    *,
    user: User,
    filters: RequestReportFilters,
    now: datetime | None = None,
) -> RequestAnalyticsOut:
    generated_at = _aware(now or datetime.now(UTC))
    rows = _request_rows(db, user=user, filters=filters)
    requests = [request for request, _, _, _ in rows]
    warehouse_names = {
        request.warehouse_id: warehouse_name
        for request, warehouse_name, _, _ in rows
    }
    assignee_names = {
        request.assigned_mover_user_id: assignee_name
        for request, _, _, assignee_name in rows
        if request.assigned_mover_user_id is not None
    }
    completed = [
        request for request in requests if request.status == BoxRequestStatus.completed
    ]
    completed_ids = [request.id for request in completed]
    discrepancy_request_ids = (
        set(
            db.scalars(
                select(BoxRequestDiscrepancy.request_id)
                .where(BoxRequestDiscrepancy.request_id.in_(completed_ids))
                .distinct()
            ).all()
        )
        if completed_ids
        else set()
    )
    discrepancy_request_ids.update(
        request.id for request in completed if int(request.variance_quantity or 0) != 0
    )

    approval_values = [
        (_aware(request.approved_at) - _aware(request.submitted_at)).total_seconds()
        for request in requests
        if request.approved_at is not None
    ]
    preparation_values = [
        (
            _aware(request.ready_for_transport_at) - _aware(request.approved_at)
        ).total_seconds()
        for request in requests
        if request.ready_for_transport_at is not None
        and request.approved_at is not None
    ]
    transport_values = [
        (
            _aware(request.awaiting_confirmation_at) - _aware(request.in_transit_at)
        ).total_seconds()
        for request in requests
        if request.awaiting_confirmation_at is not None
        and request.in_transit_at is not None
    ]
    acceptance_values = [
        (
            _aware(request.completed_at)
            - _aware(request.awaiting_confirmation_at)
        ).total_seconds()
        for request in completed
        if request.completed_at is not None
        and request.awaiting_confirmation_at is not None
    ]
    on_time_denominator = [
        request
        for request in completed
        if request.completed_at is not None and request.sla_deadline is not None
    ]
    on_time_count = sum(
        _aware(request.completed_at) <= _aware(request.sla_deadline)
        for request in on_time_denominator
        if request.completed_at is not None and request.sla_deadline is not None
    )
    shortage_ids = {
        request.id for request in completed if int(request.variance_quantity or 0) < 0
    }
    overage_ids = {
        request.id for request in completed if int(request.variance_quantity or 0) > 0
    }
    if completed_ids:
        for request_id, discrepancy_type in db.execute(
            select(
                BoxRequestDiscrepancy.request_id,
                BoxRequestDiscrepancy.discrepancy_type,
            ).where(BoxRequestDiscrepancy.request_id.in_(completed_ids))
        ).all():
            if discrepancy_type == BoxRequestDiscrepancyType.missing:
                shortage_ids.add(request_id)
            elif discrepancy_type == BoxRequestDiscrepancyType.unexpected:
                overage_ids.add(request_id)

    rejection_counts = Counter(
        (request.rejection_reason or "").strip() or "No reason provided"
        for request in requests
        if request.status == BoxRequestStatus.rejected
    )
    cancellation_counts = Counter(
        (request.cancellation_reason or "").strip() or "No reason provided"
        for request in requests
        if request.status == BoxRequestStatus.cancelled
    )
    warehouse_throughput: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    mover_throughput: dict[int | None, list[int]] = defaultdict(lambda: [0, 0])
    for request in completed:
        warehouse_throughput[request.warehouse_id][0] += 1
        warehouse_throughput[request.warehouse_id][1] += int(
            request.actual_received_quantity
            if request.actual_received_quantity is not None
            else request.quantity
        )
        mover_id = request.assigned_mover_user_id or request.completed_by_user_id
        mover_throughput[mover_id][0] += 1
        mover_throughput[mover_id][1] += int(
            request.actual_received_quantity
            if request.actual_received_quantity is not None
            else request.quantity
        )

    denominator = len(completed)
    return RequestAnalyticsOut(
        generated_at=generated_at,
        from_at=filters.from_at,
        to_at=filters.to_at,
        total_requests=len(requests),
        completed_requests=denominator,
        approval_duration=_duration(approval_values),
        preparation_duration=_duration(preparation_values),
        transport_duration=_duration(transport_values),
        acceptance_duration=_duration(acceptance_values),
        on_time=_rate(on_time_count, len(on_time_denominator)),
        discrepancy=_rate(len(discrepancy_request_ids), denominator),
        shortage=_rate(len(shortage_ids), denominator),
        overage=_rate(len(overage_ids), denominator),
        rejection_reasons=[
            RequestReasonCount(reason=reason, count=count)
            for reason, count in rejection_counts.most_common()
        ],
        cancellation_reasons=[
            RequestReasonCount(reason=reason, count=count)
            for reason, count in cancellation_counts.most_common()
        ],
        throughput_by_warehouse=[
            RequestThroughput(
                id=warehouse_id,
                name=warehouse_names.get(warehouse_id, f"Warehouse {warehouse_id}"),
                completed_requests=values[0],
                completed_quantity=values[1],
            )
            for warehouse_id, values in sorted(warehouse_throughput.items())
        ],
        throughput_by_mover=[
            RequestThroughput(
                id=mover_id,
                name=(
                    assignee_names.get(mover_id)
                    or ("Unassigned / accepting user" if mover_id is None else f"User {mover_id}")
                ),
                completed_requests=values[0],
                completed_quantity=values[1],
            )
            for mover_id, values in sorted(
                mover_throughput.items(), key=lambda item: (item[0] is None, item[0] or 0)
            )
        ],
    )
