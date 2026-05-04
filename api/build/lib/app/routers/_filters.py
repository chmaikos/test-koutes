"""Shared filter parser used by both the boxes list endpoint and exports."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, or_

from app.models.boxes import Box, BoxStatus


class BoxFilters(BaseModel):
    warehouse_id: int | None = None
    status: BoxStatus | None = None
    owner: str | None = None
    search: str | None = None
    received_from: datetime | None = None
    received_to: datetime | None = None
    updated_from: datetime | None = None
    updated_to: datetime | None = None


def parse_box_filters(
    warehouse_id: Annotated[int | None, Query()] = None,
    status: Annotated[BoxStatus | None, Query()] = None,
    owner: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(description="matches box_number or owner")] = None,
    received_from: Annotated[datetime | None, Query()] = None,
    received_to: Annotated[datetime | None, Query()] = None,
    updated_from: Annotated[datetime | None, Query()] = None,
    updated_to: Annotated[datetime | None, Query()] = None,
) -> BoxFilters:
    return BoxFilters(
        warehouse_id=warehouse_id,
        status=status,
        owner=owner,
        search=search,
        received_from=received_from,
        received_to=received_to,
        updated_from=updated_from,
        updated_to=updated_to,
    )


def apply_box_filters(stmt: Select, filters: BoxFilters) -> Select:
    if filters.warehouse_id is not None:
        stmt = stmt.where(Box.current_warehouse_id == filters.warehouse_id)
    if filters.status is not None:
        stmt = stmt.where(Box.status == filters.status)
    if filters.owner:
        stmt = stmt.where(Box.owner.ilike(f"%{filters.owner}%"))
    if filters.search:
        like = f"%{filters.search}%"
        stmt = stmt.where(or_(Box.box_number.ilike(like), Box.owner.ilike(like)))
    if filters.received_from is not None:
        stmt = stmt.where(Box.received_at >= filters.received_from)
    if filters.received_to is not None:
        stmt = stmt.where(Box.received_at <= filters.received_to)
    if filters.updated_from is not None:
        stmt = stmt.where(Box.updated_at >= filters.updated_from)
    if filters.updated_to is not None:
        stmt = stmt.where(Box.updated_at <= filters.updated_to)
    return stmt
