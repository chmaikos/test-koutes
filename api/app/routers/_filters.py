"""Shared filter parser used by both the boxes list endpoint and exports."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Select, and_, or_

from app.models.box_files import BoxFile
from app.models.boxes import Box, BoxStatus
from app.models.pallets import Pallet
from app.models.warehouses import Warehouse


class BoxFilters(BaseModel):
    warehouse_id: int | None = None
    status: BoxStatus | None = None
    lot_id: int | None = None
    lot: str | None = None
    pallet_id: int | None = None
    unassigned_pallet: bool = False
    search: str | None = None
    received_from: datetime | None = None
    received_to: datetime | None = None
    updated_from: datetime | None = None
    updated_to: datetime | None = None


# Allowlist of user-facing sort keys. We translate these to concrete
# SQLAlchemy columns in ``apply_box_sort`` rather than letting the
# router pass column names directly, so unknown / unsafe values are
# always rejected at the edge with a 422.
SORTABLE_FIELDS: frozenset[str] = frozenset(
    {"box_number", "lot", "status", "warehouse", "received_at", "updated_at"}
)


class BoxSort(BaseModel):
    sort_by: str | None = None
    sort_dir: Literal["asc", "desc"] = "desc"


def parse_box_filters(
    warehouse_id: Annotated[int | None, Query()] = None,
    status: Annotated[BoxStatus | None, Query()] = None,
    lot_id: Annotated[int | None, Query(ge=1)] = None,
    lot: Annotated[str | None, Query()] = None,
    pallet_id: Annotated[int | None, Query(ge=1)] = None,
    unassigned_pallet: Annotated[bool, Query()] = False,
    search: Annotated[
        str | None,
        Query(
            description=(
                "matches box, lot, pallet, contents, or an active File "
                "reference/description/barcode"
            )
        ),
    ] = None,
    received_from: Annotated[datetime | None, Query()] = None,
    received_to: Annotated[datetime | None, Query()] = None,
    updated_from: Annotated[datetime | None, Query()] = None,
    updated_to: Annotated[datetime | None, Query()] = None,
) -> BoxFilters:
    return BoxFilters(
        warehouse_id=warehouse_id,
        status=status,
        lot_id=lot_id,
        lot=lot,
        pallet_id=pallet_id,
        unassigned_pallet=unassigned_pallet,
        search=search,
        received_from=received_from,
        received_to=received_to,
        updated_from=updated_from,
        updated_to=updated_to,
    )


def parse_box_sort(
    sort_by: Annotated[str | None, Query()] = None,
    sort_dir: Annotated[Literal["asc", "desc"], Query()] = "desc",
) -> BoxSort:
    """Validate the sort query params before they reach the DB layer.

    ``sort_by`` is checked against the allowlist; unknown values 422 so
    a typo never leaks into an ``ORDER BY <user input>`` clause. When
    ``sort_by`` is omitted the caller stays on the historical default
    (``updated_at desc`` -- see :func:`apply_box_sort`) which keeps
    pre-sort consumers behaving identically.
    """
    if sort_by is not None and sort_by not in SORTABLE_FIELDS:
        raise HTTPException(
            status_code=422,
            detail=(
                "sort_by must be one of " + ", ".join(sorted(SORTABLE_FIELDS))
            ),
        )
    return BoxSort(sort_by=sort_by, sort_dir=sort_dir)


def apply_box_filters(
    stmt: Select,
    filters: BoxFilters,
    *,
    include_archived: bool = False,
) -> Select:
    if not include_archived:
        stmt = stmt.where(Box.archived_at.is_(None))
    if filters.warehouse_id is not None:
        stmt = stmt.where(Box.current_warehouse_id == filters.warehouse_id)
    if filters.status is not None:
        stmt = stmt.where(Box.status == filters.status)
    if filters.lot_id is not None:
        stmt = stmt.where(Box.lot_id == filters.lot_id)
    if filters.lot:
        stmt = stmt.where(Box.lot.ilike(f"%{filters.lot}%"))
    if filters.pallet_id is not None:
        stmt = stmt.where(Box.pallet_id == filters.pallet_id)
    elif filters.unassigned_pallet:
        stmt = stmt.where(Box.pallet_id.is_(None))
    if filters.search:
        like = f"%{filters.search}%"
        stmt = stmt.where(
            or_(
                Box.box_number.ilike(like),
                Box.lot.ilike(like),
                Box.pallet.has(Pallet.pallet_number.ilike(like)),
                Box.contents.ilike(like),
                Box.files.any(
                    and_(
                        BoxFile.archived_at.is_(None),
                        or_(
                            BoxFile.reference.ilike(like),
                            BoxFile.description.ilike(like),
                            BoxFile.barcode.ilike(like),
                        ),
                    ),
                ),
            )
        )
    if filters.received_from is not None:
        stmt = stmt.where(Box.received_at >= filters.received_from)
    if filters.received_to is not None:
        stmt = stmt.where(Box.received_at <= filters.received_to)
    if filters.updated_from is not None:
        stmt = stmt.where(Box.updated_at >= filters.updated_from)
    if filters.updated_to is not None:
        stmt = stmt.where(Box.updated_at <= filters.updated_to)
    return stmt


def apply_box_sort(stmt: Select, sort: BoxSort) -> Select:
    """Order the boxes list according to the validated ``BoxSort``.

    A secondary ``Box.id desc`` tiebreaker is always appended so rows
    sharing the primary sort key keep a stable position across pages
    (e.g. when sorting by ``status`` the page boundary doesn't
    shuffle within the same status group).

    ``warehouse`` is the only key that requires a join: we ``outerjoin``
    ``Warehouse`` so rows whose FK is somehow null (legacy data) still
    land at the bottom of the listing instead of disappearing.
    """
    key = sort.sort_by or "updated_at"
    if key == "warehouse":
        stmt = stmt.outerjoin(
            Warehouse, Warehouse.id == Box.current_warehouse_id
        )
        col = Warehouse.name
    else:
        col = {
            "box_number": Box.box_number,
            "lot": Box.lot,
            "status": Box.status,
            "received_at": Box.received_at,
            "updated_at": Box.updated_at,
        }[key]
    ordered = col.asc() if sort.sort_dir == "asc" else col.desc()
    return stmt.order_by(ordered, Box.id.desc())
