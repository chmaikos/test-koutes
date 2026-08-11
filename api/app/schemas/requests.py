from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.boxes import BoxStatus
from app.models.requests import (
    BoxRequestDirection,
    BoxRequestDiscrepancyType,
    BoxRequestDocumentType,
    BoxRequestEventType,
    BoxRequestExceptionKind,
    BoxRequestOrigin,
    BoxRequestPriority,
    BoxRequestStatus,
)
from app.services.boxes import BoxRuleError, normalize_box_number


class BoxRequestCreate(BaseModel):
    direction: BoxRequestDirection
    warehouse_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=5000)
    source_inbound_request_id: int | None = Field(default=None, ge=1)
    box_ids: list[int] | None = Field(default=None, min_length=1, max_length=5000)
    priority: BoxRequestPriority = BoxRequestPriority.normal
    requested_date: date | None = None
    sla_deadline: datetime | None = None
    destination_contact: str | None = Field(default=None, max_length=320)
    internal_location: str | None = Field(default=None, max_length=320)
    special_handling_instructions: str | None = Field(default=None, max_length=5000)

    @field_validator("box_ids")
    @classmethod
    def unique_box_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("box_ids must not contain duplicates")
        if value is not None and any(box_id < 1 for box_id in value):
            raise ValueError("box_ids must contain positive IDs")
        return value


class RequestAction(BaseModel):
    expected_version: int = Field(ge=1)


class RequestReject(RequestAction):
    reason: str = Field(min_length=1, max_length=2000)


class RequestCancel(RequestAction):
    reason: str | None = Field(default=None, max_length=2000)


class RequestHold(RequestAction):
    reason: str = Field(min_length=1, max_length=2000)


class RequestResume(RequestAction):
    resolution: str = Field(min_length=1, max_length=2000)


class RequestReschedule(RequestAction):
    reason: str = Field(min_length=1, max_length=2000)
    revised_window_start: datetime
    revised_window_end: datetime

    @model_validator(mode="after")
    def validate_revised_window(self) -> RequestReschedule:
        if self.revised_window_end <= self.revised_window_start:
            raise ValueError("revised_window_end must be after revised_window_start")
        return self


class RequestFailedDelivery(RequestAction):
    reason: str = Field(min_length=1, max_length=2000)
    revised_window_start: datetime | None = None
    revised_window_end: datetime | None = None

    @model_validator(mode="after")
    def validate_revised_window(self) -> RequestFailedDelivery:
        if (self.revised_window_start is None) != (self.revised_window_end is None):
            raise ValueError("both revised window values are required")
        if (
            self.revised_window_start is not None
            and self.revised_window_end is not None
            and self.revised_window_end <= self.revised_window_start
        ):
            raise ValueError("revised_window_end must be after revised_window_start")
        return self


class RequestRetry(RequestAction):
    resolution: str = Field(min_length=1, max_length=2000)


class RequestCoordinationUpdate(RequestAction):
    priority: BoxRequestPriority | None = None
    requested_date: date | None = None
    scheduled_window_start: datetime | None = None
    scheduled_window_end: datetime | None = None
    sla_deadline: datetime | None = None
    assigned_mover_user_id: int | None = Field(default=None, ge=1)
    destination_contact: str | None = Field(default=None, max_length=320)
    internal_location: str | None = Field(default=None, max_length=320)
    special_handling_instructions: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def validate_window(self) -> RequestCoordinationUpdate:
        fields = self.model_fields_set
        if (
            "scheduled_window_end" in fields
            and self.scheduled_window_end is not None
            and "scheduled_window_start" in fields
            and self.scheduled_window_start is None
        ):
            raise ValueError("scheduled_window_start is required when an end is set")
        if (
            self.scheduled_window_start is not None
            and self.scheduled_window_end is not None
            and self.scheduled_window_end <= self.scheduled_window_start
        ):
            raise ValueError("scheduled_window_end must be after the start")
        return self


class RequestCommentCreate(RequestAction):
    body: str = Field(min_length=1, max_length=5000)


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


class RequestDiscrepancyInput(BaseModel):
    discrepancy_type: BoxRequestDiscrepancyType
    request_item_id: int | None = Field(default=None, ge=1)
    box_id: int | None = Field(default=None, ge=1)
    quantity: int | None = Field(default=None, ge=1, le=5000)
    notes: str | None = Field(default=None, max_length=2000)


class RequestComplete(RequestAction):
    idempotency_key: str = Field(min_length=8, max_length=120)
    inbound_items: list[InboundBoxItem] | None = Field(default=None, max_length=5000)
    collected_box_ids: list[int] | None = Field(default=None, max_length=5000)
    discrepancies: list[RequestDiscrepancyInput] = Field(
        default_factory=list, max_length=5000
    )
    discrepancy_reason: str | None = Field(default=None, max_length=2000)

    @field_validator("collected_box_ids")
    @classmethod
    def unique_collected_box_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and (len(value) != len(set(value)) or any(v < 1 for v in value)):
            raise ValueError("collected_box_ids must contain unique positive IDs")
        return value


class RequestDraftSubmit(RequestAction):
    box_ids: list[int] = Field(min_length=1, max_length=5000)

    @field_validator("box_ids")
    @classmethod
    def unique_box_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)) or any(v < 1 for v in value):
            raise ValueError("box_ids must contain unique positive IDs")
        return value


class BoxRequestItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    box_id: int | None
    lot_id: int | None
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


class RequestAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by_user_id: int | None
    created_at: datetime


class RequestCommentOut(BaseModel):
    id: int
    author_user_id: int | None
    author_name: str
    body: str
    created_at: datetime


class RequestAssigneeOut(BaseModel):
    id: int
    display_name: str
    email: str
    role: str


class RequestPermissionsOut(BaseModel):
    can_assign: bool
    can_schedule: bool
    can_comment: bool
    can_attach: bool
    can_prepare: bool
    can_mark_ready: bool
    can_start_transit: bool
    can_mark_arrived: bool
    can_confirm: bool
    can_hold: bool
    can_resume: bool
    can_reschedule: bool
    can_report_failed: bool
    can_retry: bool


class RequestExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exception_kind: BoxRequestExceptionKind
    reason: str
    revised_window_start: datetime | None
    revised_window_end: datetime | None
    resume_target: BoxRequestStatus
    created_by_user_id: int | None
    created_at: datetime
    resolved_by_user_id: int | None
    resolved_at: datetime | None
    resolution: str | None


class RequestDiscrepancyPhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by_user_id: int | None
    created_at: datetime


class RequestDiscrepancyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_item_id: int | None
    box_id: int | None
    discrepancy_type: BoxRequestDiscrepancyType
    quantity: int | None
    notes: str | None
    created_by_user_id: int | None
    created_at: datetime
    photos: list[RequestDiscrepancyPhotoOut] = Field(default_factory=list)


class BoxRequestOut(BaseModel):
    id: int
    direction: BoxRequestDirection
    warehouse_id: int
    quantity: int
    status: BoxRequestStatus
    requester_user_id: int | None
    requester_name: str
    priority: BoxRequestPriority
    requested_date: date | None
    scheduled_window_start: datetime | None
    scheduled_window_end: datetime | None
    sla_deadline: datetime | None
    assigned_mover_user_id: int | None
    assigned_mover_name: str | None
    destination_contact: str | None
    internal_location: str | None
    special_handling_instructions: str | None
    source_inbound_request_id: int | None
    parent_request_id: int | None
    root_request_id: int | None
    child_request_ids: list[int] = Field(default_factory=list)
    origin: BoxRequestOrigin
    suggestion_quantity: int
    current_available: int
    min_inventory: int
    pending_inbound: int
    eligible_return: int
    recommendation_snapshot: dict[str, object] | None
    actual_received_quantity: int | None
    variance_quantity: int | None
    rejection_reason: str | None
    cancellation_reason: str | None
    discrepancy_reason: str | None
    receipt_restore_archived: bool
    receipt_quarantine: bool
    receipt_document_required: bool
    submitted_at: datetime
    approved_at: datetime | None
    approved_by_user_id: int | None
    preparing_at: datetime | None
    preparing_by_user_id: int | None
    ready_for_transport_at: datetime | None
    ready_for_transport_by_user_id: int | None
    in_transit_at: datetime | None
    in_transit_by_user_id: int | None
    awaiting_confirmation_at: datetime | None
    awaiting_confirmation_by_user_id: int | None
    completed_at: datetime | None
    completed_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    version: int
    items: list[BoxRequestItemOut] = Field(default_factory=list)
    documents: list[BoxRequestDocumentOut] = Field(default_factory=list)
    discrepancies: list[RequestDiscrepancyOut] = Field(default_factory=list)
    comments: list[RequestCommentOut] = Field(default_factory=list)
    attachments: list[RequestAttachmentOut] = Field(default_factory=list)
    operational_exceptions: list[RequestExceptionOut] = Field(default_factory=list)
    current_exception: RequestExceptionOut | None = None
    permissions: RequestPermissionsOut


class BoxRequestEventOut(BaseModel):
    id: int
    event_type: BoxRequestEventType
    from_status: BoxRequestStatus | None
    to_status: BoxRequestStatus | None
    user_id: int | None
    user_name: str | None
    note: str | None
    metadata: dict[str, object] = Field(default_factory=dict)
    occurred_at: datetime


class RequestConflictOut(BaseModel):
    message: str
    request_id: int
    latest_version: int
    latest_status: BoxRequestStatus
    relevant_events: list[BoxRequestEventOut] = Field(default_factory=list)


RequestIssueSeverity = Literal["critical", "high", "medium", "low"]


class RequestReconciliationIssue(BaseModel):
    issue_key: str
    issue_type: str
    severity: RequestIssueSeverity
    request_id: int
    box_id: int | None = None
    warehouse_id: int
    warehouse_name: str
    assigned_mover_user_id: int | None = None
    assigned_mover_name: str | None = None
    request_status: BoxRequestStatus
    title: str
    detail: str
    occurred_at: datetime
    due_at: datetime | None = None
    request_path: str
    box_path: str | None = None


class RequestReconciliationSummary(BaseModel):
    total: int
    critical: int
    high: int
    medium: int
    low: int
    by_type: dict[str, int] = Field(default_factory=dict)


class RequestReconciliationOut(BaseModel):
    items: list[RequestReconciliationIssue]
    total: int
    page: int
    page_size: int
    generated_at: datetime
    summary: RequestReconciliationSummary


class RequestDurationMetric(BaseModel):
    supported: bool
    average_seconds: float | None
    sample_size: int


class RequestRateMetric(BaseModel):
    numerator: int
    denominator: int
    rate: float | None


class RequestReasonCount(BaseModel):
    reason: str
    count: int


class RequestThroughput(BaseModel):
    id: int | None
    name: str
    completed_requests: int
    completed_quantity: int


class RequestAnalyticsOut(BaseModel):
    generated_at: datetime
    from_at: datetime | None
    to_at: datetime | None
    total_requests: int
    completed_requests: int
    approval_duration: RequestDurationMetric
    preparation_duration: RequestDurationMetric
    transport_duration: RequestDurationMetric
    acceptance_duration: RequestDurationMetric
    on_time: RequestRateMetric
    discrepancy: RequestRateMetric
    shortage: RequestRateMetric
    overage: RequestRateMetric
    rejection_reasons: list[RequestReasonCount]
    cancellation_reasons: list[RequestReasonCount]
    throughput_by_warehouse: list[RequestThroughput]
    throughput_by_mover: list[RequestThroughput]


class BoxRequestSuggestion(BaseModel):
    direction: BoxRequestDirection
    warehouse_id: int
    analysis_as_of: datetime
    current_available: int
    min_inventory: int
    max_capacity: int
    current_occupied: int
    baseline_gap: int
    minimum_gap: int
    pending_inbound: int
    pending_backorder: int
    scheduled_inbound: int
    scheduled_return: int
    history_window_30_start: datetime
    history_window_90_start: datetime
    history_window_end: datetime
    history_30_quantity: int
    history_90_quantity: int
    history_30_daily_rate: float
    history_90_daily_rate: float
    history_30_weight: int
    history_90_weight: int
    normalized_30_weight: float
    normalized_90_weight: float
    sample_size: int
    history_days: int
    confidence: Literal[
        "insufficient", "low", "medium", "high", "not_applicable"
    ]
    weighted_daily_rate: float
    daily_demand_forecast: float
    lead_time_days: int
    lead_time_demand: int
    safety_stock_percent: int
    safety_stock_quantity: int
    forecast_adjustment: int | None
    adjustment_quantity: int
    target_inventory: int
    capacity_limit: int
    capacity_available: int
    capacity_cap_applied: bool
    fallback_used: bool
    fallback_reason: str | None
    suggested_quantity: int
    eligible_return: int
    consumption_definition: str
    formula: str
    explanation: str


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
    lot_id: int
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
