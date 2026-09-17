from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.box_files import BoxFile, normalize_box_file_reference
from app.models.boxes import (
    ACTIVE_STATUSES,
    AVAILABLE_STATUSES,
    UNAVAILABLE_STATUSES,
    Box,
    BoxEvent,
    BoxEventType,
    BoxStatus,
)
from app.models.lots import Lot, normalize_lot_name
from app.models.notifications import RequestNotificationKind
from app.models.pallets import (
    Pallet,
    clean_optional_pallet_number,
    normalize_pallet_number,
)
from app.models.requests import (
    ACTIVE_REQUEST_STATUSES,
    BoxRequest,
    BoxRequestComment,
    BoxRequestDirection,
    BoxRequestDiscrepancy,
    BoxRequestDiscrepancyType,
    BoxRequestDocument,
    BoxRequestDocumentType,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestException,
    BoxRequestExceptionKind,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxRequestPriority,
    BoxRequestStatus,
)
from app.models.users import User, UserRole
from app.models.warehouses import Warehouse
from app.schemas.box_files import FileInput
from app.schemas.requests import (
    InboundBoxItem,
    InboundCompletionFileImpact,
    InboundCompletionPreviewOut,
    InboundCompletionPreviewRow,
    InboundCompletionPreviewSummary,
    InboundCompletionSourceWarehouseCount,
    InboundCompletionTargetPalletOut,
    RequestDiscrepancyInput,
)
from app.services.acl import can_access
from app.services.box_files import (
    BoxFileConflictError,
    IntakeFile,
    reconcile_intake_box_files,
    snapshot_request_item_files,
    split_legacy_contents,
)
from app.services.boxes import (
    BoxRuleError,
    _move_received_box_for_inbound_request,
    create_box,
    move_return_box_for_request,
    normalize_box_number,
    restore_archived_box,
)
from app.services.lots import (
    LotRuleError,
    lock_active_lots_for_use,
    resolve_lot_names_for_use,
    validate_lot_name,
)
from app.services.pallets import PalletRuleError, resolve_or_create_active_pallet
from app.services.request_notifications import enqueue_request_event
from app.services.warehouses import WarehouseRuleError, ensure_active_warehouse


class RequestRuleError(Exception):
    pass


class RequestAccessError(RequestRuleError):
    pass


class RequestConflictError(RequestRuleError):
    pass


def _pallet_number_for_box(db: Session, box: Box) -> str | None:
    if box.pallet_id is None:
        return None
    pallet = db.get(Pallet, box.pallet_id)
    return pallet.pallet_number if pallet is not None else None


def _active_files_by_box_ids(
    db: Session, box_ids: list[int], *, lock: bool = False
) -> dict[int, list[BoxFile]]:
    result = {box_id: [] for box_id in box_ids}
    if not box_ids:
        return result
    stmt = (
        select(BoxFile)
        .where(
            BoxFile.box_id.in_(sorted(set(box_ids))),
            BoxFile.archived_at.is_(None),
        )
        .order_by(BoxFile.box_id, BoxFile.position, BoxFile.id)
    )
    if lock:
        stmt = stmt.with_for_update(of=BoxFile)
    for file in db.scalars(stmt).all():
        result[file.box_id].append(file)
    return result


def _staged_file_values(item: InboundBoxItem) -> list[IntakeFile]:
    if item.files is not None:
        return [
            IntakeFile(
                reference=file.reference,
                description=file.description,
                barcode=file.barcode,
            )
            for file in item.files
        ]
    return [
        IntakeFile(
            reference=(
                f"LEGACY-BOX-{item.box_number}-FILE-{position}"
            ),
            description=description,
            legacy=True,
        )
        for position, description in enumerate(
            split_legacy_contents(item.contents), start=1
        )
    ]


def _lock_lots_for_current_links(db: Session, lot_ids: list[int | None]) -> None:
    """Hold shared Lot locks before creating current request-item references."""
    if any(lot_id is None for lot_id in lot_ids):
        raise RequestConflictError("a current request item has no lot identity")
    try:
        lock_active_lots_for_use(
            db,
            [lot_id for lot_id in lot_ids if lot_id is not None],
        )
    except LotRuleError as exc:
        raise RequestConflictError(str(exc)) from exc


def _lock_lots_for_box_ids(db: Session, box_ids: list[int]) -> dict[int, int]:
    rows = db.execute(
        select(Box.id, Box.lot_id)
        .where(Box.id.in_(sorted(set(box_ids))))
        .order_by(Box.id)
    ).all()
    lot_by_box_id = {int(box_id): int(lot_id) for box_id, lot_id in rows}
    if len(lot_by_box_id) != len(set(box_ids)):
        raise RequestConflictError("one or more boxes no longer exist")
    _lock_lots_for_current_links(db, list(lot_by_box_id.values()))
    return lot_by_box_id


@dataclass(frozen=True)
class Suggestion:
    direction: BoxRequestDirection
    warehouse_id: int
    analysis_as_of: datetime
    current_available: int
    min_inventory: int
    max_capacity: int
    current_occupied: int
    baseline_gap: int
    minimum_gap: int
    pending_inbound: int
    pending_backorder: int
    scheduled_inbound: int
    scheduled_return: int
    history_window_30_start: datetime
    history_window_90_start: datetime
    history_window_end: datetime
    history_30_quantity: int
    history_90_quantity: int
    history_30_daily_rate: float
    history_90_daily_rate: float
    history_30_weight: int
    history_90_weight: int
    normalized_30_weight: float
    normalized_90_weight: float
    sample_size: int
    history_days: int
    confidence: str
    weighted_daily_rate: float
    daily_demand_forecast: float
    lead_time_days: int
    lead_time_demand: int
    safety_stock_percent: int
    safety_stock_quantity: int
    forecast_adjustment: int | None
    adjustment_quantity: int
    target_inventory: int
    capacity_limit: int
    capacity_available: int
    capacity_cap_applied: bool
    fallback_used: bool
    fallback_reason: str | None
    suggested_quantity: int
    eligible_return: int
    consumption_definition: str
    formula: str
    explanation: str

    def to_snapshot(self) -> dict[str, object]:
        return {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in self.__dict__.items()
        }


@dataclass(frozen=True)
class ReturnSource:
    id: int
    warehouse_id: int
    completed_at: datetime
    origin: BoxRequestOrigin
    delivered_quantity: int
    eligible_quantity: int
    pallets: list[dict[str, object]]
    has_unassigned_boxes: bool


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
    as_of: datetime | None = None,
) -> Suggestion:
    warehouse = _warehouse(db, warehouse_id, user)
    now = _as_utc(as_of or datetime.now(UTC))
    window_30_start, window_90_start, window_end = _history_boundaries(now)
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
    current_occupied = int(
        db.scalar(
            select(func.count(Box.id)).where(
                Box.current_warehouse_id == warehouse_id,
                Box.status.in_(ACTIVE_STATUSES),
                Box.archived_at.is_(None),
            )
        )
        or 0
    )
    active_requests = list(
        db.scalars(
            select(BoxRequest).where(
                BoxRequest.warehouse_id == warehouse_id,
                BoxRequest.status.in_(ACTIVE_REQUEST_STATUSES),
            )
        ).all()
    )
    pending_inbound = sum(
        _outstanding_quantity(request)
        for request in active_requests
        if request.direction == BoxRequestDirection.inbound
    )
    pending_backorder = sum(
        _outstanding_quantity(request)
        for request in active_requests
        if request.direction == BoxRequestDirection.inbound
        and request.origin == BoxRequestOrigin.backorder
    )
    lead_end = now + timedelta(days=warehouse.lead_time_days)
    scheduled_inbound = sum(
        _outstanding_quantity(request)
        for request in active_requests
        if request.direction == BoxRequestDirection.inbound
        and _is_scheduled_by(request, lead_end)
    )
    scheduled_return = sum(
        _outstanding_quantity(request)
        for request in active_requests
        if request.direction == BoxRequestDirection.return_
        and _is_scheduled_by(request, lead_end)
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
    consumption_events = _deduplicate_consumption_events(
        db.scalars(
            select(BoxEvent).where(
                BoxEvent.warehouse_id == warehouse_id,
                BoxEvent.event_type == BoxEventType.status_changed,
                BoxEvent.from_status.in_(AVAILABLE_STATUSES),
                BoxEvent.to_status.in_(UNAVAILABLE_STATUSES),
                BoxEvent.occurred_at >= window_90_start,
                BoxEvent.occurred_at < window_end,
                BoxEvent.occurred_at <= now,
            )
        ).all()
    )
    history_90_quantity = len(consumption_events)
    history_30_quantity = sum(
        1
        for event in consumption_events
        if _as_utc(event.occurred_at) >= window_30_start
    )
    history_30_rate = Decimal(history_30_quantity) / Decimal(30)
    history_90_rate = Decimal(history_90_quantity) / Decimal(90)
    weight_total = warehouse.history_30_weight + warehouse.history_90_weight
    normalized_30 = Decimal(warehouse.history_30_weight) / Decimal(weight_total)
    normalized_90 = Decimal(warehouse.history_90_weight) / Decimal(weight_total)
    minimum_gap = max(0, warehouse.min_inventory - current_available)
    history_days = _history_days(consumption_events, now)
    planning_configured = (
        warehouse.lead_time_days > 0
        or warehouse.safety_stock_percent > 0
        or warehouse.forecast_adjustment is not None
    )
    enough_history = history_90_quantity >= 3 and history_days >= 30
    fallback_reason = _fallback_reason(
        sample_size=history_90_quantity,
        history_days=history_days,
        planning_configured=planning_configured,
    )
    forecast_applied = enough_history and planning_configured
    weighted_rate = history_30_rate * normalized_30 + history_90_rate * normalized_90
    daily_forecast = weighted_rate if forecast_applied else Decimal(0)
    lead_time_demand = (
        _ceil_decimal(daily_forecast * Decimal(warehouse.lead_time_days))
        if forecast_applied
        else 0
    )
    safety_stock = (
        _ceil_decimal(
            Decimal(lead_time_demand)
            * Decimal(warehouse.safety_stock_percent)
            / Decimal(100)
        )
        if forecast_applied
        else 0
    )
    adjustment = (warehouse.forecast_adjustment or 0) if forecast_applied else 0
    target_inventory = max(
        0,
        warehouse.min_inventory + lead_time_demand + safety_stock + adjustment,
    )
    capacity_available = max(
        0,
        warehouse.max_capacity
        - current_occupied
        - pending_inbound
        + scheduled_return,
    )
    raw_inbound = max(0, target_inventory - current_available - pending_inbound)
    baseline_suggestion = max(
        0,
        warehouse.min_inventory - current_available - pending_inbound,
    )
    inbound_suggestion = (
        min(raw_inbound, capacity_available)
        if forecast_applied
        else baseline_suggestion
    )
    capacity_cap_applied = forecast_applied and inbound_suggestion < raw_inbound
    confidence = _forecast_confidence(history_90_quantity)
    if direction == BoxRequestDirection.return_:
        suggested = eligible_return
        formula = "eligible_return"
        explanation = (
            "Return recommendation equals unreserved boxes currently marked Ready to Return."
        )
    else:
        suggested = inbound_suggestion
        if forecast_applied:
            formula = (
                "min(max(0, target_inventory - current_available - pending_inbound), "
                "capacity_available)"
            )
            explanation = (
                f"Target {target_inventory} = minimum {warehouse.min_inventory} + "
                f"lead-time demand {lead_time_demand} + safety stock {safety_stock} "
                f"+ adjustment {adjustment}; subtract {current_available} available "
                f"and {pending_inbound} outstanding inbound, then cap at "
                f"{capacity_available} boxes of projected capacity."
            )
        else:
            formula = "max(0, min_inventory - current_available - pending_inbound)"
            explanation = (
                f"{fallback_reason} The recommendation therefore uses the legacy "
                "minimum-inventory gap minus outstanding inbound quantities."
            )
    return Suggestion(
        direction=direction,
        warehouse_id=warehouse_id,
        analysis_as_of=now,
        current_available=current_available,
        min_inventory=warehouse.min_inventory,
        max_capacity=warehouse.max_capacity,
        current_occupied=current_occupied,
        baseline_gap=minimum_gap,
        minimum_gap=minimum_gap,
        pending_inbound=pending_inbound,
        pending_backorder=pending_backorder,
        scheduled_inbound=scheduled_inbound,
        scheduled_return=scheduled_return,
        history_window_30_start=window_30_start,
        history_window_90_start=window_90_start,
        history_window_end=window_end,
        history_30_quantity=history_30_quantity,
        history_90_quantity=history_90_quantity,
        history_30_daily_rate=float(history_30_rate),
        history_90_daily_rate=float(history_90_rate),
        history_30_weight=warehouse.history_30_weight,
        history_90_weight=warehouse.history_90_weight,
        normalized_30_weight=float(normalized_30),
        normalized_90_weight=float(normalized_90),
        sample_size=history_90_quantity,
        history_days=history_days,
        confidence=(
            "not_applicable"
            if direction == BoxRequestDirection.return_
            else confidence
        ),
        weighted_daily_rate=float(weighted_rate),
        daily_demand_forecast=float(daily_forecast),
        lead_time_days=warehouse.lead_time_days,
        lead_time_demand=lead_time_demand,
        safety_stock_percent=warehouse.safety_stock_percent,
        safety_stock_quantity=safety_stock,
        forecast_adjustment=warehouse.forecast_adjustment,
        adjustment_quantity=adjustment,
        target_inventory=target_inventory,
        capacity_limit=capacity_available,
        capacity_available=capacity_available,
        capacity_cap_applied=capacity_cap_applied,
        fallback_used=(
            direction == BoxRequestDirection.inbound and not forecast_applied
        ),
        fallback_reason=(
            fallback_reason if direction == BoxRequestDirection.inbound else None
        ),
        suggested_quantity=suggested,
        eligible_return=eligible_return,
        consumption_definition=(
            "A consumption event is one status_changed transition from an available "
            "state (received or processing) to an unavailable in-warehouse state "
            "(incomplete or ready_to_return). Created/imported, moved, restored, "
            "returned, unchanged-status events, and exact duplicate audit rows "
            "are excluded."
        ),
        formula=formula,
        explanation=explanation,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _history_boundaries(now: datetime) -> tuple[datetime, datetime, datetime]:
    zone = _app_zone()
    local_date = now.astimezone(zone).date()
    window_end = datetime.combine(
        local_date + timedelta(days=1), time.min, tzinfo=zone
    ).astimezone(UTC)
    window_30_start = datetime.combine(
        local_date - timedelta(days=29), time.min, tzinfo=zone
    ).astimezone(UTC)
    window_90_start = datetime.combine(
        local_date - timedelta(days=89), time.min, tzinfo=zone
    ).astimezone(UTC)
    return window_30_start, window_90_start, window_end


def _app_zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().app_timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _deduplicate_consumption_events(events: list[BoxEvent]) -> list[BoxEvent]:
    """Ignore duplicate audit writes without collapsing later real lifecycles."""
    unique: dict[
        tuple[int, BoxStatus | None, BoxStatus | None, datetime],
        BoxEvent,
    ] = {}
    for event in events:
        key = (
            event.box_id,
            event.from_status,
            event.to_status,
            _as_utc(event.occurred_at),
        )
        current = unique.get(key)
        if current is None or event.id < current.id:
            unique[key] = event
    return list(unique.values())


def _history_days(events: list[BoxEvent], now: datetime) -> int:
    if not events:
        return 0
    zone = _app_zone()
    oldest_date = min(_as_utc(event.occurred_at) for event in events).astimezone(
        zone
    ).date()
    current_date = now.astimezone(zone).date()
    return min(90, max(1, (current_date - oldest_date).days + 1))


def _fallback_reason(
    *,
    sample_size: int,
    history_days: int,
    planning_configured: bool,
) -> str | None:
    if sample_size == 0:
        return "No qualifying consumption events were observed in the last 90 days."
    if sample_size < 3:
        return (
            f"Only {sample_size} qualifying consumption event(s) were observed; "
            "at least 3 are required."
        )
    if history_days < 30:
        return (
            f"Qualifying consumption history spans {history_days} day(s); "
            "at least 30 days are required."
        )
    if not planning_configured:
        return (
            "Demand planning is still on its upgrade-safe defaults "
            "(zero lead time and safety stock, with no adjustment)."
        )
    return None


def _outstanding_quantity(request: BoxRequest) -> int:
    return max(0, request.quantity - (request.actual_received_quantity or 0))


def _is_scheduled_by(request: BoxRequest, horizon: datetime) -> bool:
    if request.scheduled_window_start is not None:
        return _as_utc(request.scheduled_window_start) <= horizon
    if request.requested_date is not None:
        zone = _app_zone()
        return request.requested_date <= horizon.astimezone(zone).date()
    return False


def _ceil_decimal(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _forecast_confidence(sample_size: int) -> str:
    if sample_size < 3:
        return "insufficient"
    if sample_size < 10:
        return "low"
    if sample_size < 30:
        return "medium"
    return "high"


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
        stmt = stmt.with_for_update(of=Box)
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
        pallet_contexts = {
            (item.pallet_id, item.pallet)
            for item in source.items
            if item.pallet_id is not None or item.pallet is not None
        }
        results.append(
            ReturnSource(
                id=source.id,
                warehouse_id=source.warehouse_id,
                completed_at=source.completed_at,
                origin=source.origin,
                delivered_quantity=delivered_quantity,
                eligible_quantity=eligible_quantity,
                pallets=[
                    {"pallet_id": pallet_id, "pallet_number": pallet_number}
                    for pallet_id, pallet_number in sorted(
                        pallet_contexts,
                        key=lambda value: (
                            (value[1] or "").casefold(),
                            value[0] or 0,
                        ),
                    )
                ],
                has_unassigned_boxes=any(
                    item.box_id is not None and item.pallet_id is None
                    for item in source.items
                ),
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
    metadata: dict[str, object] | None = None,
) -> BoxRequestEvent:
    return BoxRequestEvent(
        request_id=request.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        user_id=user.id,
        note=note,
        occurred_at=occurred_at or datetime.now(UTC),
        event_metadata=metadata or {},
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
    current_lots = _lock_lots_for_box_ids(db, [box.id for box in boxes])
    if any(current_lots[box.id] != box.lot_id for box in boxes):
        raise RequestConflictError("a receipt box lot changed concurrently")
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
        recommendation_snapshot=snapshot.to_snapshot(),
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
    request.root_request_id = request.id
    files_by_box_id = _active_files_by_box_ids(db, [box.id for box in boxes])
    for position, box in enumerate(boxes, start=1):
        item = BoxRequestItem(
            request_id=request.id,
            position=position,
            box_id=box.id,
            lot_id=box.lot_id,
            lot=box.lot,
            pallet_id=box.pallet_id,
            pallet=_pallet_number_for_box(db, box),
            box_number=box.box_number,
            contents=box.contents,
        )
        db.add(item)
        db.flush()
        snapshot_request_item_files(
            db,
            item=item,
            files=files_by_box_id[box.id],
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


def receipt_requires_review(
    warehouse: Warehouse,
    origin: BoxRequestOrigin,
    *,
    quantity: int,
) -> bool:
    quarantine = (
        warehouse.quarantine_imports
        if origin == BoxRequestOrigin.xlsx_import
        else warehouse.quarantine_manual_receipts
    )
    return (
        warehouse.receipt_mode.value == "admin_review"
        or warehouse.require_erp_document
        or quarantine
        or (
            warehouse.two_person_approval_threshold is not None
            and quantity >= warehouse.two_person_approval_threshold
        )
    )


def create_staged_receipt(
    db: Session,
    *,
    user: User,
    warehouse_id: int,
    items: list[InboundBoxItem],
    origin: BoxRequestOrigin,
    restore_archived: bool = False,
    commit: bool = True,
) -> BoxRequest:
    if origin not in (BoxRequestOrigin.xlsx_import, BoxRequestOrigin.manual_entry):
        raise RequestRuleError("staged receipt origin must be import or manual entry")
    rows = merge_inbound_items(items)
    if not rows:
        raise RequestRuleError("a staged receipt requires at least one item")
    warehouse = db.scalar(
        select(Warehouse).where(Warehouse.id == warehouse_id).with_for_update()
    )
    if warehouse is None or not warehouse.is_active:
        raise RequestRuleError("warehouse not found or archived")
    if not can_access(user, warehouse_id):
        raise RequestAccessError("no access to warehouse")
    snapshot = calculate_suggestion(
        db,
        user=user,
        warehouse_id=warehouse_id,
        direction=BoxRequestDirection.inbound,
    )
    quarantine = (
        warehouse.quarantine_imports
        if origin == BoxRequestOrigin.xlsx_import
        else warehouse.quarantine_manual_receipts
    )
    now = datetime.now(UTC)
    request = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=warehouse_id,
        quantity=len(rows),
        status=BoxRequestStatus.submitted,
        requester_user_id=user.id,
        origin=origin,
        suggestion_quantity=snapshot.suggested_quantity,
        current_available=snapshot.current_available,
        min_inventory=snapshot.min_inventory,
        pending_inbound=snapshot.pending_inbound,
        eligible_return=snapshot.eligible_return,
        recommendation_snapshot=snapshot.to_snapshot(),
        receipt_restore_archived=restore_archived,
        receipt_quarantine=quarantine,
        receipt_document_required=warehouse.require_erp_document,
        submitted_at=now,
        created_at=now,
        updated_at=now,
        version=1,
    )
    db.add(request)
    db.flush()
    request.root_request_id = request.id
    try:
        lots_by_name = resolve_lot_names_for_use(
            db,
            user=user,
            names=[item.lot for item in rows],
            warehouse_id=warehouse_id,
        )
    except LotRuleError as exc:
        raise RequestRuleError(str(exc)) from exc
    for position, item in enumerate(rows, start=1):
        lot_record = lots_by_name[normalize_lot_name(item.lot)]
        pallet: Pallet | None = None
        if item.pallet_id is not None and item.pallet_number is None:
            raise RequestRuleError("pallet_id requires pallet_number")
        if item.pallet_number is not None:
            try:
                pallet = resolve_or_create_active_pallet(
                    db,
                    user=user,
                    lot_id=lot_record.id,
                    warehouse_id=warehouse_id,
                    pallet_number=item.pallet_number,
                    pallet_id=item.pallet_id,
                )
            except PalletRuleError as exc:
                raise RequestRuleError(str(exc)) from exc
        request_item = BoxRequestItem(
            request_id=request.id,
            position=position,
            lot_id=lot_record.id,
            lot=lot_record.name,
            pallet_id=pallet.id if pallet is not None else None,
            pallet=pallet.pallet_number if pallet is not None else None,
            box_number=item.box_number,
            contents=item.contents,
        )
        db.add(request_item)
        db.flush()
        snapshot_request_item_files(
            db,
            item=request_item,
            values=_staged_file_values(item),
        )
    db.add(
        _event(
            request,
            event_type=BoxRequestEventType.submitted,
            user=user,
            from_status=None,
            to_status=BoxRequestStatus.submitted,
            note="Self receipt staged for administrator review.",
            occurred_at=now,
            metadata={
                "staged_receipt": True,
                "origin": origin.value,
                "restore_archived": restore_archived,
                "quarantine": quarantine,
                "document_required": warehouse.require_erp_document,
            },
        )
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.staged,
        event_token="staged:1",
    )
    if warehouse.require_erp_document:
        enqueue_request_event(
            db,
            request=request,
            kind=RequestNotificationKind.document_needed,
            event_token="document-needed:1",
        )
    if commit:
        db.commit()
        db.refresh(request)
    else:
        db.flush()
    return request


def finalize_staged_receipt(
    db: Session,
    *,
    request_id: int,
    user: User,
    expected_version: int,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    if user.role != UserRole.admin:
        raise RequestAccessError("staged receipts require admin review")
    if request.origin not in (
        BoxRequestOrigin.xlsx_import,
        BoxRequestOrigin.manual_entry,
    ) or request.status != BoxRequestStatus.submitted:
        raise RequestConflictError("request is not a pending self receipt")
    warehouse = db.scalar(
        select(Warehouse)
        .where(Warehouse.id == request.warehouse_id)
        .with_for_update()
    )
    if warehouse is None or not warehouse.is_active:
        raise RequestConflictError("warehouse is archived")
    threshold = warehouse.two_person_approval_threshold
    if (
        threshold is not None
        and request.quantity >= threshold
        and request.requester_user_id == user.id
    ):
        raise RequestAccessError(
            "a different administrator must review this receipt at the configured threshold"
        )
    if request.receipt_document_required:
        document_id = db.scalar(
            select(BoxRequestDocument.id).where(
                BoxRequestDocument.request_id == request.id,
                BoxRequestDocument.document_type == BoxRequestDocumentType.delivery_note,
                BoxRequestDocument.is_current.is_(True),
            )
        )
        if document_id is None:
            raise RequestRuleError("a current delivery_note is required")

    staged_items = sorted(request.items, key=lambda item: item.position)
    _lock_lots_for_current_links(db, [item.lot_id for item in staged_items])
    identities = [(item.lot_id, item.box_number or "") for item in staged_items]
    if len(identities) != len(set(identities)):
        raise RequestConflictError("staged receipt contains duplicate box identities")
    existing_by_identity: dict[tuple[int | None, str], Box] = {}
    for lot_id, box_number in identities:
        existing = db.scalar(
            select(Box)
            .where(Box.lot_id == lot_id, Box.box_number == box_number)
            .with_for_update(of=Box)
        )
        if existing is not None:
            if existing.archived_at is None:
                raise RequestConflictError(
                    f"box {box_number!r} already exists in the staged lot"
                )
            if not request.receipt_restore_archived:
                raise RequestConflictError(
                    f"box {box_number!r} in the staged lot is archived; "
                    "stage with restore_archived to restore it"
                )
            if not can_access(user, existing.current_warehouse_id):
                raise RequestAccessError("no access to archived box warehouse")
            existing_by_identity[(lot_id, box_number)] = existing

    occupied = int(
        db.scalar(
            select(func.count(Box.id)).where(
                Box.current_warehouse_id == warehouse.id,
                Box.archived_at.is_(None),
                Box.status.in_(ACTIVE_STATUSES),
            )
        )
        or 0
    )
    if occupied + len(staged_items) > warehouse.max_capacity:
        raise RequestConflictError(
            f"receipt exceeds warehouse capacity ({occupied} occupied, "
            f"{warehouse.max_capacity} maximum)"
        )

    target_status = (
        BoxStatus.quarantined if request.receipt_quarantine else BoxStatus.received
    )
    pallets_by_item_id: dict[int, Pallet | None] = {}
    for item in staged_items:
        if item.pallet is None:
            if item.pallet_id is not None:
                raise RequestConflictError(
                    "staged receipt item has pallet_id without a pallet snapshot"
                )
            pallets_by_item_id[item.id] = None
            continue
        try:
            pallets_by_item_id[item.id] = resolve_or_create_active_pallet(
                db,
                user=user,
                lot_id=item.lot_id or 0,
                warehouse_id=request.warehouse_id,
                pallet_number=item.pallet,
                pallet_id=item.pallet_id,
                allow_snapshot_number_mismatch=True,
            )
        except PalletRuleError as exc:
            raise RequestConflictError(str(exc)) from exc
    now = datetime.now(UTC)
    try:
        for item in staged_items:
            key = (item.lot_id, item.box_number or "")
            target_pallet = pallets_by_item_id[item.id]
            staged_files = (
                [
                    FileInput(
                        reference=snapshot.reference,
                        description=snapshot.description,
                        barcode=snapshot.barcode,
                    )
                    for snapshot in item.file_snapshots
                ]
                if item.file_snapshots
                else None
            )
            box = (
                restore_archived_box(
                    db,
                    user=user,
                    box_number=item.box_number or "",
                    lot_id=item.lot_id,
                    pallet_number=(
                        target_pallet.pallet_number
                        if target_pallet is not None
                        else None
                    ),
                    pallet_id=target_pallet.id if target_pallet is not None else None,
                    contents=item.contents,
                    files=staged_files,
                    warehouse_id=request.warehouse_id,
                    note=f"Restored through staged receipt #{request.id}.",
                    restored_status=target_status,
                    commit=False,
                )
                if key in existing_by_identity
                else create_box(
                    db,
                    user=user,
                    box_number=item.box_number or "",
                    lot_id=item.lot_id,
                    pallet_number=(
                        target_pallet.pallet_number
                        if target_pallet is not None
                        else None
                    ),
                    pallet_id=target_pallet.id if target_pallet is not None else None,
                    contents=item.contents,
                    files=staged_files,
                    warehouse_id=request.warehouse_id,
                    note=f"Created through staged receipt #{request.id}.",
                    initial_status=target_status,
                    commit=False,
                )
            )
            if box is None:
                raise RequestConflictError("archived box disappeared during finalization")
            item.box_id = box.id
            current_files = _active_files_by_box_ids(db, [box.id], lock=True)[box.id]
            by_reference = {
                file.normalized_reference: file for file in current_files
            }
            for snapshot in item.file_snapshots:
                file = by_reference.get(
                    normalize_box_file_reference(snapshot.reference)
                )
                if file is None:
                    raise RequestConflictError(
                        f"staged file {snapshot.reference!r} was not created"
                    )
                snapshot.file_id = file.id
    except (BoxRuleError, IntegrityError) as exc:
        db.rollback()
        detail = (
            "a staged box identity was created concurrently; refresh and retry"
            if isinstance(exc, IntegrityError)
            else str(exc)
        )
        raise RequestConflictError(detail) from exc

    request.actual_received_quantity = len(staged_items)
    request.variance_quantity = 0
    request.approved_by_user_id = user.id
    request.approved_at = now
    request.completed_by_user_id = user.id
    request.completed_at = now
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.completed,
        event_type=BoxRequestEventType.completed,
        note=(
            "Staged receipt finalized into quarantine."
            if request.receipt_quarantine
            else "Staged receipt approved and finalized."
        ),
        now=now,
        metadata={
            "staged_receipt": True,
            "reviewer_user_id": user.id,
            "quarantine": request.receipt_quarantine,
            "box_ids": [item.box_id for item in staged_items],
        },
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.approved,
        event_token=f"approved:{request.version}",
    )
    db.commit()
    db.refresh(request)
    return request


def create_request(
    db: Session,
    *,
    user: User,
    warehouse_id: int,
    target_warehouse_id: int | None,
    direction: BoxRequestDirection,
    quantity: int,
    source_inbound_request_id: int | None = None,
    box_ids: list[int] | None = None,
    priority: BoxRequestPriority = BoxRequestPriority.normal,
    requested_date: date | None = None,
    sla_deadline: datetime | None = None,
    destination_contact: str | None = None,
    internal_location: str | None = None,
    special_handling_instructions: str | None = None,
) -> BoxRequest:
    selected_box_ids = box_ids or []
    if direction == BoxRequestDirection.inbound:
        if target_warehouse_id is not None:
            raise RequestRuleError(
                "inbound requests cannot specify target_warehouse_id"
            )
        if source_inbound_request_id is not None or selected_box_ids:
            raise RequestRuleError(
                "inbound requests cannot specify a source request or return boxes"
            )
    else:
        if target_warehouse_id is None:
            raise RequestRuleError(
                "target_warehouse_id is required for return requests"
            )
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

    # Lock the source and return target in stable order. Besides serializing
    # reservations per source, this prevents a target from being archived
    # between validation and the request commit without deadlocking warehouse
    # archival, which locks the same rows in ID order.
    warehouse_ids = {warehouse_id}
    if target_warehouse_id is not None:
        warehouse_ids.add(target_warehouse_id)
    locked_warehouses = {
        warehouse.id: warehouse
        for warehouse in db.scalars(
            select(Warehouse)
            .where(Warehouse.id.in_(sorted(warehouse_ids)))
            .order_by(Warehouse.id)
            .with_for_update()
        ).all()
    }
    warehouse = locked_warehouses.get(warehouse_id)
    if warehouse is None:
        raise RequestRuleError("warehouse not found")
    if not warehouse.is_active:
        raise RequestRuleError("warehouse is archived")
    if not can_access(user, warehouse_id):
        raise RequestAccessError("no access to warehouse")
    if direction == BoxRequestDirection.return_:
        assert target_warehouse_id is not None
        target_warehouse = locked_warehouses.get(target_warehouse_id)
        if target_warehouse is None:
            raise RequestRuleError(f"unknown warehouse_id: {target_warehouse_id}")
        if not target_warehouse.is_active:
            raise RequestRuleError(f"warehouse_id {target_warehouse_id} is archived")
        if not can_access(user, target_warehouse_id):
            raise RequestAccessError("no access to target warehouse")
    snapshot = calculate_suggestion(
        db, user=user, warehouse_id=warehouse_id, direction=direction
    )
    return_boxes: list[Box] = []
    source_eligible_quantity = snapshot.eligible_return
    if direction == BoxRequestDirection.return_:
        locked_lots_by_box = _lock_lots_for_box_ids(db, selected_box_ids)
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
            .with_for_update(of=Box)
        ).all()
        candidates_by_id = {box.id: box for box in candidates}
        source_eligible_quantity = len(candidates)
        if any(box_id not in candidates_by_id for box_id in selected_box_ids):
            raise RequestConflictError(
                "one or more selected boxes are not ready, do not belong to the "
                "source inbound request, or are already reserved"
            )
        return_boxes = [candidates_by_id[box_id] for box_id in selected_box_ids]
        if any(
            locked_lots_by_box[box.id] != box.lot_id
            for box in return_boxes
        ):
            raise RequestConflictError("one or more selected box lots changed")

    now = datetime.now(UTC)
    recommendation_snapshot = snapshot.to_snapshot()
    if direction == BoxRequestDirection.return_:
        recommendation_snapshot.update(
            {
                "suggested_quantity": source_eligible_quantity,
                "eligible_return": source_eligible_quantity,
                "formula": "source_inbound_eligible_return",
                "explanation": (
                    "Return recommendation equals unreserved Ready to Return boxes "
                    f"from inbound request #{source_inbound_request_id}."
                ),
            }
        )
    request = BoxRequest(
        direction=direction,
        warehouse_id=warehouse_id,
        target_warehouse_id=target_warehouse_id,
        quantity=quantity,
        status=BoxRequestStatus.submitted,
        requester_user_id=user.id,
        priority=priority,
        requested_date=requested_date,
        sla_deadline=sla_deadline,
        destination_contact=_clean_optional(destination_contact),
        internal_location=_clean_optional(internal_location),
        special_handling_instructions=_clean_optional(
            special_handling_instructions
        ),
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
        recommendation_snapshot=recommendation_snapshot,
        submitted_at=now,
        created_at=now,
        updated_at=now,
        version=1,
    )
    db.add(request)
    db.flush()
    request.root_request_id = request.id

    if direction == BoxRequestDirection.return_:
        files_by_box_id = _active_files_by_box_ids(
            db, [box.id for box in return_boxes], lock=True
        )
        for position, box in enumerate(return_boxes, start=1):
            item = BoxRequestItem(
                request_id=request.id,
                position=position,
                box_id=box.id,
                lot_id=box.lot_id,
                lot=box.lot,
                pallet_id=box.pallet_id,
                pallet=_pallet_number_for_box(db, box),
                box_number=box.box_number,
                contents=box.contents,
            )
            db.add(item)
            db.flush()
            snapshot_request_item_files(
                db, item=item, files=files_by_box_id[box.id]
            )

    db.add(
        _event(
            request,
            event_type=BoxRequestEventType.submitted,
            user=user,
            from_status=None,
            to_status=BoxRequestStatus.submitted,
            metadata={
                "request_id": request.id,
                "source_warehouse_id": request.warehouse_id,
                "target_warehouse_id": request.target_warehouse_id,
            },
        )
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.submitted,
        event_token="submitted:1",
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
    expected_version: int,
) -> None:
    if not can_access(user, request.warehouse_id):
        raise RequestAccessError("request not found")
    if expected_version != request.version:
        raise RequestConflictError("request changed; refresh and retry")


def _require_mover(user: User) -> None:
    if user.role not in (UserRole.admin, UserRole.warehouse_mover):
        raise RequestAccessError("warehouse mover role required")


def _open_exception(db: Session, request_id: int) -> BoxRequestException | None:
    return db.scalar(
        select(BoxRequestException)
        .where(
            BoxRequestException.request_id == request_id,
            BoxRequestException.resolved_at.is_(None),
        )
        .order_by(BoxRequestException.created_at.desc(), BoxRequestException.id.desc())
        .with_for_update()
    )


def _require_no_open_exception(db: Session, request: BoxRequest) -> None:
    if _open_exception(db, request.id) is not None:
        raise RequestConflictError(
            "resolve the active operational exception before changing lifecycle state"
        )


def _resolve_exception(
    exception: BoxRequestException,
    *,
    user: User,
    resolution: str,
    now: datetime,
) -> None:
    cleaned = resolution.strip()
    if not cleaned:
        raise RequestRuleError("resolution is required")
    exception.resolved_by_user_id = user.id
    exception.resolved_at = now
    exception.resolution = cleaned


def _apply_revised_window(
    request: BoxRequest,
    *,
    start: datetime,
    end: datetime,
) -> int:
    if end <= start:
        raise RequestRuleError("revised window end must be after the start")
    adjustment_seconds = 0
    if request.scheduled_window_start is not None and request.sla_deadline is not None:
        adjustment = _as_utc(start) - _as_utc(request.scheduled_window_start)
        request.sla_deadline = _as_utc(request.sla_deadline) + adjustment
        adjustment_seconds = int(adjustment.total_seconds())
    request.scheduled_window_start = start
    request.scheduled_window_end = end
    return adjustment_seconds


def _clean_optional(value: str | None) -> str | None:
    cleaned = value.strip() if value else ""
    return cleaned or None


def _event_value(value: object) -> object | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _assignee(db: Session, *, user_id: int, warehouse_id: int) -> User:
    assignee = db.get(User, user_id)
    if (
        assignee is None
        or not assignee.is_active
        or assignee.role not in (UserRole.admin, UserRole.warehouse_mover)
        or not can_access(assignee, warehouse_id)
    ):
        raise RequestRuleError(
            "assignee must be an active admin or warehouse mover with warehouse access"
        )
    return assignee


def update_request_coordination(
    db: Session,
    *,
    request_id: int,
    user: User,
    expected_version: int,
    changes: dict[str, object],
) -> BoxRequest:
    """Update coordination fields; admin/mover + warehouse ACL is required."""
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    if request.status in (
        BoxRequestStatus.completed,
        BoxRequestStatus.rejected,
        BoxRequestStatus.cancelled,
    ):
        raise RequestConflictError("closed requests cannot be coordinated")

    allowed = {
        "priority",
        "requested_date",
        "scheduled_window_start",
        "scheduled_window_end",
        "sla_deadline",
        "assigned_mover_user_id",
        "destination_contact",
        "internal_location",
        "special_handling_instructions",
    }
    supplied = {key: value for key, value in changes.items() if key in allowed}
    if not supplied:
        raise RequestRuleError("provide at least one coordination field")
    if (
        "assigned_mover_user_id" in supplied
        and supplied["assigned_mover_user_id"] is not None
    ):
        _assignee(
            db,
            user_id=int(supplied["assigned_mover_user_id"]),
            warehouse_id=request.warehouse_id,
        )
    final_start = supplied.get(
        "scheduled_window_start", request.scheduled_window_start
    )
    final_end = supplied.get("scheduled_window_end", request.scheduled_window_end)
    if final_end is not None and final_start is None:
        raise RequestRuleError("scheduled window start is required")
    if (
        isinstance(final_start, datetime)
        and isinstance(final_end, datetime)
        and final_end <= final_start
    ):
        raise RequestRuleError("scheduled window end must be after the start")

    categories: dict[str, list[str]] = {
        "assignment": [],
        "schedule": [],
        "coordination": [],
    }
    category_changes: dict[str, dict[str, dict[str, object | None]]] = {
        "assignment": {},
        "schedule": {},
        "coordination": {},
    }
    string_fields = {
        "destination_contact",
        "internal_location",
        "special_handling_instructions",
    }
    for field, value in supplied.items():
        if field in string_fields:
            value = _clean_optional(value if isinstance(value, str) else None)
        old = getattr(request, field)
        if old == value:
            continue
        setattr(request, field, value)
        category = (
            "assignment"
            if field == "assigned_mover_user_id"
            else "schedule"
            if field in {"scheduled_window_start", "scheduled_window_end"}
            else "coordination"
        )
        categories[category].append(f"{field}: {old!s} -> {value!s}")
        category_changes[category][field] = {
            "from": _event_value(old),
            "to": _event_value(value),
        }
    changed = [entry for entries in categories.values() for entry in entries]
    if not changed:
        raise RequestRuleError("coordination fields are unchanged")

    now = datetime.now(UTC)
    request.version += 1
    request.updated_at = now
    event_types = {
        "assignment": BoxRequestEventType.assignment_changed,
        "schedule": BoxRequestEventType.schedule_changed,
        "coordination": BoxRequestEventType.coordination_changed,
    }
    notification_kinds = {
        "assignment": RequestNotificationKind.assignment_changed,
        "schedule": RequestNotificationKind.scheduled,
        "coordination": RequestNotificationKind.coordination_changed,
    }
    for category, entries in categories.items():
        if not entries:
            continue
        note = "; ".join(entries)
        db.add(
            _event(
                request,
                event_type=event_types[category],
                user=user,
                from_status=request.status,
                to_status=request.status,
                note=note,
                occurred_at=now,
                metadata={
                    "category": category,
                    "changes": category_changes[category],
                },
            )
        )
        enqueue_request_event(
            db,
            request=request,
            kind=notification_kinds[category],
            event_token=f"{category}:{request.version}",
            detail=note,
        )
    db.commit()
    db.refresh(request)
    return request


def add_request_comment(
    db: Session,
    *,
    request_id: int,
    user: User,
    expected_version: int,
    body: str,
) -> BoxRequestComment:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    cleaned = body.strip()
    if not cleaned:
        raise RequestRuleError("comment cannot be empty")
    now = datetime.now(UTC)
    comment = BoxRequestComment(
        request_id=request.id,
        author_user_id=user.id,
        body=cleaned,
        created_at=now,
    )
    db.add(comment)
    request.version += 1
    request.updated_at = now
    db.add(
        _event(
            request,
            event_type=BoxRequestEventType.comment_added,
            user=user,
            from_status=request.status,
            to_status=request.status,
            note=cleaned,
            occurred_at=now,
        )
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.comment_added,
        event_token=f"comment:{request.version}",
        detail=f"{user.display_name or user.email}: {cleaned}",
        exclude_user_id=user.id,
    )
    db.commit()
    db.refresh(comment)
    return comment


def _transition(
    db: Session,
    request: BoxRequest,
    *,
    user: User,
    target: BoxRequestStatus,
    event_type: BoxRequestEventType,
    note: str | None = None,
    now: datetime | None = None,
    metadata: dict[str, object] | None = None,
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
            metadata=metadata,
        )
    )
    return request


def approve_request(
    db: Session, *, request_id: int, user: User, expected_version: int
) -> BoxRequest:
    candidate = db.get(BoxRequest, request_id)
    if (
        candidate is not None
        and candidate.origin
        in (BoxRequestOrigin.xlsx_import, BoxRequestOrigin.manual_entry)
        and candidate.status == BoxRequestStatus.submitted
    ):
        return finalize_staged_receipt(
            db,
            request_id=request_id,
            user=user,
            expected_version=expected_version,
        )
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
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.approved,
        event_token=f"approved:{request.version}",
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
    expected_version: int,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    is_staged_receipt = request.origin in (
        BoxRequestOrigin.xlsx_import,
        BoxRequestOrigin.manual_entry,
    )
    if is_staged_receipt:
        if user.role != UserRole.admin:
            raise RequestAccessError("staged receipts require admin review")
    else:
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
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.rejected,
        event_token=f"rejected:{request.version}",
        detail=request.rejection_reason,
    )
    db.commit()
    db.refresh(request)
    return request


def start_preparation(
    db: Session, *, request_id: int, user: User, expected_version: int
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    _require_no_open_exception(db, request)
    if request.status != BoxRequestStatus.approved:
        raise RequestConflictError("only approved requests can start preparation")
    now = datetime.now(UTC)
    request.preparing_by_user_id = user.id
    request.preparing_at = now
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.preparing,
        event_type=BoxRequestEventType.preparation_started,
        now=now,
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.preparation_started,
        event_token=f"preparing:{request.version}",
    )
    db.commit()
    db.refresh(request)
    return request


def mark_ready_for_transport(
    db: Session, *, request_id: int, user: User, expected_version: int
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    _require_no_open_exception(db, request)
    if request.status != BoxRequestStatus.preparing:
        raise RequestConflictError("only requests being prepared can be marked ready")
    now = datetime.now(UTC)
    request.ready_for_transport_by_user_id = user.id
    request.ready_for_transport_at = now
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.ready_for_transport,
        event_type=BoxRequestEventType.ready_for_transport,
        now=now,
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.ready_for_transport,
        event_token=f"ready:{request.version}",
    )
    db.commit()
    db.refresh(request)
    return request


def start_transit(
    db: Session, *, request_id: int, user: User, expected_version: int
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    _require_no_open_exception(db, request)
    if request.status != BoxRequestStatus.ready_for_transport:
        raise RequestConflictError("only transport-ready requests can start transit")
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
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.transport_started,
        event_token=f"transport:{request.version}",
    )
    db.commit()
    db.refresh(request)
    return request


def mark_awaiting_confirmation(
    db: Session, *, request_id: int, user: User, expected_version: int
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    _require_no_open_exception(db, request)
    if request.status != BoxRequestStatus.in_transit:
        raise RequestConflictError("only in-transit requests can be marked arrived")
    now = datetime.now(UTC)
    request.awaiting_confirmation_by_user_id = user.id
    request.awaiting_confirmation_at = now
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.awaiting_confirmation,
        event_type=BoxRequestEventType.awaiting_confirmation,
        note=(
            "Delivered; awaiting requester confirmation."
            if request.direction == BoxRequestDirection.inbound
            else "Collected; awaiting warehouse confirmation."
        ),
        now=now,
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.acceptance_required,
        event_token=f"confirmation:{request.version}",
    )
    db.commit()
    db.refresh(request)
    return request


def hold_request(
    db: Session,
    *,
    request_id: int,
    user: User,
    reason: str,
    expected_version: int,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    _require_no_open_exception(db, request)
    if request.status not in ACTIVE_REQUEST_STATUSES:
        raise RequestConflictError("only active requests can be placed on hold")
    cleaned = reason.strip()
    if not cleaned:
        raise RequestRuleError("hold reason is required")
    now = datetime.now(UTC)
    exception = BoxRequestException(
        request_id=request.id,
        exception_kind=BoxRequestExceptionKind.hold,
        reason=cleaned,
        resume_target=request.status,
        created_by_user_id=user.id,
        created_at=now,
    )
    db.add(exception)
    request.version += 1
    request.updated_at = now
    db.add(
        _event(
            request,
            event_type=BoxRequestEventType.hold_started,
            user=user,
            from_status=request.status,
            to_status=request.status,
            note=cleaned,
            occurred_at=now,
            metadata={
                "exception_kind": BoxRequestExceptionKind.hold.value,
                "resume_target": request.status.value,
                "sla_paused_at": now.isoformat(),
            },
        )
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.hold_started,
        event_token=f"hold:{request.version}",
        detail=cleaned,
    )
    db.commit()
    db.refresh(request)
    return request


def resume_request(
    db: Session,
    *,
    request_id: int,
    user: User,
    resolution: str,
    expected_version: int,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    exception = _open_exception(db, request.id)
    if exception is None or exception.exception_kind != BoxRequestExceptionKind.hold:
        raise RequestConflictError("request has no active hold")
    if (
        exception.resume_target not in ACTIVE_REQUEST_STATUSES
        or exception.resume_target != request.status
    ):
        raise RequestConflictError("the hold has an invalid resume target")
    now = datetime.now(UTC)
    paused_seconds = max(
        0,
        int((_as_utc(now) - _as_utc(exception.created_at)).total_seconds()),
    )
    if request.sla_deadline is not None:
        request.sla_deadline = _as_utc(request.sla_deadline) + timedelta(
            seconds=paused_seconds
        )
    _resolve_exception(exception, user=user, resolution=resolution, now=now)
    request.version += 1
    request.updated_at = now
    db.add(
        _event(
            request,
            event_type=BoxRequestEventType.resumed,
            user=user,
            from_status=request.status,
            to_status=request.status,
            note=exception.resolution,
            occurred_at=now,
            metadata={
                "exception_id": exception.id,
                "resume_target": exception.resume_target.value,
                "sla_extension_seconds": paused_seconds,
            },
        )
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.resumed,
        event_token=f"resume:{request.version}",
        detail=exception.resolution,
    )
    db.commit()
    db.refresh(request)
    return request


def reschedule_request(
    db: Session,
    *,
    request_id: int,
    user: User,
    reason: str,
    revised_window_start: datetime,
    revised_window_end: datetime,
    expected_version: int,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    _require_no_open_exception(db, request)
    if request.status not in ACTIVE_REQUEST_STATUSES:
        raise RequestConflictError("only active requests can be rescheduled")
    cleaned = reason.strip()
    if not cleaned:
        raise RequestRuleError("reschedule reason is required")
    old_start = request.scheduled_window_start
    old_end = request.scheduled_window_end
    sla_adjustment = _apply_revised_window(
        request,
        start=revised_window_start,
        end=revised_window_end,
    )
    now = datetime.now(UTC)
    exception = BoxRequestException(
        request_id=request.id,
        exception_kind=BoxRequestExceptionKind.reschedule,
        reason=cleaned,
        revised_window_start=revised_window_start,
        revised_window_end=revised_window_end,
        resume_target=request.status,
        created_by_user_id=user.id,
        created_at=now,
        resolved_by_user_id=user.id,
        resolved_at=now,
        resolution="Transport window revised.",
    )
    db.add(exception)
    request.version += 1
    request.updated_at = now
    db.add(
        _event(
            request,
            event_type=BoxRequestEventType.rescheduled,
            user=user,
            from_status=request.status,
            to_status=request.status,
            note=cleaned,
            occurred_at=now,
            metadata={
                "old_window_start": _event_value(old_start),
                "old_window_end": _event_value(old_end),
                "revised_window_start": revised_window_start.isoformat(),
                "revised_window_end": revised_window_end.isoformat(),
                "resume_target": request.status.value,
                "sla_adjustment_seconds": sla_adjustment,
            },
        )
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.rescheduled,
        event_token=f"rescheduled:{request.version}",
        detail=cleaned,
    )
    db.commit()
    db.refresh(request)
    return request


def report_failed_delivery(
    db: Session,
    *,
    request_id: int,
    user: User,
    reason: str,
    revised_window_start: datetime | None,
    revised_window_end: datetime | None,
    expected_version: int,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    _require_no_open_exception(db, request)
    if request.status != BoxRequestStatus.in_transit:
        raise RequestConflictError("only in-transit requests can report failed transport")
    if (revised_window_start is None) != (revised_window_end is None):
        raise RequestRuleError("both revised window values are required")
    if (
        revised_window_start is not None
        and revised_window_end is not None
        and revised_window_end <= revised_window_start
    ):
        raise RequestRuleError("revised window end must be after the start")
    cleaned = reason.strip()
    if not cleaned:
        raise RequestRuleError("failed transport reason is required")
    now = datetime.now(UTC)
    exception = BoxRequestException(
        request_id=request.id,
        exception_kind=BoxRequestExceptionKind.failed_delivery,
        reason=cleaned,
        revised_window_start=revised_window_start,
        revised_window_end=revised_window_end,
        resume_target=BoxRequestStatus.ready_for_transport,
        created_by_user_id=user.id,
        created_at=now,
    )
    db.add(exception)
    request.version += 1
    request.updated_at = now
    db.add(
        _event(
            request,
            event_type=BoxRequestEventType.failed_delivery,
            user=user,
            from_status=request.status,
            to_status=request.status,
            note=cleaned,
            occurred_at=now,
            metadata={
                "exception_kind": BoxRequestExceptionKind.failed_delivery.value,
                "resume_target": BoxRequestStatus.ready_for_transport.value,
                "revised_window_start": _event_value(revised_window_start),
                "revised_window_end": _event_value(revised_window_end),
            },
        )
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.failed_delivery,
        event_token=f"failed-delivery:{request.version}",
        detail=cleaned,
    )
    db.commit()
    db.refresh(request)
    return request


def retry_transport(
    db: Session,
    *,
    request_id: int,
    user: User,
    resolution: str,
    expected_version: int,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    _require_mover(user)
    exception = _open_exception(db, request.id)
    if (
        exception is None
        or exception.exception_kind != BoxRequestExceptionKind.failed_delivery
    ):
        raise RequestConflictError("request has no failed transport to retry")
    if (
        request.status != BoxRequestStatus.in_transit
        or exception.resume_target != BoxRequestStatus.ready_for_transport
    ):
        raise RequestConflictError("the failed transport has an invalid resume target")
    now = datetime.now(UTC)
    sla_adjustment = 0
    if (
        exception.revised_window_start is not None
        and exception.revised_window_end is not None
    ):
        sla_adjustment = _apply_revised_window(
            request,
            start=exception.revised_window_start,
            end=exception.revised_window_end,
        )
    recovery_seconds = max(
        0,
        int((_as_utc(now) - _as_utc(exception.created_at)).total_seconds()),
    )
    if request.sla_deadline is not None:
        request.sla_deadline = _as_utc(request.sla_deadline) + timedelta(
            seconds=recovery_seconds
        )
    _resolve_exception(exception, user=user, resolution=resolution, now=now)
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.ready_for_transport,
        event_type=BoxRequestEventType.transport_retry,
        note=exception.resolution,
        now=now,
        metadata={
            "exception_id": exception.id,
            "resume_target": exception.resume_target.value,
            "sla_adjustment_seconds": sla_adjustment,
            "sla_recovery_extension_seconds": recovery_seconds,
        },
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.transport_retry,
        event_token=f"retry:{request.version}",
        detail=exception.resolution,
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
    expected_version: int,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    if user.role != UserRole.admin and request.requester_user_id != user.id:
        raise RequestAccessError("only the requester can cancel this request")
    _require_no_open_exception(db, request)
    if request.status not in (
        BoxRequestStatus.submitted,
        BoxRequestStatus.approved,
        BoxRequestStatus.preparing,
        BoxRequestStatus.ready_for_transport,
    ):
        raise RequestConflictError("request can no longer be cancelled")
    request.cancellation_reason = reason.strip() if reason else None
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.cancelled,
        event_type=BoxRequestEventType.cancelled,
        note=request.cancellation_reason,
        metadata={
            "cancelled_reservation": request.direction
            == BoxRequestDirection.return_,
            "reason": request.cancellation_reason,
            "operation": "request_cancelled",
        },
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.reservation_cancelled,
        event_token=f"cancelled:{request.version}",
        detail=request.cancellation_reason,
    )
    db.commit()
    db.refresh(request)
    return request


def merge_inbound_items(items: list[InboundBoxItem]) -> list[InboundBoxItem]:
    """Collapse spreadsheet rows that describe the same physical box.

    A box is unique within a lot, so rows are grouped by canonical
    ``(lot, box_number)``. Distinct contents are sorted canonically before they
    are joined so row order cannot change the accepted or persisted result.
    """
    grouped: dict[
        tuple[str, str],
        tuple[
            str,
            str | None,
            str | None,
            int | None,
            list[str],
            dict[str, FileInput],
            bool,
        ],
    ] = {}
    reference_targets: dict[
        tuple[str, str], tuple[tuple[str, str], FileInput]
    ] = {}
    for item in items:
        try:
            lot = validate_lot_name(item.lot)
            normalized_lot = normalize_lot_name(lot)
        except LotRuleError as exc:
            raise RequestRuleError(str(exc)) from exc
        try:
            box_number = normalize_box_number(item.box_number)
        except BoxRuleError as exc:
            raise RequestRuleError(str(exc)) from exc
        pallet_number = clean_optional_pallet_number(item.pallet_number)
        normalized_pallet = (
            normalize_pallet_number(pallet_number)
            if pallet_number is not None
            else None
        )
        key = (normalized_lot, box_number)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = (
                lot,
                pallet_number,
                normalized_pallet,
                item.pallet_id,
                [],
                {},
                item.files is not None,
            )
        else:
            existing_assigned = existing[1] is not None
            item_assigned = pallet_number is not None
            if existing_assigned != item_assigned:
                raise RequestRuleError(
                    f"box {box_number!r} in lot {lot!r} has conflicting pallet "
                    "assignment: Assigned vs Unassigned"
                )
            if existing_assigned and (
                existing[2] != normalized_pallet
                or (
                    existing[3] is not None
                    and item.pallet_id is not None
                    and existing[3] != item.pallet_id
                )
            ):
                raise RequestRuleError(
                    f"box {box_number!r} in lot {lot!r} is assigned to "
                    "different pallet values"
                )
            if existing[3] is None and item.pallet_id is not None:
                grouped[key] = (
                    existing[0],
                    existing[1],
                    existing[2],
                    item.pallet_id,
                    existing[4],
                    existing[5],
                    existing[6] or item.files is not None,
                )
        existing = grouped[key]
        canonical_pallet_number = existing[1]
        if canonical_pallet_number is not None and pallet_number is not None:
            canonical_pallet_number = min(
                canonical_pallet_number,
                pallet_number,
                key=lambda value: (value.casefold(), value),
            )
        grouped[key] = (
            min(existing[0], lot, key=lambda value: (value.casefold(), value)),
            canonical_pallet_number,
            existing[2],
            existing[3],
            existing[4],
            existing[5],
            existing[6] or item.files is not None,
        )
        contents = grouped[key][4]
        value = (item.contents or "").strip()
        if value and value not in contents:
            contents.append(value)
        if item.files is not None:
            file_values = grouped[key][5]
            for file in item.files:
                normalized_reference = normalize_box_file_reference(file.reference)
                existing_file = file_values.get(normalized_reference)
                if existing_file is not None and existing_file != file:
                    raise RequestRuleError(
                        f"file reference {file.reference!r} is repeated with "
                        "conflicting details"
                    )
                reference_identity = (normalized_lot, normalized_reference)
                other_target = reference_targets.get(reference_identity)
                if other_target is not None and other_target[0] != key:
                    raise RequestRuleError(
                        f"file reference {file.reference!r} targets two boxes "
                        f"in lot {lot!r}"
                    )
                file_values.setdefault(normalized_reference, file)
                reference_targets.setdefault(reference_identity, (key, file))

    merged: list[InboundBoxItem] = []
    for (
        _normalized_lot,
        box_number,
    ), (
        lot,
        pallet_number,
        _normalized_pallet,
        pallet_id,
        contents,
        files,
        has_structured_files,
    ) in grouped.items():
        combined = " | ".join(sorted(contents))
        if len(combined) > 2000:
            raise RequestRuleError(
                f"combined contents for box {box_number!r} in lot {lot!r} "
                "exceed 2000 characters"
            )
        merged.append(
            InboundBoxItem(
                lot=lot,
                box_number=box_number,
                pallet_number=pallet_number,
                pallet_id=pallet_id,
                contents=combined or None,
                files=(
                    sorted(
                        files.values(),
                        key=lambda file: (
                            normalize_box_file_reference(file.reference),
                            file.reference,
                        ),
                    )
                    if has_structured_files
                    else None
                ),
            )
        )
    return merged


def _inbound_file_values(item: InboundBoxItem) -> list[IntakeFile]:
    if item.files is not None:
        return [
            IntakeFile(
                reference=file.reference,
                description=file.description,
                barcode=file.barcode,
            )
            for file in item.files
        ]
    return [
        IntakeFile(
            reference=f"LEGACY-BOX-{item.box_number}-FILE-{position}",
            description=description,
            legacy=True,
        )
        for position, description in enumerate(
            split_legacy_contents(item.contents), start=1
        )
    ]


@dataclass(frozen=True)
class _PreviewPalletResolution:
    output: InboundCompletionTargetPalletOut
    pallet: Pallet | None
    blocked_code: str | None
    blocked_message: str | None
    signature: dict[str, object]


def _preview_pallet_resolution(
    *,
    item: InboundBoxItem,
    lot: Lot | None,
    existing_box: Box | None,
    pallets_by_id: dict[int, Pallet],
    pallets_by_identity: dict[tuple[int, str], Pallet],
) -> _PreviewPalletResolution:
    if item.pallet_number is None:
        current_pallet_id = (
            existing_box.pallet_id if existing_box is not None else None
        )
        current_pallet = (
            pallets_by_id.get(current_pallet_id)
            if current_pallet_id is not None
            else None
        )
        blocked_code: str | None = None
        blocked_message: str | None = None
        if current_pallet_id is None:
            resolution = "unassigned"
        elif current_pallet is None:
            resolution = "blocked"
            blocked_code = "current_pallet_not_found"
            blocked_message = "current pallet assignment does not exist"
        elif not current_pallet.is_active:
            resolution = "blocked"
            blocked_code = "current_pallet_archived"
            blocked_message = "current pallet assignment is archived"
        elif lot is None or current_pallet.lot_id != lot.id:
            resolution = "blocked"
            blocked_code = "current_pallet_lot_mismatch"
            blocked_message = "current pallet assignment belongs to a different lot"
        else:
            resolution = "preserve_existing"
        return _PreviewPalletResolution(
            output=InboundCompletionTargetPalletOut(
                resolution=resolution,
                pallet_id=current_pallet_id,
                pallet_number=(
                    current_pallet.pallet_number
                    if current_pallet is not None
                    else None
                ),
            ),
            pallet=current_pallet,
            blocked_code=blocked_code,
            blocked_message=blocked_message,
            signature={
                "resolution": resolution,
                "exists": current_pallet is not None,
                "id": current_pallet_id,
                "lot_id": (
                    current_pallet.lot_id if current_pallet is not None else None
                ),
                "number": (
                    current_pallet.pallet_number
                    if current_pallet is not None
                    else None
                ),
                "normalized_number": (
                    current_pallet.normalized_pallet_number
                    if current_pallet is not None
                    else None
                ),
                "version": (
                    current_pallet.version if current_pallet is not None else None
                ),
                "is_active": (
                    current_pallet.is_active if current_pallet is not None else None
                ),
                "archived_at": (
                    current_pallet.archived_at.isoformat()
                    if current_pallet is not None
                    and current_pallet.archived_at is not None
                    else None
                ),
            },
        )

    normalized_number = normalize_pallet_number(item.pallet_number)
    pallet: Pallet | None = None
    blocked_code: str | None = None
    blocked_message: str | None = None

    if item.pallet_id is not None:
        pallet = pallets_by_id.get(item.pallet_id)
        if pallet is None:
            blocked_code = "target_pallet_not_found"
            blocked_message = f"pallet {item.pallet_id} not found"
        elif lot is None or pallet.lot_id != lot.id:
            blocked_code = "target_pallet_lot_mismatch"
            blocked_message = "pallet does not belong to the receipt lot"
        elif not pallet.is_active:
            blocked_code = "target_pallet_archived"
            blocked_message = "archived pallets cannot receive boxes"
        elif pallet.normalized_pallet_number != normalized_number:
            blocked_code = "target_pallet_identity_mismatch"
            blocked_message = "pallet_id and pallet_number identify different pallets"
    elif lot is not None:
        pallet = pallets_by_identity.get((lot.id, normalized_number))
        if pallet is not None:
            if not pallet.is_active:
                blocked_code = "target_pallet_archived"
                blocked_message = f"pallet {pallet.pallet_number!r} is archived in this lot"

    if blocked_code is not None:
        resolution = "blocked"
    elif pallet is None:
        resolution = "will_create"
    else:
        resolution = "existing"
    output = InboundCompletionTargetPalletOut(
        resolution=resolution,
        pallet_id=pallet.id if pallet is not None else None,
        pallet_number=pallet.pallet_number if pallet is not None else item.pallet_number,
    )
    return _PreviewPalletResolution(
        output=output,
        pallet=pallet,
        blocked_code=blocked_code,
        blocked_message=blocked_message,
        signature={
            "resolution": resolution,
            "exists": pallet is not None,
            "id": pallet.id if pallet is not None else None,
            "lot_id": pallet.lot_id if pallet is not None else None,
            "number": (
                pallet.pallet_number if pallet is not None else normalized_number
            ),
            "normalized_number": (
                pallet.normalized_pallet_number
                if pallet is not None
                else normalized_number
            ),
            "version": pallet.version if pallet is not None else None,
            "is_active": pallet.is_active if pallet is not None else None,
            "archived_at": (
                pallet.archived_at.isoformat()
                if pallet is not None and pallet.archived_at is not None
                else None
            ),
        },
    )


@dataclass(frozen=True)
class _InboundCompletionImpact:
    preview: InboundCompletionPreviewOut
    items: list[InboundBoxItem]


def _classify_inbound_completion(
    db: Session,
    *,
    request_id: int,
    user: User,
    inbound_items: list[InboundBoxItem] | None = None,
    lock_for_update: bool,
) -> _InboundCompletionImpact:
    """Classify one canonical inbound mapping, optionally locking its impact."""
    request = (
        _locked_request(db, request_id)
        if lock_for_update
        else db.get(BoxRequest, request_id)
    )
    if request is None or not can_access(user, request.warehouse_id):
        raise RequestAccessError("request not found")
    if request.direction != BoxRequestDirection.inbound:
        raise RequestConflictError("request is not an inbound order")
    if request.status != BoxRequestStatus.awaiting_confirmation:
        raise RequestConflictError(
            "only requests awaiting confirmation can be previewed"
        )
    if user.role != UserRole.admin and request.requester_user_id != user.id:
        raise RequestAccessError("only the original requester can accept delivery")
    if db.scalar(
        select(BoxRequestException.id).where(
            BoxRequestException.request_id == request.id,
            BoxRequestException.resolved_at.is_(None),
        )
    ) is not None:
        raise RequestConflictError(
            "resolve the active operational exception before changing lifecycle state"
        )

    target_warehouse_stmt = select(Warehouse).where(
        Warehouse.id == request.warehouse_id
    )
    if lock_for_update:
        target_warehouse_stmt = target_warehouse_stmt.with_for_update(of=Warehouse)
    target_warehouse = db.scalar(target_warehouse_stmt)
    if target_warehouse is None or not target_warehouse.is_active:
        raise RequestConflictError("target warehouse is archived")

    supplied_items = list(inbound_items or [])
    merged_rows = sorted(
        merge_inbound_items(supplied_items),
        key=lambda item: (
            normalize_lot_name(item.lot),
            item.box_number,
        ),
    )
    normalized_lot_names = {normalize_lot_name(item.lot) for item in merged_rows}
    if lock_for_update and merged_rows:
        try:
            resolve_lot_names_for_use(
                db,
                user=user,
                names=[item.lot for item in merged_rows],
                warehouse_id=target_warehouse.id,
            )
        except LotRuleError as exc:
            raise RequestConflictError(str(exc)) from exc
    lots_by_name: dict[str, Lot] = {}
    if normalized_lot_names:
        lot_stmt = (
            select(Lot)
            .where(
                Lot.normalized_name.in_(sorted(normalized_lot_names)),
                Lot.merged_into_lot_id.is_(None),
            )
            .order_by(Lot.id)
        )
        if lock_for_update:
            lot_stmt = lot_stmt.with_for_update(of=Lot)
        lots_by_name = {
            lot.normalized_name: lot
            for lot in db.scalars(lot_stmt).all()
            if lot.normalized_name is not None
        }

    lot_ids = {lot.id for lot in lots_by_name.values()}
    box_numbers = {item.box_number for item in merged_rows}
    existing_boxes: list[Box] = []
    if lot_ids and box_numbers:
        box_stmt = (
            select(Box)
            .where(
                Box.lot_id.in_(sorted(lot_ids)),
                Box.box_number.in_(sorted(box_numbers)),
            )
            .order_by(Box.id)
        )
        existing_boxes = list(db.scalars(box_stmt).all())
    boxes_by_identity = {
        (box.lot_id, box.box_number): box for box in existing_boxes
    }

    mapped_pallet_ids = {
        item.pallet_id for item in merged_rows if item.pallet_id is not None
    }
    current_pallet_ids = {
        box.pallet_id for box in existing_boxes if box.pallet_id is not None
    }
    pallets_by_id = (
        {
            pallet.id: pallet
            for pallet in db.scalars(
                select(Pallet).where(
                    Pallet.id.in_(sorted(mapped_pallet_ids | current_pallet_ids))
                )
            ).all()
        }
        if mapped_pallet_ids or current_pallet_ids
        else {}
    )
    normalized_pallet_numbers = {
        normalize_pallet_number(item.pallet_number)
        for item in merged_rows
        if item.pallet_number is not None
    }
    identity_pallets = (
        db.scalars(
            select(Pallet).where(
                Pallet.lot_id.in_(sorted(lot_ids)),
                Pallet.normalized_pallet_number.in_(
                    sorted(normalized_pallet_numbers)
                ),
            )
        ).all()
        if lot_ids and normalized_pallet_numbers
        else []
    )
    pallets_by_identity = {
        (pallet.lot_id, pallet.normalized_pallet_number): pallet
        for pallet in identity_pallets
    }
    for pallet in identity_pallets:
        pallets_by_id.setdefault(pallet.id, pallet)
    if lock_for_update and pallets_by_id:
        locked_pallets = list(
            db.scalars(
                select(Pallet)
                .where(Pallet.id.in_(sorted(pallets_by_id)))
                .order_by(Pallet.id)
                .with_for_update(of=Pallet)
            ).all()
        )
        pallets_by_id = {pallet.id: pallet for pallet in locked_pallets}
        pallets_by_identity = {
            (pallet.lot_id, pallet.normalized_pallet_number): pallet
            for pallet in locked_pallets
        }
    if lock_for_update and existing_boxes:
        preliminary_pallet_ids = current_pallet_ids
        existing_boxes = list(
            db.scalars(
                select(Box)
                .where(Box.id.in_(sorted(box.id for box in existing_boxes)))
                .order_by(Box.id)
                .with_for_update(of=Box)
            ).all()
        )
        if any(
            box.pallet_id is not None
            and box.pallet_id not in preliminary_pallet_ids
            and box.pallet_id not in mapped_pallet_ids
            for box in existing_boxes
        ):
            raise RequestConflictError(
                "inbound inventory changed while locks were acquired; retry"
            )
        boxes_by_identity = {
            (box.lot_id, box.box_number): box for box in existing_boxes
        }

    incoming_values_by_identity = {
        (normalize_lot_name(item.lot), item.box_number): _inbound_file_values(item)
        for item in merged_rows
    }
    incoming_references = {
        normalize_box_file_reference(value.reference)
        for values in incoming_values_by_identity.values()
        for value in values
    }
    existing_box_ids = {box.id for box in existing_boxes}
    file_filter = BoxFile.box_id.in_(sorted(existing_box_ids))
    if incoming_references:
        file_filter = file_filter | (
            (BoxFile.lot_id.in_(sorted(lot_ids)))
            & BoxFile.normalized_reference.in_(sorted(incoming_references))
        )
    file_stmt = (
        select(BoxFile)
        .where(file_filter)
        .order_by(BoxFile.id)
    )
    if lock_for_update:
        file_stmt = file_stmt.with_for_update(of=BoxFile)
    relevant_files = list(db.scalars(file_stmt).all()) if lot_ids else []
    files_by_lot_reference = {
        (file.lot_id, file.normalized_reference): file for file in relevant_files
    }
    active_files_by_box_id: dict[int, list[BoxFile]] = {}
    for file in relevant_files:
        if file.archived_at is None:
            active_files_by_box_id.setdefault(file.box_id, []).append(file)
    file_source_box_ids = {
        file.box_id for file in relevant_files if file.box_id not in existing_box_ids
    }
    file_source_boxes: dict[int, Box] = {}
    if file_source_box_ids:
        source_box_stmt = (
            select(Box)
            .where(Box.id.in_(sorted(file_source_box_ids)))
            .order_by(Box.id)
        )
        if lock_for_update:
            source_box_stmt = source_box_stmt.with_for_update(of=Box)
        file_source_boxes = {
            source.id: source for source in db.scalars(source_box_stmt).all()
        }
    file_boxes_by_id = {
        **{existing.id: existing for existing in existing_boxes},
        **file_source_boxes,
    }

    box_ids = {box.id for box in existing_boxes}
    reservation_box_ids = box_ids | file_source_box_ids
    reservation_ids_by_box_id: dict[int, list[int]] = {
        box_id: [] for box_id in reservation_box_ids
    }
    if reservation_box_ids:
        return_reference_rows = db.execute(
            select(BoxRequestItem.box_id, BoxRequest.id, BoxRequest.status)
            .join(BoxRequest, BoxRequest.id == BoxRequestItem.request_id)
            .where(
                BoxRequestItem.box_id.in_(sorted(reservation_box_ids)),
                BoxRequest.direction == BoxRequestDirection.return_,
            )
            .distinct()
            .order_by(BoxRequestItem.box_id, BoxRequest.id)
        ).all()
        for box_id, active_request_id, request_status in return_reference_rows:
            if request_status not in ACTIVE_REQUEST_STATUSES:
                continue
            if box_id is not None:
                reservation_ids_by_box_id[box_id].append(active_request_id)

    source_warehouse_ids = {
        box.current_warehouse_id
        for box in (*existing_boxes, *file_source_boxes.values())
    }
    warehouses_by_id = {
        warehouse.id: warehouse
        for warehouse in db.scalars(
            select(Warehouse).where(
                Warehouse.id.in_(
                    sorted(source_warehouse_ids | {target_warehouse.id})
                )
            )
        ).all()
    }

    canonical_contents: dict[tuple[str, str], list[str]] = {}
    for item in supplied_items:
        identity = (normalize_lot_name(item.lot), item.box_number)
        value = (item.contents or "").strip()
        if value:
            canonical_contents.setdefault(identity, []).append(value)

    rows: list[InboundCompletionPreviewRow] = []
    signature_rows: list[dict[str, object]] = []
    source_counts: dict[int, int] = {}
    for item in merged_rows:
        normalized_lot = normalize_lot_name(item.lot)
        lot = lots_by_name.get(normalized_lot)
        box = (
            boxes_by_identity.get((lot.id, item.box_number))
            if lot is not None
            else None
        )
        reservations = (
            reservation_ids_by_box_id.get(box.id, []) if box is not None else []
        )
        target_pallet = _preview_pallet_resolution(
            item=item,
            lot=lot,
            existing_box=box,
            pallets_by_id=pallets_by_id,
            pallets_by_identity=pallets_by_identity,
        )
        file_impacts: list[InboundCompletionFileImpact] = []
        supplied_file_references: set[str] = set()
        for value in incoming_values_by_identity[
            (normalized_lot, item.box_number)
        ]:
            normalized_reference = normalize_box_file_reference(value.reference)
            supplied_file_references.add(normalized_reference)
            existing_file = (
                files_by_lot_reference.get((lot.id, normalized_reference))
                if lot is not None
                else None
            )
            source_box = (
                file_boxes_by_id.get(existing_file.box_id)
                if existing_file is not None
                else None
            )
            if existing_file is None:
                action = "create"
                file_blocked_code = None
                file_blocked_message = None
            elif existing_file.archived_at is not None:
                action = "blocked"
                file_blocked_code = "archived_file_reference"
                file_blocked_message = (
                    f"file reference {value.reference!r} is archived in this lot"
                )
            elif box is not None and existing_file.box_id == box.id:
                changed = (
                    existing_file.reference != value.reference
                    or existing_file.description != value.description
                    or existing_file.barcode != value.barcode
                )
                action = "update" if changed else "preserve"
                file_blocked_code = None
                file_blocked_message = None
            elif source_box is None or source_box.archived_at is not None:
                action = "blocked"
                file_blocked_code = "file_source_box_unavailable"
                file_blocked_message = (
                    f"source box for file {value.reference!r} is unavailable"
                )
            elif reservation_ids_by_box_id.get(existing_file.box_id):
                action = "blocked"
                file_blocked_code = "file_source_box_reserved"
                rendered = ", ".join(
                    f"#{reservation_id}"
                    for reservation_id in reservation_ids_by_box_id[
                        existing_file.box_id
                    ]
                )
                file_blocked_message = (
                    f"source box for file {value.reference!r} is reserved by "
                    f"active return request(s) {rendered}"
                )
            elif source_box.lot_id != (lot.id if lot is not None else None):
                action = "blocked"
                file_blocked_code = "file_lot_mismatch"
                file_blocked_message = (
                    f"file reference {value.reference!r} belongs to another lot"
                )
            else:
                action = "move"
                file_blocked_code = None
                file_blocked_message = None
            file_impacts.append(
                InboundCompletionFileImpact(
                    action=action,
                    reference=value.reference,
                    description=value.description,
                    barcode=value.barcode,
                    file_id=existing_file.id if existing_file is not None else None,
                    file_version=(
                        existing_file.version if existing_file is not None else None
                    ),
                    source_box_id=(
                        existing_file.box_id if existing_file is not None else None
                    ),
                    source_box_number=(
                        source_box.box_number if source_box is not None else None
                    ),
                    source_warehouse_id=(
                        source_box.current_warehouse_id
                        if source_box is not None
                        else None
                    ),
                    source_warehouse_name=(
                        warehouses_by_id[source_box.current_warehouse_id].name
                        if source_box is not None
                        and source_box.current_warehouse_id in warehouses_by_id
                        else None
                    ),
                    source_status=source_box.status if source_box is not None else None,
                    target_box_id=box.id if box is not None else None,
                    blocked_code=file_blocked_code,
                    blocked_message=file_blocked_message,
                )
            )
        if box is not None:
            for existing_file in active_files_by_box_id.get(box.id, []):
                if existing_file.normalized_reference in supplied_file_references:
                    continue
                file_impacts.append(
                    InboundCompletionFileImpact(
                        action="preserve",
                        reference=existing_file.reference,
                        description=existing_file.description,
                        barcode=existing_file.barcode,
                        file_id=existing_file.id,
                        file_version=existing_file.version,
                        source_box_id=box.id,
                        source_box_number=box.box_number,
                        source_warehouse_id=box.current_warehouse_id,
                        source_warehouse_name=(
                            warehouses_by_id[box.current_warehouse_id].name
                            if box.current_warehouse_id in warehouses_by_id
                            else None
                        ),
                        source_status=box.status,
                        target_box_id=box.id,
                    )
                )

        blocked_code: str | None = None
        blocked_message: str | None = None
        if box is None:
            classification = "create"
        elif box.archived_at is not None:
            classification = "blocked"
            blocked_code = "archived_identity"
            blocked_message = (
                f"box {box.box_number!r} in lot {item.lot!r} is archived"
            )
        elif box.current_warehouse_id == target_warehouse.id:
            classification = "blocked"
            blocked_code = "existing_at_target"
            blocked_message = (
                f"box {box.box_number!r} already exists in the target warehouse"
            )
        elif box.status != BoxStatus.received:
            classification = "blocked"
            blocked_code = "invalid_status"
            blocked_message = (
                f"box {box.box_number!r} has status {box.status.value!r}; "
                "only received boxes can be relocated"
            )
        elif reservations:
            classification = "blocked"
            blocked_code = "active_return_reservation"
            rendered = ", ".join(f"#{reservation_id}" for reservation_id in reservations)
            blocked_message = (
                f"box {box.box_number!r} is reserved by active return "
                f"request(s) {rendered}"
            )
        else:
            classification = "relocate"

        if target_pallet.blocked_code is not None and classification != "blocked":
            classification = "blocked"
            blocked_code = target_pallet.blocked_code
            blocked_message = target_pallet.blocked_message
        blocked_file = next(
            (impact for impact in file_impacts if impact.action == "blocked"),
            None,
        )
        if blocked_file is not None and classification != "blocked":
            classification = "blocked"
            blocked_code = blocked_file.blocked_code
            blocked_message = blocked_file.blocked_message

        source_warehouse = (
            warehouses_by_id.get(box.current_warehouse_id)
            if box is not None
            else None
        )
        current_pallet = (
            pallets_by_id.get(box.pallet_id)
            if box is not None and box.pallet_id is not None
            else None
        )
        row = InboundCompletionPreviewRow(
            classification=classification,
            lot=item.lot,
            box_number=item.box_number,
            normalized_lot=normalized_lot,
            normalized_box_number=item.box_number,
            mapped_pallet_number=item.pallet_number,
            mapped_pallet_id=item.pallet_id,
            existing_box_id=box.id if box is not None else None,
            current_status=box.status if box is not None else None,
            source_warehouse_id=(
                box.current_warehouse_id if box is not None else None
            ),
            source_warehouse_name=(
                source_warehouse.name if source_warehouse is not None else None
            ),
            current_pallet_id=box.pallet_id if box is not None else None,
            current_pallet_number=(
                current_pallet.pallet_number if current_pallet is not None else None
            ),
            target_warehouse_id=target_warehouse.id,
            target_warehouse_name=target_warehouse.name,
            target_pallet_resolution=target_pallet.output,
            active_return_reservation_ids=reservations,
            file_impacts=file_impacts,
            blocked_code=blocked_code,
            blocked_message=blocked_message,
        )
        rows.append(row)
        if classification == "relocate" and box is not None:
            source_counts[box.current_warehouse_id] = (
                source_counts.get(box.current_warehouse_id, 0) + 1
            )
        signature_rows.append(
            {
                "normalized_lot": normalized_lot,
                "normalized_box_number": item.box_number,
                "normalized_pallet_number": (
                    normalize_pallet_number(item.pallet_number)
                    if item.pallet_number is not None
                    else None
                ),
                "mapped_pallet_id": item.pallet_id,
                "contents": sorted(
                    set(
                        canonical_contents.get(
                            (normalized_lot, item.box_number),
                            [],
                        )
                    )
                ),
                "existing_box": (
                    {
                        "id": box.id,
                        "status": box.status.value,
                        "archived_at": (
                            box.archived_at.isoformat()
                            if box.archived_at is not None
                            else None
                        ),
                        "current_warehouse_id": box.current_warehouse_id,
                        "current_pallet_id": box.pallet_id,
                        "current_pallet_number": (
                            current_pallet.pallet_number
                            if current_pallet is not None
                            else None
                        ),
                        "current_pallet": (
                            {
                                "id": current_pallet.id,
                                "lot_id": current_pallet.lot_id,
                                "number": current_pallet.pallet_number,
                                "normalized_number": (
                                    current_pallet.normalized_pallet_number
                                ),
                                "version": current_pallet.version,
                                "is_active": current_pallet.is_active,
                                "archived_at": (
                                    current_pallet.archived_at.isoformat()
                                    if current_pallet.archived_at is not None
                                    else None
                                ),
                            }
                            if current_pallet is not None
                            else None
                        ),
                    }
                    if box is not None
                    else None
                ),
                "active_return_reservation_ids": reservations,
                "target_pallet": target_pallet.signature,
                "files": [
                    impact.model_dump(mode="json") for impact in file_impacts
                ],
            }
        )

    created = sum(row.classification == "create" for row in rows)
    relocated = sum(row.classification == "relocate" for row in rows)
    blocked = sum(row.classification == "blocked" for row in rows)
    all_file_impacts = [
        impact for row in rows for impact in row.file_impacts
    ]
    source_warehouse_counts = [
        InboundCompletionSourceWarehouseCount(
            warehouse_id=warehouse_id,
            warehouse_name=warehouses_by_id[warehouse_id].name,
            count=count,
        )
        for warehouse_id, count in sorted(source_counts.items())
    ]
    signature_payload = {
        "request_id": request.id,
        "request_version": request.version,
        "target_warehouse": {
            "id": target_warehouse.id,
            "name": target_warehouse.name,
            "is_active": target_warehouse.is_active,
        },
        "rows": signature_rows,
    }
    impact_signature = hashlib.sha256(
        json.dumps(
            signature_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return _InboundCompletionImpact(
        preview=InboundCompletionPreviewOut(
            request_id=request.id,
            request_version=request.version,
            target_warehouse_id=target_warehouse.id,
            target_warehouse_name=target_warehouse.name,
            impact_signature=impact_signature,
            can_complete=blocked == 0,
            summary=InboundCompletionPreviewSummary(
                created=created,
                relocated=relocated,
                blocked=blocked,
                files_created=sum(
                    impact.action == "create" for impact in all_file_impacts
                ),
                files_updated=sum(
                    impact.action == "update" for impact in all_file_impacts
                ),
                files_moved=sum(
                    impact.action == "move" for impact in all_file_impacts
                ),
                files_preserved=sum(
                    impact.action == "preserve" for impact in all_file_impacts
                ),
                files_blocked=sum(
                    impact.action == "blocked" for impact in all_file_impacts
                ),
                source_warehouse_counts=source_warehouse_counts,
            ),
            rows=rows,
        ),
        items=merged_rows,
    )


def preview_inbound_completion(
    db: Session,
    *,
    request_id: int,
    user: User,
    inbound_items: list[InboundBoxItem] | None = None,
) -> InboundCompletionPreviewOut:
    """Classify an inbound mapping without changing persistent state."""
    with db.no_autoflush:
        return _classify_inbound_completion(
            db,
            request_id=request_id,
            user=user,
            inbound_items=inbound_items,
            lock_for_update=False,
        ).preview


def complete_request(
    db: Session,
    *,
    request_id: int,
    user: User,
    inbound_items: list[InboundBoxItem] | None = None,
    accept_existing_received_boxes: bool = False,
    accept_file_moves: bool = False,
    inbound_impact_signature: str | None = None,
    collected_box_ids: list[int] | None = None,
    discrepancies: list[RequestDiscrepancyInput] | None = None,
    discrepancy_reason: str | None = None,
    expected_version: int,
    idempotency_key: str,
    affected_warehouse_ids: set[int] | None = None,
    affected_pallet_warehouse_ids: dict[int, set[int]] | None = None,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    if not can_access(user, request.warehouse_id):
        raise RequestAccessError("request not found")
    if (
        request.status == BoxRequestStatus.completed
        and request.completion_idempotency_key == idempotency_key
    ):
        if request.direction == BoxRequestDirection.inbound:
            if user.role != UserRole.admin and request.requester_user_id != user.id:
                raise RequestAccessError("only the original requester can accept delivery")
        else:
            _require_mover(user)
        if affected_warehouse_ids is not None:
            affected_warehouse_ids.add(request.warehouse_id)
            if request.target_warehouse_id is not None:
                affected_warehouse_ids.add(request.target_warehouse_id)
            completed_event = db.scalar(
                select(BoxRequestEvent)
                .where(
                    BoxRequestEvent.request_id == request.id,
                    BoxRequestEvent.event_type.in_(
                        (
                            BoxRequestEventType.completed,
                            BoxRequestEventType.partial_completion,
                        )
                    ),
                )
                .order_by(BoxRequestEvent.id.desc())
            )
            metadata = completed_event.event_metadata if completed_event else {}
            for source_id in (metadata or {}).get("source_warehouse_ids", []):
                if isinstance(source_id, int):
                    affected_warehouse_ids.add(source_id)
            if affected_pallet_warehouse_ids is not None:
                stored_pallet_warehouses = (metadata or {}).get(
                    "affected_pallet_warehouse_ids", {}
                )
                if isinstance(stored_pallet_warehouses, dict):
                    for raw_pallet_id, raw_warehouse_ids in (
                        stored_pallet_warehouses.items()
                    ):
                        try:
                            pallet_id = int(raw_pallet_id)
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(raw_warehouse_ids, list):
                            continue
                        affected_pallet_warehouse_ids.setdefault(
                            pallet_id, set()
                        ).update(
                            warehouse_id
                            for warehouse_id in raw_warehouse_ids
                            if isinstance(warehouse_id, int)
                        )
        return request
    _check_common(request, user=user, expected_version=expected_version)
    _require_no_open_exception(db, request)
    if request.status != BoxRequestStatus.awaiting_confirmation:
        raise RequestConflictError(
            "only requests awaiting confirmation can be completed"
        )

    supplied_discrepancies = list(discrepancies or [])
    cleaned_reason = (discrepancy_reason or "").strip()
    completion_event = BoxRequestEventType.completed
    created_box_ids: list[int] = []
    relocated_box_ids: list[int] = []
    relocation_source_counts: dict[int, int] = {}
    file_impact_counts: dict[str, int] = {}
    if affected_warehouse_ids is not None:
        affected_warehouse_ids.add(request.warehouse_id)
        if request.target_warehouse_id is not None:
            affected_warehouse_ids.add(request.target_warehouse_id)
    if request.direction == BoxRequestDirection.inbound:
        if user.role != UserRole.admin and request.requester_user_id != user.id:
            raise RequestAccessError("only the original requester can accept delivery")
        rows = merge_inbound_items(inbound_items or [])
        actual_quantity = len(rows)
        variance = actual_quantity - request.quantity
        expected_type = (
            BoxRequestDiscrepancyType.missing
            if variance < 0
            else BoxRequestDiscrepancyType.unexpected
        )
        recorded_variance = sum(
            entry.quantity or 1
            for entry in supplied_discrepancies
            if entry.discrepancy_type == expected_type
        )
        if variance != 0 and recorded_variance < abs(variance) and not cleaned_reason:
            raise RequestRuleError(
                f"a {expected_type.value} discrepancy covering {abs(variance)} "
                "box(es), or discrepancy_reason, is required"
            )
        if variance != 0 and recorded_variance < abs(variance):
            supplied_discrepancies.append(
                RequestDiscrepancyInput(
                    discrepancy_type=expected_type,
                    quantity=abs(variance) - recorded_variance,
                    notes=cleaned_reason,
                )
            )
        request.actual_received_quantity = actual_quantity
        request.variance_quantity = variance
        request.discrepancy_reason = cleaned_reason or _compatibility_reason(
            supplied_discrepancies
        )
        if request.items:
            raise RequestConflictError("inbound items have already been recorded")
        try:
            impact = _classify_inbound_completion(
                db,
                request_id=request.id,
                user=user,
                inbound_items=inbound_items,
                lock_for_update=True,
            )
        except RequestRuleError:
            db.rollback()
            raise
        current_signature = impact.preview.impact_signature
        file_impact_counts = {
            "created": impact.preview.summary.files_created,
            "updated": impact.preview.summary.files_updated,
            "moved": impact.preview.summary.files_moved,
            "preserved": impact.preview.summary.files_preserved,
            "blocked": impact.preview.summary.files_blocked,
        }
        if (
            inbound_impact_signature is not None
            and inbound_impact_signature != current_signature
        ):
            db.rollback()
            raise RequestConflictError(
                "inbound impact changed; refresh the preview and retry"
            )
        if impact.preview.summary.blocked:
            first_blocked = next(
                row for row in impact.preview.rows if row.classification == "blocked"
            )
            db.rollback()
            raise RequestConflictError(
                f"inbound completion is blocked ({first_blocked.blocked_code}): "
                f"{first_blocked.blocked_message}"
            )
        if affected_warehouse_ids is not None:
            affected_warehouse_ids.update(
                entry.warehouse_id
                for entry in impact.preview.summary.source_warehouse_counts
            )
        if impact.preview.summary.relocated:
            if not accept_existing_received_boxes:
                db.rollback()
                raise RequestConflictError(
                    "explicit acceptance is required to relocate existing received boxes"
                )
            if inbound_impact_signature is None:
                db.rollback()
                raise RequestConflictError(
                    "inbound_impact_signature is required for existing box relocation"
                )
        if impact.preview.summary.files_moved:
            if not accept_file_moves:
                db.rollback()
                raise RequestConflictError(
                    "explicit accept_file_moves acknowledgement is required"
                )
            if inbound_impact_signature is None:
                db.rollback()
                raise RequestConflictError(
                    "inbound_impact_signature is required for file moves"
                )
        rows = impact.items
        try:
            lots_by_name = resolve_lot_names_for_use(
                db,
                user=user,
                names=[item.lot for item in rows],
                warehouse_id=request.warehouse_id,
            )
            for position, (item, preview_row) in enumerate(
                zip(rows, impact.preview.rows, strict=True),
                start=1,
            ):
                lot_record = lots_by_name[normalize_lot_name(item.lot)]
                if item.pallet_id is not None and item.pallet_number is None:
                    raise RequestConflictError("pallet_id requires pallet_number")
                target_pallet: Pallet | None = None
                if item.pallet_number is not None:
                    target_pallet = resolve_or_create_active_pallet(
                        db,
                        user=user,
                        lot_id=lot_record.id,
                        warehouse_id=request.warehouse_id,
                        pallet_number=item.pallet_number,
                        pallet_id=item.pallet_id,
                        receipt_creation_authorized=True,
                    )
                if preview_row.classification == "create":
                    box = create_box(
                        db,
                        user=user,
                        box_number=item.box_number,
                        lot_id=lot_record.id,
                        pallet_number=(
                            target_pallet.pallet_number
                            if target_pallet is not None
                            else None
                        ),
                        pallet_id=(
                            target_pallet.id if target_pallet is not None else None
                        ),
                        contents=item.contents,
                        # Reconcile below so accepted same-Lot references can
                        # move into a newly created target Box as previewed.
                        files=[],
                        warehouse_id=request.warehouse_id,
                        note=f"Received through request #{request.id}",
                        receipt_authorized_pallet_creation=True,
                        commit=False,
                    )
                    reconcile_intake_box_files(
                        db,
                        user=user,
                        box=box,
                        files=item.files,
                        contents=item.contents,
                        allow_moves=True,
                        reason=f"Reconciled through inbound request #{request.id}",
                    )
                    created_box_ids.append(box.id)
                    if (
                        affected_pallet_warehouse_ids is not None
                        and target_pallet is not None
                    ):
                        affected_pallet_warehouse_ids.setdefault(
                            target_pallet.id, set()
                        ).add(request.warehouse_id)
                else:
                    if preview_row.existing_box_id is None:
                        raise RequestConflictError(
                            "relocation preview lost its existing box identity"
                        )
                    box = db.get(Box, preview_row.existing_box_id)
                    if box is None:
                        raise RequestConflictError(
                            "relocation preview box no longer exists"
                        )
                    source_warehouse_id = box.current_warehouse_id
                    source_pallet_id = box.pallet_id
                    _move_received_box_for_inbound_request(
                        db,
                        user=user,
                        box=box,
                        request_id=request.id,
                        target_warehouse_id=request.warehouse_id,
                        target_pallet=target_pallet,
                        mapped_contents=item.contents,
                        commit=False,
                    )
                    reconcile_intake_box_files(
                        db,
                        user=user,
                        box=box,
                        files=item.files,
                        contents=item.contents,
                        allow_moves=True,
                        reason=f"Reconciled through inbound request #{request.id}",
                    )
                    relocated_box_ids.append(box.id)
                    relocation_source_counts[source_warehouse_id] = (
                        relocation_source_counts.get(source_warehouse_id, 0) + 1
                    )
                    if affected_warehouse_ids is not None:
                        affected_warehouse_ids.add(source_warehouse_id)
                    if affected_pallet_warehouse_ids is not None:
                        if source_pallet_id is not None:
                            affected_pallet_warehouse_ids.setdefault(
                                source_pallet_id, set()
                            ).add(source_warehouse_id)
                        effective_pallet_id = (
                            target_pallet.id
                            if target_pallet is not None
                            else source_pallet_id
                        )
                        if effective_pallet_id is not None:
                            affected_pallet_warehouse_ids.setdefault(
                                effective_pallet_id, set()
                            ).add(request.warehouse_id)
                request_item = BoxRequestItem(
                    request_id=request.id,
                    position=position,
                    box_id=box.id,
                    lot_id=box.lot_id,
                    lot=box.lot,
                    pallet_id=box.pallet_id,
                    pallet=_pallet_number_for_box(db, box),
                    box_number=box.box_number,
                    contents=box.contents,
                )
                db.add(request_item)
                db.flush()
                snapshot_request_item_files(
                    db,
                    item=request_item,
                    files=_active_files_by_box_ids(
                        db, [box.id], lock=True
                    )[box.id],
                )
        except (RequestConflictError, BoxFileConflictError) as exc:
            db.rollback()
            raise RequestConflictError(str(exc)) from exc
        except (BoxRuleError, LotRuleError, PalletRuleError, IntegrityError) as exc:
            db.rollback()
            raise RequestConflictError(str(exc)) from exc
        db.flush()
        if variance < 0:
            completion_event = BoxRequestEventType.partial_completion
            _create_follow_up(
                db,
                request=request,
                user=user,
                quantity=abs(variance),
                origin=BoxRequestOrigin.backorder,
                status=BoxRequestStatus.submitted,
            )
    else:
        _require_mover(user)
        if request.target_warehouse_id is None:
            raise RequestConflictError("return request has no target warehouse")
        target_warehouse = db.scalar(
            select(Warehouse)
            .where(Warehouse.id == request.target_warehouse_id)
            .with_for_update()
        )
        if target_warehouse is None or not target_warehouse.is_active:
            raise RequestConflictError("target warehouse is archived")
        if len(request.items) != request.quantity:
            raise RequestConflictError("return allocation is incomplete")
        allocated_by_box_id = {
            item.box_id: item for item in request.items if item.box_id is not None
        }
        selected_ids = (
            list(allocated_by_box_id)
            if collected_box_ids is None
            else collected_box_ids
        )
        if not selected_ids:
            raise RequestRuleError("select at least one collected box")
        if len(selected_ids) != len(set(selected_ids)) or any(
            box_id not in allocated_by_box_id for box_id in selected_ids
        ):
            raise RequestRuleError("collected_box_ids must be a unique subset of the request")
        locked_boxes = db.scalars(
            select(Box)
            .where(Box.id.in_(sorted(selected_ids)))
            .order_by(Box.id)
            .with_for_update(of=Box)
        ).all()
        boxes_by_id = {box.id: box for box in locked_boxes}
        return_boxes: list[Box] = []
        for box_id in selected_ids:
            box = boxes_by_id.get(box_id)
            if (
                box is None
                or box.archived_at is not None
                or box.status != BoxStatus.ready_to_return
                or box.current_warehouse_id != request.warehouse_id
            ):
                raise RequestConflictError(
                    f"box {box_id} is no longer ready to return "
                    "from the request warehouse"
                )
            return_boxes.append(box)
        try:
            for box in return_boxes:
                move_return_box_for_request(
                    db,
                    user=user,
                    box=box,
                    request_id=request.id,
                    source_warehouse_id=request.warehouse_id,
                    target_warehouse_id=target_warehouse.id,
                    commit=False,
                )
        except BoxRuleError as exc:
            db.rollback()
            raise RequestConflictError(str(exc)) from exc
        unresolved = [
            item for item in request.items if item.box_id not in set(selected_ids)
        ]
        request.actual_received_quantity = len(selected_ids)
        request.variance_quantity = len(selected_ids) - request.quantity
        if unresolved:
            completion_event = BoxRequestEventType.partial_completion
            for item in unresolved:
                supplied_discrepancies.append(
                    RequestDiscrepancyInput(
                        discrepancy_type=BoxRequestDiscrepancyType.missing,
                        request_item_id=item.id,
                        box_id=item.box_id,
                        quantity=1,
                        notes=cleaned_reason or "Not collected; released for reselection.",
                    )
                )
            request.discrepancy_reason = cleaned_reason or "Partial return collection"
            _create_follow_up(
                db,
                request=request,
                user=user,
                quantity=len(unresolved),
                origin=BoxRequestOrigin.return_reselection,
                status=BoxRequestStatus.draft,
            )
        else:
            request.discrepancy_reason = cleaned_reason or None

    now = datetime.now(UTC)
    _record_discrepancies(
        db,
        request=request,
        user=user,
        entries=supplied_discrepancies,
        now=now,
    )
    request.completion_idempotency_key = idempotency_key
    request.completed_by_user_id = user.id
    request.completed_at = now
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.completed,
        event_type=completion_event,
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
        metadata={
            "request_id": request.id,
            "source_warehouse_id": (
                next(iter(relocation_source_counts))
                if request.direction == BoxRequestDirection.inbound
                and len(relocation_source_counts) == 1
                else (
                    None
                    if request.direction == BoxRequestDirection.inbound
                    else request.warehouse_id
                )
            ),
            "source_warehouse_ids": (
                sorted(relocation_source_counts)
                if request.direction == BoxRequestDirection.inbound
                else [request.warehouse_id]
            ),
            "target_warehouse_id": (
                request.warehouse_id
                if request.direction == BoxRequestDirection.inbound
                else request.target_warehouse_id
            ),
            "requested_quantity": request.quantity,
            "actual_quantity": request.actual_received_quantity,
            "variance_quantity": request.variance_quantity,
            "discrepancy_types": sorted(
                {entry.discrepancy_type.value for entry in supplied_discrepancies}
            ),
            "created_box_ids": created_box_ids,
            "relocated_box_ids": relocated_box_ids,
            "relocation_source_warehouse_counts": {
                str(warehouse_id): count
                for warehouse_id, count in sorted(relocation_source_counts.items())
            },
            "affected_pallet_warehouse_ids": {
                str(pallet_id): sorted(warehouse_ids)
                for pallet_id, warehouse_ids in sorted(
                    (affected_pallet_warehouse_ids or {}).items()
                )
            },
            "accept_existing_received_boxes": accept_existing_received_boxes,
            "accept_file_moves": accept_file_moves,
            "file_impact_counts": file_impact_counts,
        },
    )
    enqueue_request_event(
        db,
        request=request,
        kind=RequestNotificationKind.confirmation_received,
        event_token=f"completed:{request.version}",
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RequestConflictError(
            "request completion conflicted with a concurrent inventory change"
        ) from exc
    db.refresh(request)
    return request


def _compatibility_reason(entries: list[RequestDiscrepancyInput]) -> str | None:
    notes = [entry.notes.strip() for entry in entries if entry.notes and entry.notes.strip()]
    return "; ".join(dict.fromkeys(notes)) or None


def _record_discrepancies(
    db: Session,
    *,
    request: BoxRequest,
    user: User,
    entries: list[RequestDiscrepancyInput],
    now: datetime,
) -> None:
    request_item_ids = {item.id for item in request.items}
    request_box_ids = {item.box_id for item in request.items if item.box_id is not None}
    for entry in entries:
        if entry.request_item_id is not None and entry.request_item_id not in request_item_ids:
            raise RequestRuleError("discrepancy request_item_id does not belong to the request")
        if entry.box_id is not None and entry.box_id not in request_box_ids:
            raise RequestRuleError("discrepancy box_id does not belong to the request")
        db.add(
            BoxRequestDiscrepancy(
                request_id=request.id,
                request_item_id=entry.request_item_id,
                box_id=entry.box_id,
                discrepancy_type=entry.discrepancy_type,
                quantity=entry.quantity,
                notes=entry.notes.strip() if entry.notes else None,
                created_by_user_id=user.id,
                created_at=now,
            )
        )


def _create_follow_up(
    db: Session,
    *,
    request: BoxRequest,
    user: User,
    quantity: int,
    origin: BoxRequestOrigin,
    status: BoxRequestStatus,
) -> BoxRequest:
    now = datetime.now(UTC)
    follow_up = BoxRequest(
        direction=request.direction,
        warehouse_id=request.warehouse_id,
        target_warehouse_id=request.target_warehouse_id,
        quantity=quantity,
        status=status,
        requester_user_id=request.requester_user_id,
        source_inbound_request_id=request.source_inbound_request_id,
        parent_request_id=request.id,
        root_request_id=request.root_request_id or request.id,
        origin=origin,
        suggestion_quantity=quantity,
        current_available=request.current_available,
        min_inventory=request.min_inventory,
        pending_inbound=request.pending_inbound,
        eligible_return=request.eligible_return,
        recommendation_snapshot={
            **(request.recommendation_snapshot or {}),
            "suggested_quantity": quantity,
            "formula": "generated_follow_up_quantity",
            "explanation": (
                f"Generated from the unresolved quantity of request #{request.id}."
            ),
        },
        submitted_at=now,
        created_at=now,
        updated_at=now,
        version=1,
    )
    db.add(follow_up)
    db.flush()
    db.add(
        _event(
            follow_up,
            event_type=(
                BoxRequestEventType.submitted
                if status == BoxRequestStatus.submitted
                else BoxRequestEventType.draft_created
            ),
            user=user,
            from_status=None,
            to_status=status,
            note=f"Generated from partial completion of request #{request.id}.",
            occurred_at=now,
            metadata={
                "generated_follow_up": True,
                "parent_request_id": request.id,
                "root_request_id": request.root_request_id or request.id,
                "origin": origin.value,
                "quantity": quantity,
                "source_warehouse_id": follow_up.warehouse_id,
                "target_warehouse_id": follow_up.target_warehouse_id,
            },
        )
    )
    return follow_up


def submit_follow_up_draft(
    db: Session,
    *,
    request_id: int,
    user: User,
    box_ids: list[int],
    expected_version: int,
) -> BoxRequest:
    request = _locked_request(db, request_id)
    _check_common(request, user=user, expected_version=expected_version)
    if request.status != BoxRequestStatus.draft:
        raise RequestConflictError("only generated drafts can be submitted")
    if request.origin != BoxRequestOrigin.return_reselection:
        raise RequestRuleError("this draft does not support box reselection")
    if user.role != UserRole.admin and request.requester_user_id != user.id:
        raise RequestAccessError("only the requester can submit this follow-up")
    if len(box_ids) != request.quantity:
        raise RequestRuleError("select exactly the draft quantity")
    if request.source_inbound_request_id is None:
        raise RequestRuleError("return follow-up has no inbound source")
    locked_lots_by_box = _lock_lots_for_box_ids(db, box_ids)
    candidates = get_return_candidates(
        db,
        user=user,
        source_inbound_request_id=request.source_inbound_request_id,
        lock=True,
    )
    candidates_by_id = {box.id: box for box in candidates}
    if any(box_id not in candidates_by_id for box_id in box_ids):
        raise RequestConflictError("one or more selected boxes are no longer available")
    files_by_box_id = _active_files_by_box_ids(db, box_ids, lock=True)
    for position, box_id in enumerate(box_ids, start=1):
        box = candidates_by_id[box_id]
        if locked_lots_by_box[box_id] != box.lot_id:
            raise RequestConflictError("one or more selected box lots changed")
        item = BoxRequestItem(
            request_id=request.id,
            position=position,
            box_id=box.id,
            lot_id=box.lot_id,
            lot=box.lot,
            pallet_id=box.pallet_id,
            pallet=_pallet_number_for_box(db, box),
            box_number=box.box_number,
            contents=box.contents,
        )
        db.add(item)
        db.flush()
        snapshot_request_item_files(
            db, item=item, files=files_by_box_id[box.id]
        )
    request.submitted_at = datetime.now(UTC)
    _transition(
        db,
        request,
        user=user,
        target=BoxRequestStatus.submitted,
        event_type=BoxRequestEventType.submitted,
        note="Follow-up draft submitted with a new box selection.",
    )
    db.commit()
    db.refresh(request)
    return request
