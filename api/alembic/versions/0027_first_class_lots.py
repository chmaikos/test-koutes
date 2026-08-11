"""promote lot names to first-class audited records

Revision ID: 0027_first_class_lots
Revises: 0026_request_workflow_final
Create Date: 2026-08-11 19:45:00.000000
"""

from __future__ import annotations

from collections import Counter

import sqlalchemy as sa

from alembic import op

revision = "0027_first_class_lots"
down_revision = "0026_request_workflow_final"
branch_labels = None
depends_on = None

_POSTGRES_CLEAN_NAME = (
    "COALESCE(btrim(regexp_replace({column}, '[[:space:]]+', ' ', 'g'), ' '), '')"
)


def _clean_name(value: str | None) -> str:
    return " ".join(value.split()) if value is not None else ""


def _normalized_name(value: str | None) -> str:
    return _clean_name(value).lower()


def _create_tables() -> None:
    op.create_table(
        "lots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("normalized_name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by_user_id", sa.Integer()),
        sa.Column("updated_by_user_id", sa.Integer()),
        sa.CheckConstraint("name <> ''", name="ck_lots_name_not_blank"),
        sa.CheckConstraint(
            "normalized_name <> ''",
            name="ck_lots_normalized_name_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_lots_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_lots_updated_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("normalized_name", name="uq_lots_normalized_name"),
    )
    op.create_table(
        "lot_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "created",
                "renamed",
                "reassigned",
                name="lot_event_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("old_name", sa.String(length=64)),
        sa.Column("new_name", sa.String(length=64)),
        sa.Column("actor_user_id", sa.Integer()),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["lots.id"],
            name="fk_lot_events_lot_id_lots",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_lot_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_lot_events_lot_occurred",
        "lot_events",
        ["lot_id", "occurred_at"],
    )
    op.create_index(
        "ix_lot_events_actor_occurred",
        "lot_events",
        ["actor_user_id", "occurred_at"],
    )


def _postgresql_backfill() -> None:
    box_display = _POSTGRES_CLEAN_NAME.format(column="b.lot")
    box_normalized = f"lower({box_display})"
    item_display = _POSTGRES_CLEAN_NAME.format(column="i.lot")
    item_normalized = f"lower({item_display})"

    # No archived-row filter is intentional: archived inventory participates
    # in the same permanent (lot_id, box_number) identity.
    op.execute(
        f"""
        DO $$
        DECLARE
            blank_count INTEGER;
            conflicts TEXT;
        BEGIN
            SELECT COUNT(*) INTO blank_count
            FROM boxes b
            WHERE {box_display} = '';

            IF blank_count > 0 THEN
                RAISE EXCEPTION
                    'First-class lots migration blocked: % box row(s) have blank lot names.',
                    blank_count
                    USING HINT =
                        'Run api/scripts/lot_migration_preflight.py and repair blank lots.';
            END IF;

            SELECT string_agg(
                format('%s / box_number=%s (%s rows)', normalized_name, box_number, row_count),
                '; ' ORDER BY normalized_name, box_number
            )
            INTO conflicts
            FROM (
                SELECT
                    {box_normalized} AS normalized_name,
                    b.box_number,
                    COUNT(*) AS row_count
                FROM boxes b
                GROUP BY {box_normalized}, b.box_number
                HAVING COUNT(*) > 1
            ) duplicate_boxes;

            IF conflicts IS NOT NULL THEN
                RAISE EXCEPTION
                    'First-class lots migration blocked by duplicate box identities: %',
                    conflicts
                    USING HINT =
                        'Run api/scripts/lot_migration_preflight.py and reconcile every '
                        'reported normalized lot + box_number pair, including archived rows.';
            END IF;
        END
        $$;
        """
    )

    # Boxes win spelling ties, then the oldest row. Request snapshots supply
    # names only when no box currently represents that normalized lot.
    op.execute(
        f"""
        WITH source_names AS (
            SELECT
                0 AS source_rank,
                b.id AS source_id,
                {box_display} AS display_name,
                {box_normalized} AS normalized_name
            FROM boxes b
            UNION ALL
            SELECT
                1 AS source_rank,
                i.id AS source_id,
                {item_display} AS display_name,
                {item_normalized} AS normalized_name
            FROM box_request_items i
            WHERE i.lot IS NOT NULL
              AND {item_display} <> ''
        ),
        ranked_names AS (
            SELECT
                display_name,
                normalized_name,
                row_number() OVER (
                    PARTITION BY normalized_name
                    ORDER BY source_rank, source_id
                ) AS spelling_rank
            FROM source_names
        )
        INSERT INTO lots (name, normalized_name, version)
        SELECT display_name, normalized_name, 1
        FROM ranked_names
        WHERE spelling_rank = 1
        ORDER BY normalized_name;
        """
    )
    op.execute(
        f"""
        UPDATE boxes b
        SET lot_id = l.id
        FROM lots l
        WHERE l.normalized_name = {box_normalized};
        """
    )
    op.execute(
        f"""
        UPDATE box_request_items i
        SET lot_id = COALESCE(
            (
                SELECT l.id
                FROM lots l
                WHERE i.lot IS NOT NULL
                  AND l.normalized_name = {item_normalized}
            ),
            (SELECT b.lot_id FROM boxes b WHERE b.id = i.box_id)
        )
        WHERE i.box_id IS NOT NULL OR i.lot IS NOT NULL;
        """
    )
    op.execute(
        """
        INSERT INTO lot_events (
            lot_id,
            event_type,
            old_name,
            new_name,
            actor_user_id,
            reason,
            metadata
        )
        SELECT
            id,
            'created',
            NULL,
            name,
            NULL,
            'Backfilled from legacy lot text during first-class lots migration.',
            '{"source":"migration_0027"}'
        FROM lots
        ORDER BY id;
        """
    )


def _sqlite_backfill() -> None:
    """Backfill SQLite test databases with the same Python normalization rule."""
    context = op.get_context()
    if context.as_sql:
        raise RuntimeError(
            "SQLite offline SQL is not a deployment artifact; generate offline SQL "
            "with the PostgreSQL DATABASE_URL used in production."
        )

    bind = op.get_bind()
    box_rows = list(
        bind.execute(
            sa.text("SELECT id, lot, box_number FROM boxes ORDER BY id")
        ).mappings()
    )
    blank_box_ids = [row["id"] for row in box_rows if not _clean_name(row["lot"])]
    if blank_box_ids:
        raise RuntimeError(
            "First-class lots migration blocked: blank lot names on box ids "
            f"{blank_box_ids}. Run api/scripts/lot_migration_preflight.py."
        )

    identities = Counter(
        (_normalized_name(row["lot"]), row["box_number"]) for row in box_rows
    )
    conflicts = sorted(key for key, count in identities.items() if count > 1)
    if conflicts:
        rendered = ", ".join(
            f"{normalized!r} / box_number={box_number!r}"
            for normalized, box_number in conflicts
        )
        raise RuntimeError(
            "First-class lots migration blocked by duplicate box identities: "
            f"{rendered}. Run api/scripts/lot_migration_preflight.py; archived rows count."
        )

    item_rows = list(
        bind.execute(
            sa.text(
                "SELECT id, box_id, lot FROM box_request_items ORDER BY id"
            )
        ).mappings()
    )
    spellings: dict[str, tuple[int, int, str]] = {}
    for source_rank, rows in ((0, box_rows), (1, item_rows)):
        for row in rows:
            raw_name = row["lot"]
            if raw_name is None or not _clean_name(raw_name):
                continue
            display_name = _clean_name(raw_name)
            normalized_name = _normalized_name(raw_name)
            candidate = (source_rank, row["id"], display_name)
            if normalized_name not in spellings or candidate < spellings[normalized_name]:
                spellings[normalized_name] = candidate

    for normalized_name in sorted(spellings):
        display_name = spellings[normalized_name][2]
        bind.execute(
            sa.text(
                """
                INSERT INTO lots (name, normalized_name, version)
                VALUES (:name, :normalized_name, 1)
                """
            ),
            {"name": display_name, "normalized_name": normalized_name},
        )

    lot_ids = {
        row["normalized_name"]: row["id"]
        for row in bind.execute(
            sa.text("SELECT id, normalized_name FROM lots")
        ).mappings()
    }
    box_lot_ids: dict[int, int] = {}
    for row in box_rows:
        lot_id = lot_ids[_normalized_name(row["lot"])]
        box_lot_ids[row["id"]] = lot_id
        bind.execute(
            sa.text("UPDATE boxes SET lot_id = :lot_id WHERE id = :box_id"),
            {"lot_id": lot_id, "box_id": row["id"]},
        )
    for row in item_rows:
        lot_id = None
        if row["lot"] and _clean_name(row["lot"]):
            lot_id = lot_ids[_normalized_name(row["lot"])]
        if lot_id is None:
            lot_id = box_lot_ids.get(row["box_id"])
        if lot_id is not None:
            bind.execute(
                sa.text(
                    "UPDATE box_request_items SET lot_id = :lot_id WHERE id = :item_id"
                ),
                {"lot_id": lot_id, "item_id": row["id"]},
            )
    bind.execute(
        sa.text(
            """
            INSERT INTO lot_events (
                lot_id, event_type, new_name, reason, metadata
            )
            SELECT
                id,
                'created',
                name,
                'Backfilled from legacy lot text during first-class lots migration.',
                '{"source":"migration_0027"}'
            FROM lots
            ORDER BY id
            """
        )
    )


def _cut_over_postgresql() -> None:
    op.create_foreign_key(
        "fk_boxes_lot_id_lots",
        "boxes",
        "lots",
        ["lot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_box_request_items_lot_id_lots",
        "box_request_items",
        "lots",
        ["lot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_boxes_lot_id", "boxes", ["lot_id"])
    op.create_index(
        "ix_box_request_items_lot_id",
        "box_request_items",
        ["lot_id"],
    )
    op.drop_constraint("uq_boxes_lot_box_number", "boxes", type_="unique")
    op.create_unique_constraint(
        "uq_boxes_lot_box_number",
        "boxes",
        ["lot_id", "box_number"],
    )
    op.alter_column(
        "boxes",
        "lot_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("boxes", "lot")


def _cut_over_sqlite() -> None:
    with op.batch_alter_table("boxes") as batch_op:
        batch_op.drop_constraint("uq_boxes_lot_box_number", type_="unique")
        batch_op.create_foreign_key(
            "fk_boxes_lot_id_lots",
            "lots",
            ["lot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_boxes_lot_box_number",
            ["lot_id", "box_number"],
        )
        batch_op.alter_column(
            "lot_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.drop_column("lot")
    op.create_index("ix_boxes_lot_id", "boxes", ["lot_id"])

    with op.batch_alter_table("box_request_items") as batch_op:
        batch_op.create_foreign_key(
            "fk_box_request_items_lot_id_lots",
            "lots",
            ["lot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_box_request_items_lot_id",
        "box_request_items",
        ["lot_id"],
    )


def upgrade() -> None:
    dialect_name = op.get_context().dialect.name
    if dialect_name == "postgresql":
        op.execute(
            "ALTER TYPE box_event_type ADD VALUE IF NOT EXISTS 'lot_reassigned'"
        )
    _create_tables()
    op.add_column("boxes", sa.Column("lot_id", sa.Integer(), nullable=True))
    op.add_column(
        "box_request_items",
        sa.Column("lot_id", sa.Integer(), nullable=True),
    )

    if dialect_name == "sqlite":
        _sqlite_backfill()
        _cut_over_sqlite()
    else:
        _postgresql_backfill()
        _cut_over_postgresql()


def _downgrade_postgresql() -> None:
    op.add_column("boxes", sa.Column("lot", sa.String(length=64), nullable=True))
    op.execute(
        """
        UPDATE boxes b
        SET lot = l.name
        FROM lots l
        WHERE l.id = b.lot_id
        """
    )
    op.alter_column(
        "boxes",
        "lot",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_constraint("uq_boxes_lot_box_number", "boxes", type_="unique")
    op.create_unique_constraint(
        "uq_boxes_lot_box_number",
        "boxes",
        ["lot", "box_number"],
    )
    op.drop_index("ix_boxes_lot_id", table_name="boxes")
    op.drop_constraint(
        "fk_boxes_lot_id_lots",
        "boxes",
        type_="foreignkey",
    )
    op.drop_column("boxes", "lot_id")

    op.drop_index("ix_box_request_items_lot_id", table_name="box_request_items")
    op.drop_constraint(
        "fk_box_request_items_lot_id_lots",
        "box_request_items",
        type_="foreignkey",
    )
    op.drop_column("box_request_items", "lot_id")


def _downgrade_sqlite() -> None:
    op.add_column("boxes", sa.Column("lot", sa.String(length=64), nullable=True))
    op.execute(
        """
        UPDATE boxes
        SET lot = (SELECT lots.name FROM lots WHERE lots.id = boxes.lot_id)
        """
    )
    op.drop_index("ix_boxes_lot_id", table_name="boxes")
    with op.batch_alter_table("boxes") as batch_op:
        batch_op.drop_constraint("uq_boxes_lot_box_number", type_="unique")
        batch_op.create_unique_constraint(
            "uq_boxes_lot_box_number",
            ["lot", "box_number"],
        )
        batch_op.alter_column(
            "lot",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.drop_constraint(
            "fk_boxes_lot_id_lots",
            type_="foreignkey",
        )
        batch_op.drop_column("lot_id")

    op.drop_index(
        "ix_box_request_items_lot_id",
        table_name="box_request_items",
    )
    with op.batch_alter_table("box_request_items") as batch_op:
        batch_op.drop_constraint(
            "fk_box_request_items_lot_id_lots",
            type_="foreignkey",
        )
        batch_op.drop_column("lot_id")


def downgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        _downgrade_sqlite()
    else:
        _downgrade_postgresql()

    op.drop_index("ix_lot_events_actor_occurred", table_name="lot_events")
    op.drop_index("ix_lot_events_lot_occurred", table_name="lot_events")
    op.drop_table("lot_events")
    op.drop_table("lots")
