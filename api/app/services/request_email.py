from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.config import get_settings
from app.models.notifications import RequestNotificationKind
from app.models.requests import BoxRequest
from app.models.users import User
from app.models.warehouses import Warehouse

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(("html", "j2")),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class RequestEmail:
    subject: str
    html: str
    text: str


_COPY: dict[RequestNotificationKind, tuple[str, str, str]] = {
    RequestNotificationKind.staged: (
        "Receipt awaiting review",
        "An imported or manually entered receipt was staged for administrator review.",
        "#d97706",
    ),
    RequestNotificationKind.submitted: (
        "New request submitted",
        "A new request is waiting for warehouse coordination.",
        "#2563eb",
    ),
    RequestNotificationKind.approved: (
        "Request approved",
        "The request has been approved.",
        "#16a34a",
    ),
    RequestNotificationKind.rejected: (
        "Request rejected",
        "The request has been rejected. Open it to review the reason.",
        "#dc2626",
    ),
    RequestNotificationKind.preparation_started: (
        "Preparation started",
        "Warehouse preparation has started for this request.",
        "#7c3aed",
    ),
    RequestNotificationKind.ready_for_transport: (
        "Ready for transport",
        "The request is ready for dispatch or collection.",
        "#16a34a",
    ),
    RequestNotificationKind.document_needed: (
        "ERP document required",
        "A delivery note must be uploaded before this receipt can be finalized.",
        "#d97706",
    ),
    RequestNotificationKind.document_uploaded: (
        "ERP document uploaded",
        "A delivery or return document was added to the request.",
        "#2563eb",
    ),
    RequestNotificationKind.quarantine_released: (
        "Quarantine released",
        "Quarantined receipt boxes were released into available stock.",
        "#16a34a",
    ),
    RequestNotificationKind.quarantine_rejected: (
        "Quarantine rejected",
        "Quarantined receipt boxes were rejected and archived.",
        "#dc2626",
    ),
    RequestNotificationKind.scheduled: (
        "Transport scheduled",
        "The scheduled transport window changed.",
        "#7c3aed",
    ),
    RequestNotificationKind.transport_started: (
        "Transport started",
        "The request is now in transit.",
        "#0284c7",
    ),
    RequestNotificationKind.acceptance_required: (
        "Confirmation required",
        "Delivery or collection is complete and final confirmation is required.",
        "#ea580c",
    ),
    RequestNotificationKind.confirmation_received: (
        "Request completed",
        "Final requester or warehouse confirmation was recorded.",
        "#16a34a",
    ),
    RequestNotificationKind.hold_started: (
        "Request placed on hold",
        "Operational work is paused and the SLA clock is suspended.",
        "#d97706",
    ),
    RequestNotificationKind.resumed: (
        "Request resumed",
        "The operational hold was resolved and work can continue.",
        "#16a34a",
    ),
    RequestNotificationKind.rescheduled: (
        "Request rescheduled",
        "The transport window was revised.",
        "#7c3aed",
    ),
    RequestNotificationKind.failed_delivery: (
        "Transport attempt failed",
        "The delivery or collection attempt failed and requires recovery.",
        "#dc2626",
    ),
    RequestNotificationKind.transport_retry: (
        "Transport retry prepared",
        "The failed attempt was resolved and the request is ready for another transport.",
        "#0284c7",
    ),
    RequestNotificationKind.comment_added: (
        "New request comment",
        "A new comment was added to the request.",
        "#475569",
    ),
    RequestNotificationKind.reservation_cancelled: (
        "Request reservation cancelled",
        "The request or its reserved inventory was cancelled.",
        "#dc2626",
    ),
    RequestNotificationKind.overdue: (
        "Request overdue",
        "The request passed its SLA deadline and needs attention.",
        "#991b1b",
    ),
    RequestNotificationKind.assignment_changed: (
        "Request assignment changed",
        "The assigned warehouse mover changed.",
        "#7c3aed",
    ),
    RequestNotificationKind.coordination_changed: (
        "Request details updated",
        "Coordination details on the request changed.",
        "#475569",
    ),
    RequestNotificationKind.attachment_uploaded: (
        "Supporting attachment added",
        "A supporting non-ERP attachment was added.",
        "#2563eb",
    ),
}


def notification_copy(
    request: BoxRequest,
    warehouse: Warehouse,
    kind: RequestNotificationKind,
    *,
    detail: str | None = None,
) -> tuple[str, str]:
    headline, message, _ = _COPY[kind]
    title = f"{headline}: request #{request.id}"
    body = detail or f"{message} ({warehouse.name})"
    return title, body


def render_request_email(
    *,
    request: BoxRequest,
    warehouse: Warehouse,
    kind: RequestNotificationKind,
    assignee: User | None,
    detail: str | None = None,
) -> RequestEmail:
    headline, default_message, accent = _COPY[kind]
    message = detail or default_message
    base_url = (get_settings().public_base_url or "").rstrip("/")
    deep_link = f"{base_url}/requests/{request.id}" if base_url else ""
    context = {
        "accent": accent,
        "headline": headline,
        "message": message,
        "request_id": request.id,
        "warehouse_name": warehouse.name,
        "status": request.status.value.replace("_", " ").title(),
        "priority": request.priority.value.title(),
        "assigned_to": (
            assignee.display_name or assignee.email if assignee is not None else "Unassigned"
        ),
        "deep_link": deep_link,
    }
    subject = f"[REQUEST] {headline} · #{request.id} · {warehouse.name}"
    return RequestEmail(
        subject=subject,
        html=_ENV.get_template("request_event.html.j2").render(**context),
        text=_ENV.get_template("request_event.txt.j2").render(**context),
    )


__all__ = [
    "RequestEmail",
    "notification_copy",
    "render_request_email",
]
