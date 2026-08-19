from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models.alerts import Alert
from app.models.boxes import ACTIVE_STATUSES, Box
from app.models.employees import Employee
from app.models.requests import BLOCKING_REQUEST_STATUSES, BoxRequest
from app.models.users import User
from app.models.warehouses import Warehouse


class WarehouseRuleError(ValueError):
    pass


class WarehouseArchiveConflict(WarehouseRuleError):
    def __init__(
        self,
        *,
        active_boxes: int = 0,
        active_requests: int = 0,
        active_source_requests: int = 0,
        active_target_requests: int = 0,
        last_active_warehouse: bool = False,
    ) -> None:
        self.active_boxes = active_boxes
        self.active_requests = active_requests
        self.active_source_requests = active_source_requests
        self.active_target_requests = active_target_requests
        self.last_active_warehouse = last_active_warehouse
        super().__init__("warehouse cannot be archived until blocking activity is cleared")

    def detail(self) -> dict[str, object]:
        return {
            "message": str(self),
            "active_boxes": self.active_boxes,
            "active_requests": self.active_requests,
            "active_source_requests": self.active_source_requests,
            "active_target_requests": self.active_target_requests,
            "last_active_warehouse": self.last_active_warehouse,
        }


def ensure_warehouse_exists(db: Session, warehouse_id: int) -> Warehouse:
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise WarehouseRuleError(f"unknown warehouse_id: {warehouse_id}")
    return warehouse


def ensure_active_warehouse(db: Session, warehouse_id: int) -> Warehouse:
    warehouse = ensure_warehouse_exists(db, warehouse_id)
    if not warehouse.is_active:
        raise WarehouseRuleError(f"warehouse_id {warehouse_id} is archived")
    return warehouse


def archive_warehouse(
    db: Session,
    *,
    warehouse_id: int,
    user: User,
) -> Warehouse:
    # Lock the complete, normally small warehouse set in a stable order.
    # This serializes concurrent archives so two requests cannot both observe
    # "two active warehouses" and archive the final pair.
    warehouses = db.scalars(
        select(Warehouse).order_by(Warehouse.id).with_for_update()
    ).all()
    warehouse = next((row for row in warehouses if row.id == warehouse_id), None)
    if warehouse is None:
        raise WarehouseRuleError(f"unknown warehouse_id: {warehouse_id}")
    if not warehouse.is_active:
        raise WarehouseRuleError("warehouse is already archived")

    active_count = sum(1 for row in warehouses if row.is_active)
    active_boxes = int(
        db.scalar(
            select(func.count(Box.id)).where(
                Box.current_warehouse_id == warehouse_id,
                Box.archived_at.is_(None),
                Box.status.in_(ACTIVE_STATUSES),
            )
        )
        or 0
    )
    active_source_requests = int(
        db.scalar(
            select(func.count(BoxRequest.id)).where(
                BoxRequest.warehouse_id == warehouse_id,
                BoxRequest.status.in_(BLOCKING_REQUEST_STATUSES),
            )
        )
        or 0
    )
    active_target_requests = int(
        db.scalar(
            select(func.count(BoxRequest.id)).where(
                BoxRequest.target_warehouse_id == warehouse_id,
                BoxRequest.status.in_(BLOCKING_REQUEST_STATUSES),
            )
        )
        or 0
    )
    active_requests = int(
        db.scalar(
            select(func.count(func.distinct(BoxRequest.id))).where(
                or_(
                    BoxRequest.warehouse_id == warehouse_id,
                    BoxRequest.target_warehouse_id == warehouse_id,
                ),
                BoxRequest.status.in_(BLOCKING_REQUEST_STATUSES),
            )
        )
        or 0
    )
    last_active = active_count <= 1
    if active_boxes or active_requests or last_active:
        raise WarehouseArchiveConflict(
            active_boxes=active_boxes,
            active_requests=active_requests,
            active_source_requests=active_source_requests,
            active_target_requests=active_target_requests,
            last_active_warehouse=last_active,
        )

    now = datetime.now(UTC)
    warehouse.is_active = False
    warehouse.archived_at = now
    warehouse.archived_by_user_id = user.id
    db.execute(
        update(Employee)
        .where(
            Employee.warehouse_id == warehouse_id,
            Employee.is_active.is_(True),
        )
        .values(is_active=False)
    )
    db.execute(
        update(Alert)
        .where(
            Alert.warehouse_id == warehouse_id,
            Alert.resolved_at.is_(None),
        )
        .values(resolved_at=now)
    )
    db.commit()
    db.refresh(warehouse)
    return warehouse


def restore_warehouse(db: Session, *, warehouse_id: int) -> Warehouse:
    warehouse = db.scalar(
        select(Warehouse)
        .where(Warehouse.id == warehouse_id)
        .with_for_update()
    )
    if warehouse is None:
        raise WarehouseRuleError(f"unknown warehouse_id: {warehouse_id}")
    if warehouse.is_active:
        return warehouse
    warehouse.is_active = True
    warehouse.archived_at = None
    warehouse.archived_by_user_id = None
    db.commit()
    db.refresh(warehouse)
    return warehouse


__all__ = [
    "WarehouseArchiveConflict",
    "WarehouseRuleError",
    "archive_warehouse",
    "ensure_active_warehouse",
    "ensure_warehouse_exists",
    "restore_warehouse",
]
