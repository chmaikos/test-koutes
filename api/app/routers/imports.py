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
from app.events import bus, publish_notification_event
from app.models.requests import BoxRequest
from app.schemas.boxes import (
    BoxOut,
    ImportResult,
    ImportSkip,
    MappedImportRequest,
)
from app.schemas.requests import (
    XlsxPreviewOut,
    XlsxPreviewRow,
    XlsxPreviewSheet,
)
from app.services.alerts import evaluate_safe
from app.services.boxes import BoxRuleError
from app.services.imports import import_boxes_xlsx, import_mapped_boxes
from app.services.requests import RequestAccessError, RequestRuleError
from app.services.xlsx_preview import MAX_XLSX_BYTES, preview_xlsx

router = APIRouter(prefix="/boxes", tags=["boxes"])


@router.post("/import-preview", response_model=XlsxPreviewOut)
async def preview_box_import(
    user: Annotated[CurrentUser, Depends(require_operator)],
    file: Annotated[UploadFile, File(description="An XLSX workbook to map")],
) -> XlsxPreviewOut:
    del user
    filename = file.filename or "workbook.xlsx"
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="only .xlsx files are supported")
    payload = await file.read(MAX_XLSX_BYTES + 1)
    try:
        sheets = preview_xlsx(payload)
    except (BoxRuleError, RequestRuleError) as exc:
        code = 403 if isinstance(exc, RequestAccessError) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
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


@router.post("/import-mapped", response_model=ImportResult)
async def import_mapped_box_rows(
    payload: MappedImportRequest,
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_operator)],
) -> ImportResult:
    try:
        outcome = import_mapped_boxes(
            db,
            user=user,
            warehouse_id=payload.warehouse_id,
            items=[
                (item.box_number, item.lot, item.contents)
                for item in payload.items
            ],
            restore_archived=payload.restore_archived,
        )
    except BoxRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if outcome.created or outcome.restored:
        await bus.publish(
            "box.updated",
            {"warehouse_id": payload.warehouse_id, "bulk": True},
        )
    for request_id in outcome.receipt_request_ids:
        await bus.publish("request.created", {"id": request_id})
    for request_id in outcome.staged_receipt_ids:
        await bus.publish("request.created", {"id": request_id, "staged": True})
        await publish_notification_event(
            warehouse_id=payload.warehouse_id,
            request_id=request_id,
        )
    if outcome.created or outcome.restored:
        background.add_task(evaluate_safe, db)
    return ImportResult(
        created=[BoxOut.model_validate(box) for box in outcome.created],
        restored=[BoxOut.model_validate(box) for box in outcome.restored],
        skipped=[
            ImportSkip(
                row=skipped.row,
                box_number=skipped.box_number,
                reason=skipped.reason,
            )
            for skipped in outcome.skipped
        ],
        receipt_request_ids=outcome.receipt_request_ids,
        staged_receipt_ids=outcome.staged_receipt_ids,
    )


@router.post("/import", response_model=ImportResult)
async def import_boxes(
    db: DbSession,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_operator)],
    file: Annotated[
        UploadFile,
        File(description="An XLSX workbook of new boxes"),
    ],
    warehouse_id: Annotated[
        int | None,
        Form(
            description="Optional default warehouse for rows that don't specify one"
        ),
    ] = None,
    restore_archived: Annotated[
        bool,
        Form(description="Restore archived boxes matching lot and box number"),
    ] = False,
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
            restore_archived=restore_archived,
        )
    except (BoxRuleError, RequestRuleError) as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
                if isinstance(exc, RequestAccessError)
                else status.HTTP_400_BAD_REQUEST
            ),
            detail=str(exc),
        ) from exc

    affected_warehouses = {
        box.current_warehouse_id
        for box in outcome.created + outcome.restored
    }
    for wid in affected_warehouses:
        await bus.publish("box.updated", {"warehouse_id": wid, "bulk": True})
    for request_id in outcome.receipt_request_ids:
        await bus.publish("request.created", {"id": request_id})
    for request_id in outcome.staged_receipt_ids:
        await bus.publish("request.created", {"id": request_id, "staged": True})
        staged = db.get(BoxRequest, request_id)
        if staged is not None:
            await publish_notification_event(
                warehouse_id=staged.warehouse_id,
                request_id=request_id,
            )
    if outcome.created or outcome.restored:
        background.add_task(evaluate_safe, db)

    return ImportResult(
        created=[BoxOut.model_validate(b) for b in outcome.created],
        restored=[BoxOut.model_validate(b) for b in outcome.restored],
        skipped=[
            ImportSkip(row=s.row, box_number=s.box_number, reason=s.reason)
            for s in outcome.skipped
        ],
        receipt_request_ids=outcome.receipt_request_ids,
        staged_receipt_ids=outcome.staged_receipt_ids,
    )
