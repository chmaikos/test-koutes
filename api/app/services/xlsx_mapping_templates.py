from __future__ import annotations

import fnmatch
import re
import unicodedata
from datetime import UTC
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.models.users import User
from app.models.warehouses import Warehouse
from app.models.xlsx_mapping_templates import (
    XlsxMappingTemplate,
    XlsxMappingUseCase,
)
from app.services.acl import allowed_warehouse_ids, can_access


class XlsxTemplateRuleError(ValueError):
    pass


def normalize_match_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    text = "".join(
        character
        for character in decomposed
        if not unicodedata.category(character).startswith("M")
    )
    return " ".join(part for part in re.split(r"[\W_]+", text) if part)


def filename_fingerprint(filename: str) -> str:
    return normalize_match_text(Path(filename).name)


def header_fingerprint(headers: list[str]) -> str:
    normalized = [normalize_match_text(value) for value in headers[:50]]
    while normalized and not normalized[-1]:
        normalized.pop()
    return "\x1f".join(normalized)


def visible_templates_stmt(
    user: User,
    *,
    use_case: XlsxMappingUseCase,
    warehouse_id: int | None,
) -> Select[tuple[XlsxMappingTemplate]]:
    """Return own templates plus shared templates in accessible warehouses.

    Admins can see every warehouse-shared template but, like other users, never
    see another user's private templates. Mutation remains owner-only.
    """
    stmt = select(XlsxMappingTemplate).where(
        XlsxMappingTemplate.use_case == use_case
    )
    allowed = allowed_warehouse_ids(user)
    shared_clause = XlsxMappingTemplate.warehouse_id.is_not(None)
    if allowed is not None:
        shared_clause = XlsxMappingTemplate.warehouse_id.in_(allowed)
    if warehouse_id is not None:
        shared_clause = shared_clause & (
            XlsxMappingTemplate.warehouse_id == warehouse_id
        )
    own_clause = XlsxMappingTemplate.owner_user_id == user.id
    if warehouse_id is not None:
        own_clause = own_clause & or_(
            XlsxMappingTemplate.warehouse_id.is_(None),
            XlsxMappingTemplate.warehouse_id == warehouse_id,
        )
    return stmt.where(or_(own_clause, shared_clause))


def ensure_template_visible(user: User, template: XlsxMappingTemplate) -> None:
    if template.owner_user_id == user.id:
        return
    if template.warehouse_id is not None and can_access(user, template.warehouse_id):
        return
    raise XlsxTemplateRuleError("mapping template not found")


def ensure_template_owner(user: User, template: XlsxMappingTemplate) -> None:
    """Require ownership even for admins; administration grants visibility, not takeover."""
    if template.owner_user_id != user.id:
        raise XlsxTemplateRuleError("only the template owner can change it")


def validate_sharing(
    db: Session,
    user: User,
    warehouse_id: int | None,
) -> None:
    if warehouse_id is None:
        return
    if db.get(Warehouse, warehouse_id) is None:
        raise XlsxTemplateRuleError("warehouse not found")
    if not can_access(user, warehouse_id):
        raise XlsxTemplateRuleError(
            "warehouse access is required to share a mapping template"
        )


def validate_mapping_config(
    *,
    lot_source: str,
    fixed_lot: str | None,
    column_mappings: dict,
) -> None:
    if "box_number" not in column_mappings:
        raise XlsxTemplateRuleError("box number mapping is required")
    if "pallet_number" not in column_mappings:
        raise XlsxTemplateRuleError("pallet number mapping is required")
    if lot_source == "fixed":
        if not (fixed_lot or "").strip():
            raise XlsxTemplateRuleError("fixed lot is required")
    elif lot_source == "column":
        if not column_mappings.get("lot"):
            raise XlsxTemplateRuleError("lot column mapping is required")
    else:
        raise XlsxTemplateRuleError("invalid lot source")


def rank_suggestions(
    templates: list[XlsxMappingTemplate],
    *,
    filename: str,
    sheet_name: str,
    header_candidates: list[list[str]],
) -> list[tuple[XlsxMappingTemplate, float, str, bool]]:
    current_filename = filename_fingerprint(filename)
    current_sheet = normalize_match_text(sheet_name)
    candidate_headers = [
        fingerprint
        for headers in header_candidates
        if (fingerprint := header_fingerprint(headers))
    ]
    ranked: list[tuple[XlsxMappingTemplate, float, str, bool]] = []
    for template in templates:
        exact_header = bool(
            template.header_fingerprint
            and template.header_fingerprint in candidate_headers
        )
        header_score = max(
            (
                SequenceMatcher(
                    None, template.header_fingerprint, candidate
                ).ratio()
                for candidate in candidate_headers
            ),
            default=0.0,
        )
        filename_score = SequenceMatcher(
            None, template.filename_fingerprint, current_filename
        ).ratio()
        normalized_pattern = normalize_match_text(template.sheet_pattern)
        pattern_match = fnmatch.fnmatch(
            sheet_name.casefold(), template.sheet_pattern.casefold()
        )
        sheet_score = (
            1.0
            if pattern_match
            else SequenceMatcher(None, normalized_pattern, current_sheet).ratio()
        )
        usage_score = min(template.usage_count / 20, 1)
        confidence = (
            0.55 * header_score
            + 0.22 * filename_score
            + 0.18 * sheet_score
            + 0.05 * usage_score
        )
        if exact_header:
            confidence = max(confidence, 0.98)
            explanation = "Exact normalized header match"
        else:
            reasons = []
            if header_score >= 0.7:
                reasons.append("similar headers")
            if filename_score >= 0.65:
                reasons.append("similar filename")
            if sheet_score >= 0.65:
                reasons.append("matching worksheet")
            explanation = (
                "Matched by " + ", ".join(reasons)
                if reasons
                else "Low-similarity fallback"
            )
        ranked.append((template, round(min(confidence, 1), 3), explanation, exact_header))
    ranked.sort(
        key=lambda item: (
            item[3],
            item[1],
            item[0].usage_count,
            _last_used_sort_value(item[0]),
            -item[0].id,
        ),
        reverse=True,
    )
    return ranked


def _last_used_sort_value(template: XlsxMappingTemplate) -> float:
    value = template.last_used_at
    if value is None:
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()
