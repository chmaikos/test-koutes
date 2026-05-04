from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from app.deps import CurrentUser, DbSession, require_operator
from app.events import bus
from app.schemas.boxes import BoxOut, ImportResult, ImportSkip
from app.services.alerts import evaluate_safe
from app.services.boxes import BoxRuleError
from app.services.imports import import_boxes_xlsx

router = APIRouter(prefix="/boxes", tags=["boxes"])


@router.post("/import", response_model=ImportResult)
async def import_boxes(
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_operator)],
    file: UploadFile = File(..., description="An XLSX workbook of new boxes"),
    warehouse_id: int | None = Form(
        default=None,
        description="Optional default warehouse for rows that don't specify one",
    ),
) -> ImportResult:
    if file.filename and not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="only .xlsx files are supported",
        )
    payload = await file.read()
    try:
        outcome = import_boxes_xlsx(
            db,
            user=user,
            file_bytes=payload,
            default_warehouse_id=warehouse_id,
        )
    except BoxRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    affected_warehouses = {b.current_warehouse_id for b in outcome.created}
    for wid in affected_warehouses:
        await bus.publish("box.updated", {"warehouse_id": wid, "bulk": True})
    if outcome.created:
        background.add_task(evaluate_safe, db)

    return ImportResult(
        created=[BoxOut.model_validate(b) for b in outcome.created],
        skipped=[
            ImportSkip(row=s.row, box_number=s.box_number, reason=s.reason)
            for s in outcome.skipped
        ],
    )
