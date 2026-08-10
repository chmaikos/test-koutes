from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.services.alerts import dispatch_safe, evaluate_safe
from app.services.productivity_dispatch import send_daily_reports_safe
from app.services.request_notifications import dispatch_request_email_safe

logger = logging.getLogger("warehouse.scheduler")


def _eval_tick() -> None:
    db = SessionLocal()
    try:
        evaluate_safe(db)
    finally:
        db.close()


def _dispatch_tick() -> None:
    db = SessionLocal()
    try:
        dispatch_safe(db)
    finally:
        db.close()


def _request_dispatch_tick() -> None:
    db = SessionLocal()
    try:
        dispatch_request_email_safe(db)
    finally:
        db.close()


def _resolve_app_tz() -> ZoneInfo:
    """Resolve ``APP_TIMEZONE`` to a real ``ZoneInfo`` (UTC on bad input).

    The cron trigger is one of the few places we *must* have a real
    timezone object: APScheduler interprets the bare hour against this
    tz to decide when the job fires. Falling back to UTC silently keeps
    a misconfigured deployment running -- the report just lands at UTC
    18:00 instead of local 18:00 -- rather than failing to start.
    """
    name = get_settings().app_timezone or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "unknown APP_TIMEZONE %r; falling back to UTC for scheduler", name
        )
        return ZoneInfo("UTC")


def _productivity_tick() -> None:
    """Send the per-warehouse productivity report for *today* (local).

    "Today" is computed in ``APP_TIMEZONE`` so a cron firing at local
    18:00 sends the report for the day that just ended in that zone,
    matching what operators saw on the dashboard.
    """
    tz = _resolve_app_tz()
    today = datetime.now(tz).date()
    db = SessionLocal()
    try:
        outcome = send_daily_reports_safe(db, on_date=today)
        logger.info(
            "productivity report run for %s: sent=%s failed=%s "
            "skipped_empty=%s skipped_already_sent=%s skipped_no_recipients=%s",
            today,
            outcome.sent,
            outcome.failed,
            outcome.skipped_empty,
            outcome.skipped_already_sent,
            outcome.skipped_no_recipients,
        )
    finally:
        db.close()


def build_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")
    # Detection runs every minute -- it only touches the DB and is also
    # called inline on every box mutation, so this is just a safety net
    # for cases where the request hook is bypassed (e.g. an admin override).
    scheduler.add_job(
        _eval_tick,
        trigger="interval",
        seconds=60,
        id="alerts-eval-tick",
        max_instances=1,
        coalesce=True,
    )
    # Email dispatch runs less often: it makes outbound HTTP calls to
    # Graph and exists primarily to deliver triggered/reminder/escalation
    # mail without slowing down the request that opened the alert. 5 min
    # is a good compromise between latency and not flooding Graph when a
    # tenant has hundreds of warehouses.
    scheduler.add_job(
        _dispatch_tick,
        trigger="interval",
        seconds=300,
        id="alerts-dispatch-tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _request_dispatch_tick,
        trigger="interval",
        seconds=60,
        id="request-notifications-dispatch-tick",
        max_instances=1,
        coalesce=True,
    )
    # End-of-day productivity report. Fires once per day at the
    # configured local hour; the dispatch service is idempotent so a
    # duplicate firing on restart inside the report window is harmless.
    productivity_hour = max(0, min(23, settings.productivity_report_hour))
    scheduler.add_job(
        _productivity_tick,
        trigger=CronTrigger(
            hour=productivity_hour, minute=0, timezone=_resolve_app_tz()
        ),
        id="productivity-daily-report",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


# Re-export UTC so tests can monkey-patch ``datetime.now`` consistently
# without re-importing it from the stdlib in test files.
__all__ = ["UTC", "build_scheduler"]
