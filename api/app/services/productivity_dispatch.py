"""Send the per-warehouse end-of-day productivity report.

The dispatcher is intentionally separate from the rendering code so the
tests can verify routing/idempotency without exercising Jinja, and the
renderer can be unit-tested without faking a Graph client.

Idempotency is enforced by the ``productivity_report_runs`` table: a
successful row for ``(warehouse_id, report_date)`` blocks a re-send,
even if the scheduler tick fires twice (which can happen after a
restart inside the report window). Failed sends do *not* block a retry
-- they record the failure for diagnosis but the next run will try
again, which is what an operator paging through Graph errors expects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.employees import ProductivityReportRun
from app.models.warehouses import Warehouse
from app.services.acl import allowed_warehouse_ids  # noqa: F401  (re-export for tests)
from app.services.alert_recipients import primary_recipients
from app.services.graph_email import send_alert_email
from app.services.productivity import (
    WarehouseProductivity,
    daily_summary,
    empty_summary,
)
from app.services.productivity_email import render_productivity_report

logger = logging.getLogger("warehouse.productivity")


@dataclass
class DispatchOutcome:
    """Aggregated result of a single ``send_daily_reports`` invocation."""

    sent: int = 0
    skipped_already_sent: int = 0
    skipped_no_recipients: int = 0
    skipped_empty: int = 0
    failed: int = 0


def _existing_run(
    db: Session, *, warehouse_id: int, report_date: date
) -> ProductivityReportRun | None:
    return db.get(ProductivityReportRun, (warehouse_id, report_date))


def _record_run(
    db: Session,
    *,
    warehouse_id: int,
    report_date: date,
    ok: bool,
    recipients: list[str],
    error: str | None,
) -> None:
    """Upsert a run row.

    A failed send overwrites the previous failure (so the latest error
    is what the audit row shows) but a successful row is locked in --
    ``send_daily_reports`` checks for ``ok=True`` *before* even building
    the email so a successful run cannot be overwritten by a later
    failure on the same day.
    """
    existing = _existing_run(
        db, warehouse_id=warehouse_id, report_date=report_date
    )
    recipient_str = ", ".join(recipients)[:2000]
    if existing is None:
        db.add(
            ProductivityReportRun(
                warehouse_id=warehouse_id,
                report_date=report_date,
                ok=ok,
                recipients=recipient_str,
                error=(error or None) if not ok else None,
            )
        )
    else:
        existing.ok = ok
        existing.recipients = recipient_str
        existing.error = (error or None) if not ok else None
    db.commit()


def send_daily_reports(
    db: Session,
    *,
    on_date: date,
) -> DispatchOutcome:
    """Send one productivity report email per warehouse for ``on_date``.

    Iterates every warehouse in the database (the scheduler ignores
    per-user ACL: every warehouse should report regardless of who
    happens to be logged in). For each one we:

    1. Skip if a successful run already exists for this date.
    2. Compute the day's summary; skip silently when there are no
       entries unless ``PRODUCTIVITY_REPORT_INCLUDE_EMPTY=true``.
    3. Resolve recipients via :func:`primary_recipients` (same routing
       as triggered alert mails).
    4. Render + send via :func:`send_alert_email`. Both success and
       failure are recorded in ``productivity_report_runs``.

    Returns a :class:`DispatchOutcome` with per-bucket counts so a
    cron-job inspector can tell at a glance whether the run was healthy.
    """
    outcome = DispatchOutcome()
    settings = get_settings()
    if not settings.graph_configured:
        logger.info(
            "graph not configured; skipping daily productivity reports"
        )
        return outcome

    warehouses = db.scalars(select(Warehouse).order_by(Warehouse.id)).all()
    if not warehouses:
        return outcome

    summaries = daily_summary(db, on_date=on_date)

    for wh in warehouses:
        run = _existing_run(db, warehouse_id=wh.id, report_date=on_date)
        if run is not None and run.ok:
            outcome.skipped_already_sent += 1
            continue

        summary: WarehouseProductivity = summaries.get(
            wh.id
        ) or empty_summary(wh.id)
        if (
            summary.entry_count == 0
            and not settings.productivity_report_include_empty
        ):
            outcome.skipped_empty += 1
            continue

        recipients = primary_recipients(db, wh.id)
        if not recipients:
            outcome.skipped_no_recipients += 1
            _record_run(
                db,
                warehouse_id=wh.id,
                report_date=on_date,
                ok=False,
                recipients=[],
                error="no recipients",
            )
            continue

        email = render_productivity_report(
            warehouse=wh, summary=summary, report_date=on_date
        )
        ok, error = send_alert_email(
            subject=email.subject, html_body=email.html, to=recipients
        )
        _record_run(
            db,
            warehouse_id=wh.id,
            report_date=on_date,
            ok=ok,
            recipients=recipients,
            error=error,
        )
        if ok:
            outcome.sent += 1
        else:
            outcome.failed += 1
            logger.warning(
                "productivity report failed for warehouse %s: %s",
                wh.id,
                error,
            )

    return outcome


def send_daily_reports_safe(db: Session, *, on_date: date) -> DispatchOutcome:
    """Best-effort wrapper for the scheduler -- never raises."""
    try:
        return send_daily_reports(db, on_date=on_date)
    except Exception:  # pragma: no cover - defensive
        logger.exception("productivity report dispatch failed")
        return DispatchOutcome()


__all__ = [
    "DispatchOutcome",
    "send_daily_reports",
    "send_daily_reports_safe",
]
