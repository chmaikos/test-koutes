from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import SessionLocal
from app.services.alerts import evaluate_safe

logger = logging.getLogger("warehouse.scheduler")


def _tick() -> None:
    db = SessionLocal()
    try:
        evaluate_safe(db)
    finally:
        db.close()


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _tick,
        trigger="interval",
        seconds=60,
        id="alerts-tick",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
