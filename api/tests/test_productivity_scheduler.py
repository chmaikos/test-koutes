"""Daily-report recovery respects local time and schedules bounded retries."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.jobs import scheduler
from app.services.productivity_dispatch import DispatchOutcome


def test_report_tick_only_runs_after_configured_local_hour(monkeypatch):
    settings = SimpleNamespace(app_timezone="Europe/Athens", productivity_report_hour=18)
    monkeypatch.setattr(scheduler, "get_settings", lambda: settings)
    current = [datetime(2026, 10, 14, 17, 59, tzinfo=ZoneInfo("Europe/Athens"))]

    class Clock:
        @staticmethod
        def now(tz):
            return current[0].astimezone(tz)

    monkeypatch.setattr(scheduler, "datetime", Clock)
    calls = []

    class Session:
        def close(self):
            calls.append("closed")

    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    def send(db, **kwargs):
        calls.append(kwargs)
        return DispatchOutcome()

    monkeypatch.setattr(scheduler, "send_daily_reports_safe", send)
    scheduler._productivity_tick()
    assert calls == []
    current[0] = current[0].replace(hour=18, minute=5)
    scheduler._productivity_tick()
    assert calls == [{"on_date": current[0].date(), "due_only": True}, "closed"]


def test_scheduler_registers_minute_report_recovery():
    built = scheduler.build_scheduler()
    jobs = {job.id: job for job in built.get_jobs()}
    assert "productivity-daily-report" in jobs
    recovery = jobs["productivity-report-retry"]
    assert recovery.trigger.interval.total_seconds() == 60
    assert recovery.max_instances == 1
