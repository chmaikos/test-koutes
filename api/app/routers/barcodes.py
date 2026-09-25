from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models.barcode_identities import BarcodeFileMigrationAudit
from app.models.users import UserRole
from app.schemas.barcodes import (
    BarcodeFileMigrationAuditOut,
    BarcodeResolutionOut,
)
from app.services.barcodes import (
    BarcodeNotFoundError,
    InvalidBarcodeError,
    resolve_barcode,
)

router = APIRouter(prefix="/barcodes", tags=["barcodes"])


@router.get(
    "/migration-audit/files",
    response_model=list[BarcodeFileMigrationAuditOut],
)
def list_file_barcode_migration_audit(
    db: DbSession,
    user: CurrentUser,
) -> list[BarcodeFileMigrationAuditOut]:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    rows = db.scalars(
        select(BarcodeFileMigrationAudit).order_by(
            BarcodeFileMigrationAudit.file_id
        )
    ).all()
    return [BarcodeFileMigrationAuditOut.model_validate(row) for row in rows]


@router.get("/{barcode}", response_model=BarcodeResolutionOut)
def resolve_exact_barcode(
    barcode: str,
    db: DbSession,
    user: CurrentUser,
) -> BarcodeResolutionOut:
    try:
        result = resolve_barcode(db, user=user, barcode=barcode)
    except InvalidBarcodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except BarcodeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="barcode not found",
        ) from exc
    return BarcodeResolutionOut.model_validate(result)


__all__ = ["router"]
