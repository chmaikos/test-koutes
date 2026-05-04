from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models.boxes import Box
from app.routers._filters import BoxFilters, apply_box_filters, parse_box_filters
from app.services.exports import boxes_to_csv, boxes_to_xlsx

router = APIRouter(prefix="/exports", tags=["exports"])


def _filename(prefix: str, ext: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}.{ext}"


@router.get("/boxes.csv")
def export_csv(
    db: DbSession,
    user: CurrentUser,
    filters: Annotated[BoxFilters, Depends(parse_box_filters)],
) -> Response:
    stmt = apply_box_filters(select(Box), filters).order_by(Box.updated_at.desc())
    rows = db.scalars(stmt).all()
    payload = boxes_to_csv(rows)
    return Response(
        content=payload,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename("boxes", "csv")}"'
        },
    )


@router.get("/boxes.xlsx")
def export_xlsx(
    db: DbSession,
    user: CurrentUser,
    filters: Annotated[BoxFilters, Depends(parse_box_filters)],
) -> Response:
    stmt = apply_box_filters(select(Box), filters).order_by(Box.updated_at.desc())
    rows = db.scalars(stmt).all()
    payload = boxes_to_xlsx(rows)
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename("boxes", "xlsx")}"'
        },
    )
