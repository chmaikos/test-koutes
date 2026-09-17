from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError

from app.deps import CurrentUser, DbSession
from app.models.xlsx_mapping_templates import (
    XlsxMappingTemplate,
    XlsxMappingUseCase,
)
from app.schemas.xlsx_mapping_templates import (
    XlsxMappingSuggestion,
    XlsxMappingSuggestionRequest,
    XlsxMappingTemplateCreate,
    XlsxMappingTemplateOut,
    XlsxMappingTemplateUpdate,
)
from app.services.xlsx_mapping_templates import (
    XlsxTemplateRuleError,
    ensure_template_owner,
    ensure_template_visible,
    filename_fingerprint,
    header_fingerprint,
    rank_suggestions,
    validate_mapping_config,
    validate_sharing,
    visible_templates_stmt,
)

router = APIRouter(
    prefix="/xlsx-mapping-templates", tags=["xlsx-mapping-templates"]
)


def _error(exc: XlsxTemplateRuleError) -> HTTPException:
    message = str(exc)
    if message == "mapping template not found":
        code = status.HTTP_404_NOT_FOUND
    elif "owner" in message or "access" in message:
        code = status.HTTP_403_FORBIDDEN
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=message)


def _out(template: XlsxMappingTemplate, user: CurrentUser) -> XlsxMappingTemplateOut:
    return XlsxMappingTemplateOut(
        id=template.id,
        owner_user_id=template.owner_user_id,
        owner_name=template.owner.display_name or template.owner.email,
        warehouse_id=template.warehouse_id,
        use_case=template.use_case,
        name=template.name,
        sheet_pattern=template.sheet_pattern,
        filename_fingerprint=template.filename_fingerprint,
        header_fingerprint=template.header_fingerprint,
        column_mappings=template.column_mappings,
        lot_source=template.lot_source,
        fixed_lot=template.fixed_lot,
        row_start=template.row_start,
        include_rows_by_default=template.include_rows_by_default,
        usage_count=template.usage_count,
        last_used_at=template.last_used_at,
        created_at=template.created_at,
        updated_at=template.updated_at,
        is_owner=template.owner_user_id == user.id,
        is_shared=template.warehouse_id is not None,
        is_legacy_incomplete=not bool(
            template.column_mappings.get("file_reference")
        ),
    )


def _template(db: DbSession, template_id: int) -> XlsxMappingTemplate:
    template = db.get(XlsxMappingTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="mapping template not found")
    return template


def _commit(db: DbSession) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="you already have a template with that name for this use case",
        ) from exc


@router.get("", response_model=list[XlsxMappingTemplateOut])
def list_mapping_templates(
    db: DbSession,
    user: CurrentUser,
    use_case: XlsxMappingUseCase,
    warehouse_id: int | None = Query(default=None),
) -> list[XlsxMappingTemplateOut]:
    templates = db.scalars(
        visible_templates_stmt(
            user, use_case=use_case, warehouse_id=warehouse_id
        ).order_by(
            XlsxMappingTemplate.usage_count.desc(),
            XlsxMappingTemplate.updated_at.desc(),
        )
    ).all()
    return [_out(template, user) for template in templates]


@router.post("", response_model=XlsxMappingTemplateOut, status_code=201)
def create_mapping_template(
    payload: XlsxMappingTemplateCreate,
    db: DbSession,
    user: CurrentUser,
) -> XlsxMappingTemplateOut:
    mappings = payload.column_mappings.model_dump(exclude_none=True)
    try:
        validate_sharing(db, user, payload.warehouse_id)
        validate_mapping_config(
            lot_source=payload.lot_source,
            fixed_lot=payload.fixed_lot,
            column_mappings=mappings,
            require_file_reference=True,
        )
    except XlsxTemplateRuleError as exc:
        raise _error(exc) from exc
    template = XlsxMappingTemplate(
        owner_user_id=user.id,
        warehouse_id=payload.warehouse_id,
        use_case=payload.use_case,
        name=payload.name.strip(),
        sheet_pattern=payload.sheet_pattern.strip(),
        filename_fingerprint=filename_fingerprint(payload.filename),
        header_fingerprint=header_fingerprint(payload.headers),
        column_mappings=mappings,
        lot_source=payload.lot_source,
        fixed_lot=(payload.fixed_lot or "").strip() or None,
        row_start=payload.row_start,
        include_rows_by_default=payload.include_rows_by_default,
    )
    db.add(template)
    _commit(db)
    db.refresh(template)
    return _out(template, user)


@router.post("/suggestions", response_model=list[XlsxMappingSuggestion])
def suggest_mapping_templates(
    payload: XlsxMappingSuggestionRequest,
    db: DbSession,
    user: CurrentUser,
) -> list[XlsxMappingSuggestion]:
    templates = db.scalars(
        visible_templates_stmt(
            user,
            use_case=payload.use_case,
            warehouse_id=payload.warehouse_id,
        )
    ).all()
    return [
        XlsxMappingSuggestion(
            template=_out(template, user),
            confidence=confidence,
            explanation=explanation,
            exact_header_match=exact,
        )
        for template, confidence, explanation, exact in rank_suggestions(
            list(templates),
            filename=payload.filename,
            sheet_name=payload.sheet_name,
            header_candidates=payload.header_candidates,
        )[:5]
    ]


@router.patch("/{template_id}", response_model=XlsxMappingTemplateOut)
def update_mapping_template(
    template_id: int,
    payload: XlsxMappingTemplateUpdate,
    db: DbSession,
    user: CurrentUser,
) -> XlsxMappingTemplateOut:
    template = _template(db, template_id)
    try:
        ensure_template_visible(user, template)
        ensure_template_owner(user, template)
        if "warehouse_id" in payload.model_fields_set:
            validate_sharing(db, user, payload.warehouse_id)
    except XlsxTemplateRuleError as exc:
        raise _error(exc) from exc

    fields = payload.model_fields_set
    if "name" in fields:
        template.name = payload.name.strip()  # type: ignore[union-attr]
    if "warehouse_id" in fields:
        template.warehouse_id = payload.warehouse_id
    if "sheet_pattern" in fields:
        template.sheet_pattern = payload.sheet_pattern.strip()  # type: ignore[union-attr]
    if "filename" in fields:
        template.filename_fingerprint = filename_fingerprint(payload.filename or "")
    if "headers" in fields:
        template.header_fingerprint = header_fingerprint(payload.headers or [])
    if "column_mappings" in fields and payload.column_mappings is not None:
        template.column_mappings = payload.column_mappings.model_dump(exclude_none=True)
    if "lot_source" in fields and payload.lot_source is not None:
        template.lot_source = payload.lot_source
    if "fixed_lot" in fields:
        template.fixed_lot = (payload.fixed_lot or "").strip() or None
    if "row_start" in fields and payload.row_start is not None:
        template.row_start = payload.row_start
    if (
        "include_rows_by_default" in fields
        and payload.include_rows_by_default is not None
    ):
        template.include_rows_by_default = payload.include_rows_by_default
    try:
        validate_mapping_config(
            lot_source=template.lot_source,
            fixed_lot=template.fixed_lot,
            column_mappings=template.column_mappings,
            require_file_reference="column_mappings" in fields,
        )
    except XlsxTemplateRuleError as exc:
        raise _error(exc) from exc
    _commit(db)
    db.refresh(template)
    return _out(template, user)


@router.delete("/{template_id}", status_code=204)
def delete_mapping_template(
    template_id: int,
    db: DbSession,
    user: CurrentUser,
) -> Response:
    template = _template(db, template_id)
    try:
        ensure_template_visible(user, template)
        ensure_template_owner(user, template)
    except XlsxTemplateRuleError as exc:
        raise _error(exc) from exc
    db.delete(template)
    db.commit()
    return Response(status_code=204)


@router.post("/{template_id}/use", response_model=XlsxMappingTemplateOut)
def record_mapping_template_use(
    template_id: int,
    db: DbSession,
    user: CurrentUser,
) -> XlsxMappingTemplateOut:
    template = _template(db, template_id)
    try:
        ensure_template_visible(user, template)
    except XlsxTemplateRuleError as exc:
        raise _error(exc) from exc
    template.usage_count += 1
    from datetime import UTC, datetime

    template.last_used_at = datetime.now(UTC)
    db.commit()
    db.refresh(template)
    return _out(template, user)
