"""add warehouse planning settings and recommendation snapshots

Revision ID: 0024_demand_recommendations
Revises: 0023_request_reporting
Create Date: 2026-08-10 22:10:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0024_demand_recommendations"
down_revision = "0023_request_reporting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "warehouses",
        sa.Column("lead_time_days", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "warehouses",
        sa.Column(
            "safety_stock_percent", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "warehouses",
        sa.Column("history_30_weight", sa.Integer(), nullable=False, server_default="70"),
    )
    op.add_column(
        "warehouses",
        sa.Column("history_90_weight", sa.Integer(), nullable=False, server_default="30"),
    )
    op.add_column(
        "warehouses",
        sa.Column("forecast_adjustment", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_warehouses_lead_time_days_range",
        "warehouses",
        "lead_time_days >= 0 AND lead_time_days <= 365",
    )
    op.create_check_constraint(
        "ck_warehouses_safety_stock_percent_range",
        "warehouses",
        "safety_stock_percent >= 0 AND safety_stock_percent <= 500",
    )
    op.create_check_constraint(
        "ck_warehouses_history_weights_range",
        "warehouses",
        "history_30_weight >= 0 AND history_30_weight <= 1000 "
        "AND history_90_weight >= 0 AND history_90_weight <= 1000 "
        "AND history_30_weight + history_90_weight > 0",
    )
    op.create_check_constraint(
        "ck_warehouses_forecast_adjustment_range",
        "warehouses",
        "forecast_adjustment IS NULL OR "
        "(forecast_adjustment >= -5000 AND forecast_adjustment <= 5000)",
    )
    op.add_column(
        "box_requests",
        sa.Column(
            "recommendation_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_box_events_warehouse_type_occurred",
        "box_events",
        ["warehouse_id", "event_type", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_box_events_warehouse_type_occurred",
        table_name="box_events",
    )
    op.drop_column("box_requests", "recommendation_snapshot")
    op.drop_constraint(
        "ck_warehouses_forecast_adjustment_range",
        "warehouses",
        type_="check",
    )
    op.drop_constraint(
        "ck_warehouses_history_weights_range",
        "warehouses",
        type_="check",
    )
    op.drop_constraint(
        "ck_warehouses_safety_stock_percent_range",
        "warehouses",
        type_="check",
    )
    op.drop_constraint(
        "ck_warehouses_lead_time_days_range",
        "warehouses",
        type_="check",
    )
    op.drop_column("warehouses", "forecast_adjustment")
    op.drop_column("warehouses", "history_90_weight")
    op.drop_column("warehouses", "history_30_weight")
    op.drop_column("warehouses", "safety_stock_percent")
    op.drop_column("warehouses", "lead_time_days")
