from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.xlsx_mapping_templates import XlsxMappingUseCase


class XlsxColumnRef(BaseModel):
    index: int = Field(ge=0, lt=50)
    header: str = Field(default="", max_length=240)


class XlsxColumnMappings(BaseModel):
    box_number: XlsxColumnRef
    pallet_number: XlsxColumnRef
    lot: XlsxColumnRef | None = None
    contents: XlsxColumnRef | None = None


class XlsxMappingTemplateCreate(BaseModel):
    use_case: XlsxMappingUseCase
    name: str = Field(min_length=1, max_length=120)
    warehouse_id: int | None = None
    sheet_pattern: str = Field(default="*", min_length=1, max_length=120)
    filename: str = Field(default="", max_length=255)
    headers: list[str] = Field(default_factory=list, max_length=50)
    column_mappings: XlsxColumnMappings
    lot_source: Literal["fixed", "column"]
    fixed_lot: str | None = Field(default=None, max_length=64)
    row_start: int = Field(default=1, ge=1, le=5000)
    include_rows_by_default: bool = True

    @model_validator(mode="after")
    def validate_lot_config(self):
        if self.lot_source == "fixed" and not (self.fixed_lot or "").strip():
            raise ValueError("fixed_lot is required when lot_source is fixed")
        if self.lot_source == "column" and self.column_mappings.lot is None:
            raise ValueError("a lot column is required when lot_source is column")
        return self


class XlsxMappingTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    warehouse_id: int | None = None
    sheet_pattern: str | None = Field(default=None, min_length=1, max_length=120)
    filename: str | None = Field(default=None, max_length=255)
    headers: list[str] | None = Field(default=None, max_length=50)
    column_mappings: XlsxColumnMappings | None = None
    lot_source: Literal["fixed", "column"] | None = None
    fixed_lot: str | None = Field(default=None, max_length=64)
    row_start: int | None = Field(default=None, ge=1, le=5000)
    include_rows_by_default: bool | None = None


class XlsxMappingTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int
    owner_name: str
    warehouse_id: int | None
    use_case: XlsxMappingUseCase
    name: str
    sheet_pattern: str
    filename_fingerprint: str
    header_fingerprint: str
    column_mappings: XlsxColumnMappings
    lot_source: Literal["fixed", "column"]
    fixed_lot: str | None
    row_start: int
    include_rows_by_default: bool
    usage_count: int
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime
    is_owner: bool
    is_shared: bool


class XlsxMappingSuggestionRequest(BaseModel):
    use_case: XlsxMappingUseCase
    warehouse_id: int | None = None
    filename: str = Field(default="", max_length=255)
    sheet_name: str = Field(default="", max_length=120)
    header_candidates: list[list[str]] = Field(default_factory=list, max_length=20)


class XlsxMappingSuggestion(BaseModel):
    template: XlsxMappingTemplateOut
    confidence: float = Field(ge=0, le=1)
    explanation: str
    exact_header_match: bool
