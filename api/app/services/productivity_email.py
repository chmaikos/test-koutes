"""Render the daily productivity report email.

Kept separate from :mod:`app.services.alert_email` because the report
is not driven by an :class:`Alert` row -- the alert renderer's context
(severity, inventory, threshold, recent box events) doesn't apply. Both
modules share the same Jinja conventions: inline-styled HTML, plain-text
fallback, autoescape on, ``StrictUndefined`` so a missing variable
fails loudly in tests instead of silently rendering an empty span.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.config import get_settings
from app.models.warehouses import Warehouse
from app.services.productivity import WarehouseProductivity

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"


@dataclass(frozen=True)
class ProductivityEmail:
    subject: str
    html: str
    text: str


_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(("html", "j2", "html.j2")),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )
    return _env


def _build_subject(*, warehouse: Warehouse, report_date: date) -> str:
    return f"[Productivity] {warehouse.name} - {report_date.isoformat()}"


def _summary_to_context(summary: WarehouseProductivity) -> dict:
    """Project the dataclass into a plain dict the template iterates over.

    Templates avoid attribute access on Performer/WarehouseProductivity
    so they remain trivial to render against hand-built dicts in tests.
    """
    return {
        "total_pages": summary.total_pages,
        "total_hours": summary.total_hours,
        "avg_pages_per_day": summary.avg_pages_per_day,
        "entry_count": summary.entry_count,
        "active_employees": summary.active_employees,
        "top": [
            {
                "employee_id": p.employee_id,
                "employee_name": p.employee_name,
                "pages": p.pages,
                "hours": p.hours,
                "pages_per_day": p.pages_per_day,
            }
            for p in summary.top
        ],
        "bottom": [
            {
                "employee_id": p.employee_id,
                "employee_name": p.employee_name,
                "pages": p.pages,
                "hours": p.hours,
                "pages_per_day": p.pages_per_day,
            }
            for p in summary.bottom
        ],
    }


def render_productivity_report(
    *,
    warehouse: Warehouse,
    summary: WarehouseProductivity,
    report_date: date,
    base_url: str | None = None,
) -> ProductivityEmail:
    """Render the (subject, html, text) tuple for one warehouse-day.

    Pure function: no DB access, no IO. Callers (:mod:`productivity_dispatch`)
    are responsible for resolving warehouses, computing summaries, and
    actually sending the result.
    """
    env = _get_env()
    if base_url is None:
        base_url = get_settings().public_base_url
    cta_url = f"{base_url.rstrip('/')}/productivity" if base_url else ""
    context = {
        "warehouse": {
            "id": warehouse.id,
            "name": warehouse.name,
            "min_inventory": warehouse.min_inventory,
            "max_capacity": warehouse.max_capacity,
        },
        "summary": _summary_to_context(summary),
        "report_date": report_date.isoformat(),
        "cta_url": cta_url,
    }
    subject = _build_subject(warehouse=warehouse, report_date=report_date)
    html = env.get_template("productivity_daily.html.j2").render(
        subject=subject, **context
    )
    text = env.get_template("productivity_daily.txt.j2").render(
        subject=subject, **context
    )
    return ProductivityEmail(subject=subject, html=html, text=text)


__all__ = ["ProductivityEmail", "render_productivity_report"]
