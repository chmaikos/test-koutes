from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.boxes import BoxStatus
from app.models.requests import (
    BoxRequestDirection,
    BoxRequestDocumentType,
    BoxRequestEventType,
    BoxRequestOrigin,
    BoxRequestStatus,
)
from app.services.boxes import BoxRuleError, normalize_box_number


class BoxRequestCreate(BaseModel):
    direction: BoxRequestDirection
    warehouse_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=5000)
    source_inbound_request_id: int | None = Field(default=None, ge=1)
    box_ids: list[int] | None = Field(default=None, min_length=1, max_length=5000)

    @field_validator("box_ids")
    @classmethod
    def unique_box_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("box_ids must not contain duplicates")
        if value is not None and any(box_id < 1 for box_id in value):
            raise ValueError("box_ids must contain positive IDs")
        return value


class RequestAction(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)


class RequestReject(RequestAction):
    reason: str = Field(min_length=1, max_length=2000)


class RequestCancel(RequestAction):
    reason: str | None = Field(default=None, max_length=2000)


class InboundBoxItem(BaseModel):
    lot: str = Field(min_length=1, max_length=64)
    box_number: str = Field(min_length=1, max_length=64)
    contents: str | None = Field(default=None, max_length=200)

    @field_validator("box_number")
    @classmethod
    def normalize_number(cls, value: str) -> str:
        try:
            return normalize_box_number(value)
        except BoxRuleError as exc:
            raise ValueError(str(exc)) from exc


class RequestComplete(RequestAction):
    inbound_items: list[InboundBoxItem] | None = Field(default=None, max_length=5000)
    discrepancy_reason: str | None = Field(default=None, max_length=2000)


class BoxRequestItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    box_id: int | None
    lot: str | None
    box_number: str | None
    contents: str | None


class BoxRequestDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_type: BoxRequestDocumentType
    erp_reference: str
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by_user_id: int | None
    created_at: datetime
    is_current: bool


class BoxRequestOut(BaseModel):
    id: int
    direction: BoxRequestDirection
    warehouse_id: int
    quantity: int
    status: BoxRequestStatus
    requester_user_id: int | None
    requester_name: str
    source_inbound_request_id: int | None
    origin: BoxRequestOrigin
    suggestion_quantity: int
    current_available: int
    min_inventory: int
    pending_inbound: int
    eligible_return: int
    actual_received_quantity: int | None
    variance_quantity: int | None
    rejection_reason: str | None
    cancellation_reason: str | None
    discrepancy_reason: str | None
    submitted_at: datetime
    approved_at: datetime | None
    in_transit_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int
    items: list[BoxRequestItemOut] = Field(default_factory=list)
    documents: list[BoxRequestDocumentOut] = Field(default_factory=list)


class BoxRequestEventOut(BaseModel):
    id: int
    event_type: BoxRequestEventType
    from_status: BoxRequestStatus | None
    to_status: BoxRequestStatus | None
    user_id: int | None
    user_name: str | None
    note: str | None
    occurred_at: datetime


class BoxRequestSuggestion(BaseModel):
    direction: BoxRequestDirection
    warehouse_id: int
    current_available: int
    min_inventory: int
    pending_inbound: int
    suggested_quantity: int
    eligible_return: int


class ReturnSourceOut(BaseModel):
    id: int
    warehouse_id: int
    completed_at: datetime
    origin: BoxRequestOrigin
    delivered_quantity: int
    eligible_quantity: int


class ReturnCandidateOut(BaseModel):
    box_id: int
    box_number: str
    lot: str
    contents: str | None
    status: BoxStatus


class XlsxPreviewRow(BaseModel):
    row_number: int
    cells: list[str]


class XlsxPreviewSheet(BaseModel):
    name: str
    max_columns: int
    rows: list[XlsxPreviewRow]


class XlsxPreviewOut(BaseModel):
    filename: str
    sheets: list[XlsxPreviewSheet]
