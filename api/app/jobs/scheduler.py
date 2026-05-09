from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import SessionLocal
from app.services.alerts import dispatch_safe, evaluate_safe

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


def build_scheduler() -> AsyncIOScheduler:
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
    return scheduler
