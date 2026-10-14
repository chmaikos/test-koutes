"""Send the per-warehouse end-of-day productivity report.

The dispatcher is intentionally separate from the rendering code so the
tests can verify routing/idempotency without exercising Jinja, and the
renderer can be unit-tested without faking a Graph client.

Idempotency is enforced by the ``productivity_report_runs`` table: a
successful row for ``(warehouse_id, report_date)`` blocks a re-send,
even if the scheduler tick fires twice (which can happen after a
restart inside the report window). A committed lease prevents concurrent
workers sending the same report. Automatic retries use capped exponential
backoff; explicit calls can retry immediately. An expired claim can recover
a crashed worker. Graph acceptance followed by a crash before finalization
can still cause duplicate delivery because Graph provides no idempotency key.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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
    skipped_claimed: int = 0


def _existing_run(
    db: Session, *, warehouse_id: int, report_date: date
) -> ProductivityReportRun | None:
    return db.get(ProductivityReportRun, (warehouse_id, report_date))


CLAIM_LEASE = timedelta(minutes=15)
MAX_AUTOMATIC_ATTEMPTS = 8


def _claim_run(
    db: Session, *, warehouse_id: int, report_date: date,
    now: datetime, due_only: bool = False,
) -> str | None:
    """Commit an atomic, fenced lease before any network send.

    Concurrent initial inserts are protected by the composite primary key;
    conditional updates prevent a second worker taking a live claim.
    """
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(insert(ProductivityReportRun).values(
        warehouse_id=warehouse_id, report_date=report_date,
        ok=False, recipients="", attempts=0,
    ).on_conflict_do_nothing(index_elements=["warehouse_id", "report_date"]))
    eligible = [
        ProductivityReportRun.warehouse_id == warehouse_id,
        ProductivityReportRun.report_date == report_date,
        ProductivityReportRun.ok.is_(False),
        or_(ProductivityReportRun.claimed_at.is_(None),
            ProductivityReportRun.claimed_at <= now - CLAIM_LEASE),
    ]
    if due_only:
        eligible.extend([
            ProductivityReportRun.attempts < MAX_AUTOMATIC_ATTEMPTS,
            or_(ProductivityReportRun.next_attempt_at.is_(None),
                ProductivityReportRun.next_attempt_at <= now),
        ])
    token = str(uuid4())
    result = db.execute(update(ProductivityReportRun).where(*eligible).values(
        claim_token=token, claimed_at=now,
        attempts=ProductivityReportRun.attempts + 1,
    ).execution_options(synchronize_session=False))
    db.commit()
    return token if result.rowcount == 1 else None


def _finish_run(
    db: Session, *, warehouse_id: int, report_date: date,
    claim_token: str, now: datetime, ok: bool,
    recipients: list[str], error: str | None,
) -> bool:
    """Only the current lease owner can write its delivery result."""
    predicate = (
        ProductivityReportRun.warehouse_id == warehouse_id,
        ProductivityReportRun.report_date == report_date,
        ProductivityReportRun.claim_token == claim_token,
        ProductivityReportRun.ok.is_(False),
    )
    attempts = db.scalar(select(ProductivityReportRun.attempts).where(*predicate))
    if attempts is None:
        db.rollback()
        return False
    delay = min(3600, 60 * (2 ** min(attempts - 1, 6)))
    result = db.execute(update(ProductivityReportRun).where(*predicate).values(
        ok=ok, recipients=", ".join(recipients)[:2000],
        error=None if ok else (error or "delivery failed")[:500],
        sent_at=now, claimed_at=None, claim_token=None,
        next_attempt_at=None if ok else now + timedelta(seconds=delay),
    ).execution_options(synchronize_session=False))
    db.commit()
    return result.rowcount == 1


def send_daily_reports(
    db: Session,
    *,
    on_date: date,
    due_only: bool = False,
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
    4. Acquire a durable lease, then render/send without DB locks held across
       Graph delivery. Record success/failure only while owning that lease.

    ``due_only=True`` enforces automatic retry backoff and the daily attempt
    cap. The default allows explicit retries without bypassing live claims
    or successful delivery records.

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

    warehouses = db.scalars(
        select(Warehouse)
        .where(Warehouse.is_active.is_(True))
        .order_by(Warehouse.id)
    ).all()
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

        now = datetime.now(UTC)
        claim_token = _claim_run(
            db, warehouse_id=wh.id, report_date=on_date, now=now, due_only=due_only,
        )
        if claim_token is None:
            outcome.skipped_claimed += 1
            continue

        recipients: list[str] = []
        try:
            recipients = primary_recipients(db, wh.id)
            if not recipients:
                ok, error = False, "no recipients"
            else:
                email = render_productivity_report(
                    warehouse=wh, summary=summary, report_date=on_date
                )
                # End DB reads before blocking on Graph; the durable lease
                # remains committed throughout delivery.
                db.commit()
                ok, error = send_alert_email(
                    subject=email.subject, html_body=email.html, to=recipients
                )
        except Exception as exc:
            db.rollback()
            logger.exception("productivity report failed for warehouse %s", wh.id)
            ok, error = False, str(exc)
        finalized = _finish_run(
            db, warehouse_id=wh.id, report_date=on_date,
            claim_token=claim_token, now=datetime.now(UTC),
            ok=ok, recipients=recipients, error=error,
        )
        if not finalized:
            outcome.skipped_claimed += 1
            continue
        if not recipients:
            outcome.skipped_no_recipients += 1
            continue
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


def send_daily_reports_safe(
    db: Session, *, on_date: date, due_only: bool = False
) -> DispatchOutcome:
    """Best-effort wrapper for the scheduler -- never raises."""
    try:
        return send_daily_reports(db, on_date=on_date, due_only=due_only)
    except Exception:  # pragma: no cover - defensive
        db.rollback()
        logger.exception("productivity report dispatch failed")
        return DispatchOutcome()


__all__ = [
    "DispatchOutcome",
    "send_daily_reports",
    "send_daily_reports_safe",
]
