"""Render alert emails (subject + HTML + plain-text) from Jinja templates.

Kept deliberately free of HTTP / SQLAlchemy concerns so the renderer is easy
to unit-test in isolation. The higher-level ``render_for_alert`` helper in this
module pulls the bits of context it needs (warehouse, recent box events,
acknowledger) out of the database and hands plain values to ``render``.

Email HTML uses inline styles and table-based layout because that is the only
markup that survives every major mail client (Outlook in particular). The
plain-text body is a real fallback, not just stripped HTML, because some clients
(and screen readers) prefer it.
"""
from __future__ import annotations

import enum
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.alerts import Alert, AlertType
from app.models.boxes import Box, BoxEvent
from app.models.users import User
from app.models.warehouses import Warehouse

logger = logging.getLogger("warehouse.alert_email")

# Templates live next to the `app` package on disk so this works in both the
# pip-installed Docker layout and the source-checkout layout the tests use.
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"


class EmailKind(str, enum.Enum):
    triggered = "triggered"
    reminder = "reminder"
    resolved = "resolved"
    escalated = "escalated"
    test = "test"


@dataclass(frozen=True)
class AlertEmail:
    subject: str
    html: str
    text: str


@dataclass(frozen=True)
class _Severity:
    """Visual + verbal severity styling for the email header strip."""

    label: str  # short uppercase label rendered in the strip
    prefix: str  # subject-line prefix (square brackets included)
    bg: str  # CSS hex for the strip background
    fg: str  # CSS hex for the strip text colour
    bar: str  # CSS hex for the inventory bar fill


# Severity colour palette. Keep these explicit hex values rather than relying
# on a CSS framework -- email clients strip <link> and most <style> blocks.
_SEV_ACTION = _Severity("Action required", "[ACTION]", "#dc2626", "#ffffff", "#dc2626")
_SEV_HEADS_UP = _Severity("Heads up", "[HEADS UP]", "#f59e0b", "#1e293b", "#f59e0b")
_SEV_ATTENTION = _Severity("Attention", "[ATTENTION]", "#0284c7", "#ffffff", "#0284c7")
_SEV_REMINDER = _Severity("Reminder", "[REMINDER]", "#ea580c", "#ffffff", "#ea580c")
_SEV_ESCALATED = _Severity("Escalated", "[ESCALATED]", "#991b1b", "#ffffff", "#991b1b")
_SEV_RESOLVED = _Severity("Resolved", "[RESOLVED]", "#16a34a", "#ffffff", "#16a34a")
_SEV_TEST = _Severity("Test", "[TEST]", "#475569", "#ffffff", "#475569")


def _base_severity_for_type(alert_type: AlertType) -> _Severity:
    """Severity used for the *triggered* mail of each alert type.

    Reminder/escalated/resolved overlay their own severity on top of this;
    near_/box_stuck variants land in HEADS UP / ATTENTION instead of ACTION
    so operators can tell a leading indicator from a fire.
    """
    name = alert_type.value
    if name in ("low_inventory", "max_capacity"):
        return _SEV_ACTION
    if name in ("near_capacity", "near_low_inventory"):
        return _SEV_HEADS_UP
    if name == "box_stuck":
        return _SEV_ATTENTION
    return _SEV_ACTION  # defensive default for any future enum value


def _severity_for(kind: EmailKind, alert_type: AlertType) -> _Severity:
    if kind == EmailKind.test:
        return _SEV_TEST
    if kind == EmailKind.resolved:
        return _SEV_RESOLVED
    if kind == EmailKind.escalated:
        return _SEV_ESCALATED
    if kind == EmailKind.reminder:
        return _SEV_REMINDER
    return _base_severity_for_type(alert_type)


_TYPE_HEADLINE: dict[str, str] = {
    "low_inventory": "{warehouse} is below minimum inventory",
    "max_capacity": "{warehouse} is at or over capacity",
    "near_capacity": "{warehouse} is nearing capacity",
    "near_low_inventory": "{warehouse} is approaching minimum inventory",
    "box_stuck": "Boxes are stuck in {warehouse}",
}

# A short imperative sentence telling the recipient what is expected of them.
# Tier 4 alert types are listed even though they are not yet emitted by the
# evaluator; that way the renderer is ready when those values arrive.
_TYPE_ACTION: dict[str, str] = {
    "low_inventory": (
        "Receive new boxes to bring this warehouse above the minimum, "
        "or lower the threshold if this is intentional."
    ),
    "max_capacity": (
        "Move boxes out of this warehouse or raise its capacity. "
        "Receiving new boxes here will fail until inventory drops."
    ),
    "near_capacity": (
        "Capacity is close. Plan to redirect incoming boxes to a different "
        "warehouse or move some out before it goes red."
    ),
    "near_low_inventory": (
        "Inventory is close to the minimum. Schedule a top-up before the "
        "low-inventory alert fires."
    ),
    "box_stuck": (
        "These boxes have been in 'received' for too long. "
        "Process or return them, or contact the owner."
    ),
}


def _summary_line(alert: Alert) -> str:
    """Single-line value/threshold for the subject and the summary card."""
    name = alert.type.value
    if name == "max_capacity":
        return f"{alert.value}/{alert.threshold}"
    if name == "low_inventory":
        return f"{alert.value} (minimum {alert.threshold})"
    if name == "near_capacity":
        pct = _percent(alert.value, alert.threshold)
        return f"{alert.value}/{alert.threshold} ({pct}%)"
    if name == "near_low_inventory":
        return f"{alert.value} (minimum {alert.threshold})"
    if name == "box_stuck":
        return f"{alert.value} stuck box(es)"
    return f"{alert.value}/{alert.threshold}"


def _percent(value: int, threshold: int) -> int:
    if threshold <= 0:
        return 0 if value <= 0 else 100
    return max(0, min(round(value * 100 / threshold), 999))


def _render_text_bar(value: int, threshold: int, *, width: int = 24) -> str:
    """Plain-text progress bar, e.g. ``[#########---------------]``."""
    pct = _percent(value, threshold)
    filled = max(0, min(width, round(width * pct / 100)))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _format_event(ev: BoxEvent, box_number: str, warehouses: dict[int, str]) -> dict[str, Any]:
    """Turn a BoxEvent into a small dict the templates can iterate over.

    Templates stay free of SQLAlchemy attribute access, which keeps them
    cheap to render in unit tests with hand-built dicts.
    """
    name = ev.event_type.value

    def wname(wid: int | None) -> str | None:
        if wid is None:
            return None
        return warehouses.get(wid, f"#{wid}")

    if name == "created":
        line = f"Received at {wname(ev.to_warehouse_id) or '?'}"
    elif name == "moved":
        line = (
            f"Moved from {wname(ev.from_warehouse_id) or '?'} "
            f"to {wname(ev.to_warehouse_id) or '?'}"
        )
    elif name == "status_changed":
        line = (
            f"Status: {ev.from_status.value if ev.from_status else '?'} -> "
            f"{ev.to_status.value if ev.to_status else '?'}"
        )
    elif name == "returned":
        line = "Returned"
    else:
        line = name
    return {
        "event_type": name,
        "box_number": box_number,
        "occurred_at": ev.occurred_at,
        "line": line,
    }


def _recent_events_for_warehouse(
    db: Session, warehouse_id: int, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Last N box events on the given warehouse, formatted for templates."""
    rows = db.execute(
        select(BoxEvent, Box.box_number)
        .join(Box, Box.id == BoxEvent.box_id)
        .where(BoxEvent.warehouse_id == warehouse_id)
        .order_by(BoxEvent.occurred_at.desc(), BoxEvent.id.desc())
        .limit(limit)
    ).all()
    if not rows:
        return []
    warehouse_names = {
        wid: name
        for wid, name in db.execute(select(Warehouse.id, Warehouse.name)).all()
    }
    return [_format_event(ev, box_number, warehouse_names) for ev, box_number in rows]


# ---------------------------------------------------------------------------
# Jinja environment
# ---------------------------------------------------------------------------


def _build_environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "j2", "html.j2")),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    env.filters["shortdt"] = _filter_shortdt
    return env


def _filter_shortdt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M UTC")


_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = _build_environment()
    return _env


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------


def _build_subject(
    *,
    kind: EmailKind,
    alert: Alert,
    warehouse: Warehouse,
) -> str:
    severity = _severity_for(kind, alert.type)
    summary = _summary_line(alert)
    name = alert.type.value
    if name == "max_capacity":
        body = f"{warehouse.name} over capacity ({summary})"
    elif name == "low_inventory":
        body = f"{warehouse.name} below minimum ({summary})"
    elif name == "near_capacity":
        body = f"{warehouse.name} nearing capacity ({summary})"
    elif name == "near_low_inventory":
        body = f"{warehouse.name} near minimum inventory ({summary})"
    elif name == "box_stuck":
        body = f"{summary} stuck in {warehouse.name}"
    else:
        body = f"{warehouse.name} alert ({summary})"
    return f"{severity.prefix} {body}"


def _build_context(
    *,
    kind: EmailKind,
    alert: Alert,
    warehouse: Warehouse,
    recent_events: Sequence[dict[str, Any]],
    acknowledged_by: User | None,
    base_url: str,
) -> dict[str, Any]:
    severity = _severity_for(kind, alert.type)
    name = alert.type.value
    headline = _TYPE_HEADLINE.get(name, "Warehouse alert").format(
        warehouse=warehouse.name
    )
    action = _TYPE_ACTION.get(name, "Review the warehouse and take appropriate action.")
    cta_url = f"{base_url.rstrip('/')}/alerts/{alert.id}" if base_url else ""
    pct = _percent(alert.value, alert.threshold) if alert.threshold > 0 else 0
    bar_pct = max(0, min(100, pct))
    return {
        "kind": kind.value,
        "severity": {
            "label": severity.label,
            "prefix": severity.prefix,
            "bg": severity.bg,
            "fg": severity.fg,
            "bar": severity.bar,
        },
        "alert": {
            "id": alert.id,
            "type": name,
            "value": alert.value,
            "threshold": alert.threshold,
            "triggered_at": alert.triggered_at,
            "resolved_at": alert.resolved_at,
            "acknowledged_at": alert.acknowledged_at,
            "summary": _summary_line(alert),
        },
        "warehouse": {
            "id": warehouse.id,
            "name": warehouse.name,
            "min_inventory": warehouse.min_inventory,
            "max_capacity": warehouse.max_capacity,
        },
        "headline": headline,
        "action": action,
        "percent": pct,
        "bar_percent": bar_pct,
        "text_bar": _render_text_bar(alert.value, alert.threshold),
        "recent_events": list(recent_events),
        "acknowledged_by": (
            {
                "display_name": acknowledged_by.display_name or acknowledged_by.email,
                "email": acknowledged_by.email,
            }
            if acknowledged_by is not None
            else None
        ),
        "cta_url": cta_url,
    }


def render(
    *,
    kind: EmailKind,
    alert: Alert,
    warehouse: Warehouse,
    recent_events: Sequence[dict[str, Any]] = (),
    acknowledged_by: User | None = None,
    base_url: str = "",
) -> AlertEmail:
    """Render the email for a given alert + kind.

    Returns the subject, fully-styled HTML body, and a plain-text fallback.
    Pure: no DB, no IO. ``recent_events`` is expected as a list of dicts
    (see :func:`_recent_events_for_warehouse`).
    """
    env = _get_env()
    context = _build_context(
        kind=kind,
        alert=alert,
        warehouse=warehouse,
        recent_events=recent_events,
        acknowledged_by=acknowledged_by,
        base_url=base_url,
    )
    subject = _build_subject(kind=kind, alert=alert, warehouse=warehouse)
    html_template = env.get_template(f"{kind.value}.html.j2")
    text_template = env.get_template(f"{kind.value}.txt.j2")
    html = html_template.render(subject=subject, **context)
    text = text_template.render(subject=subject, **context)
    return AlertEmail(subject=subject, html=html, text=text)


def render_for_alert(
    db: Session,
    *,
    kind: EmailKind,
    alert: Alert,
    base_url: str | None = None,
) -> AlertEmail | None:
    """Convenience wrapper that pulls warehouse/events/ack-user from the DB.

    Returns ``None`` if the alert's warehouse no longer exists -- callers
    should treat that as "skip this email"; the alert row itself remains.
    """
    warehouse = db.get(Warehouse, alert.warehouse_id)
    if warehouse is None:
        logger.warning(
            "alert %s references missing warehouse %s; skipping email",
            alert.id,
            alert.warehouse_id,
        )
        return None
    recent_events = _recent_events_for_warehouse(db, warehouse.id, limit=5)
    ack_user: User | None = None
    if alert.acknowledged_by_user_id is not None:
        ack_user = db.get(User, alert.acknowledged_by_user_id)
    if base_url is None:
        base_url = get_settings().public_base_url
    return render(
        kind=kind,
        alert=alert,
        warehouse=warehouse,
        recent_events=recent_events,
        acknowledged_by=ack_user,
        base_url=base_url or "",
    )
