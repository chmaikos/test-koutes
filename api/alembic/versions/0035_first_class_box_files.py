"""add first-class tracked box files and request snapshots

Revision ID: 0035_first_class_box_files
Revises: 0034_reduced_email_notifications
Create Date: 2026-09-17 12:30:00.000000
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "0035_first_class_box_files"
down_revision = "0034_reduced_email_notifications"
branch_labels = None
depends_on = None

_BOX_ID_LOT_CONSTRAINT = "uq_boxes_id_lot_id"
_SNAPSHOT_KIND_LEGACY = "legacy_contents"


def split_legacy_contents(contents: str | None) -> list[str]:
    """Split the legacy pipe-delimited display field into non-blank segments."""
    if not contents:
        return []
    return [segment.strip() for segment in contents.split("|") if segment.strip()]


def legacy_file_reference(box_number: str, box_id: int, ordinal: int) -> str:
    """Build a deterministic, collision-free reference for a legacy segment."""
    clean_number = " ".join(box_number.split())
    return f"LEGACY-{clean_number}-BOX-{box_id}-FILE-{ordinal}"


def legacy_request_snapshot_reference(request_item_id: int, ordinal: int) -> str:
    return f"LEGACY-REQUEST-ITEM-{request_item_id}-FILE-{ordinal}"


def _add_box_composite_identity() -> None:
    context = op.get_context()
    if context.dialect.name == "sqlite":
        with op.batch_alter_table("boxes") as batch_op:
            batch_op.create_unique_constraint(
                _BOX_ID_LOT_CONSTRAINT,
                ["id", "lot_id"],
            )
        return
    op.create_unique_constraint(
        _BOX_ID_LOT_CONSTRAINT,
        "boxes",
        ["id", "lot_id"],
    )


def _create_box_file_tables() -> None:
    op.create_table(
        "box_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("box_id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=255), nullable=False),
        sa.Column("normalized_reference", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("barcode", sa.String(length=255)),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("archived_by_user_id", sa.Integer()),
        sa.Column("archive_reason", sa.Text()),
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
        sa.CheckConstraint(
            "length(trim(reference)) > 0",
            name="ck_box_files_reference_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(normalized_reference)) > 0",
            name="ck_box_files_normalized_reference_not_blank",
        ),
        sa.CheckConstraint("position > 0", name="ck_box_files_position_positive"),
        sa.CheckConstraint("version > 0", name="ck_box_files_version_positive"),
        sa.CheckConstraint(
            "archive_reason IS NULL OR archived_at IS NOT NULL",
            name="ck_box_files_archive_reason_state",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["lots.id"],
            name="fk_box_files_lot_id_lots",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["box_id", "lot_id"],
            ["boxes.id", "boxes.lot_id"],
            name="fk_box_files_box_lot_boxes",
            ondelete="RESTRICT",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["archived_by_user_id"],
            ["users.id"],
            name="fk_box_files_archived_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_box_files_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_box_files_updated_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "lot_id",
            "normalized_reference",
            name="uq_box_files_lot_normalized_reference",
        ),
        sa.UniqueConstraint(
            "box_id",
            "position",
            name="uq_box_files_box_position",
        ),
    )
    op.create_index(
        "ix_box_files_lot_archived",
        "box_files",
        ["lot_id", "archived_at"],
    )
    op.create_index(
        "ix_box_files_box_archived",
        "box_files",
        ["box_id", "archived_at"],
    )
    op.create_index("ix_box_files_barcode", "box_files", ["barcode"])

    op.create_table(
        "box_file_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "created",
                "updated",
                "moved",
                "archived",
                "restored",
                "box_moved",
                "box_status_changed",
                "lot_reassigned",
                name="box_file_event_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("before_snapshot", sa.JSON()),
        sa.Column("after_snapshot", sa.JSON()),
        sa.Column("actor_user_id", sa.Integer()),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "before_snapshot IS NOT NULL OR after_snapshot IS NOT NULL",
            name="ck_box_file_events_has_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["box_files.id"],
            name="fk_box_file_events_file_id_box_files",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_box_file_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_box_file_events_file_occurred",
        "box_file_events",
        ["file_id", "occurred_at"],
    )
    op.create_index(
        "ix_box_file_events_actor_occurred",
        "box_file_events",
        ["actor_user_id", "occurred_at"],
    )
    op.create_index(
        "ix_box_file_events_type_occurred",
        "box_file_events",
        ["event_type", "occurred_at"],
    )


def _create_request_snapshot_table() -> None:
    op.create_table(
        "box_request_item_file_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_item_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer()),
        sa.Column("reference", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("barcode", sa.String(length=255)),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "snapshot_kind",
            sa.Enum(
                "tracked_file",
                _SNAPSHOT_KIND_LEGACY,
                name="box_request_item_file_snapshot_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(trim(reference)) > 0",
            name="ck_request_item_file_snapshots_reference_not_blank",
        ),
        sa.CheckConstraint(
            "position > 0",
            name="ck_request_item_file_snapshots_position_positive",
        ),
        sa.ForeignKeyConstraint(
            ["request_item_id"],
            ["box_request_items.id"],
            name="fk_request_item_file_snapshots_request_item",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["box_files.id"],
            name="fk_request_item_file_snapshots_file",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "request_item_id",
            "position",
            name="uq_request_item_file_snapshots_position",
        ),
    )
    op.create_index(
        "ix_request_item_file_snapshots_file_id",
        "box_request_item_file_snapshots",
        ["file_id"],
    )
    op.create_index(
        "ix_request_item_file_snapshots_kind",
        "box_request_item_file_snapshots",
        ["snapshot_kind"],
    )


def _backfill_online() -> None:
    bind = op.get_bind()
    boxes = bind.execute(
        sa.text(
            """
            SELECT id, lot_id, box_number, contents
            FROM boxes
            WHERE contents IS NOT NULL
            ORDER BY id
            """
        )
    ).mappings()
    file_rows: list[dict[str, Any]] = []
    for box in boxes:
        for position, description in enumerate(
            split_legacy_contents(box["contents"]),
            start=1,
        ):
            reference = legacy_file_reference(
                str(box["box_number"]),
                int(box["id"]),
                position,
            )
            file_rows.append(
                {
                    "lot_id": int(box["lot_id"]),
                    "box_id": int(box["id"]),
                    "reference": reference,
                    "normalized_reference": reference.lower(),
                    "description": description,
                    "position": position,
                }
            )
    if file_rows:
        bind.execute(
            sa.text(
                """
                INSERT INTO box_files (
                    lot_id,
                    box_id,
                    reference,
                    normalized_reference,
                    description,
                    position
                )
                VALUES (
                    :lot_id,
                    :box_id,
                    :reference,
                    :normalized_reference,
                    :description,
                    :position
                )
                """
            ),
            file_rows,
        )

    request_items = bind.execute(
        sa.text(
            """
            SELECT id, contents
            FROM box_request_items
            WHERE contents IS NOT NULL
            ORDER BY id
            """
        )
    ).mappings()
    snapshot_rows: list[dict[str, Any]] = []
    for item in request_items:
        for position, description in enumerate(
            split_legacy_contents(item["contents"]),
            start=1,
        ):
            snapshot_rows.append(
                {
                    "request_item_id": int(item["id"]),
                    "reference": legacy_request_snapshot_reference(
                        int(item["id"]),
                        position,
                    ),
                    "description": description,
                    "position": position,
                    "snapshot_kind": _SNAPSHOT_KIND_LEGACY,
                }
            )
    if snapshot_rows:
        bind.execute(
            sa.text(
                """
                INSERT INTO box_request_item_file_snapshots (
                    request_item_id,
                    reference,
                    description,
                    position,
                    snapshot_kind
                )
                VALUES (
                    :request_item_id,
                    :reference,
                    :description,
                    :position,
                    :snapshot_kind
                )
                """
            ),
            snapshot_rows,
        )


def _backfill_offline_postgresql() -> None:
    op.execute(
        """
        WITH raw_segments AS (
            SELECT
                b.id AS box_id,
                b.lot_id,
                b.box_number,
                btrim(part.segment) AS description,
                part.source_position
            FROM boxes AS b
            CROSS JOIN LATERAL string_to_table(b.contents, '|')
                WITH ORDINALITY AS part(segment, source_position)
            WHERE btrim(part.segment) <> ''
        ),
        segments AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY box_id ORDER BY source_position
                ) AS position
            FROM raw_segments
        )
        INSERT INTO box_files (
            lot_id,
            box_id,
            reference,
            normalized_reference,
            description,
            position
        )
        SELECT
            lot_id,
            box_id,
            'LEGACY-' ||
                regexp_replace(btrim(box_number), '[[:space:]]+', ' ', 'g') ||
                '-BOX-' || box_id::text || '-FILE-' || position::text,
            lower(
                'LEGACY-' ||
                regexp_replace(btrim(box_number), '[[:space:]]+', ' ', 'g') ||
                '-BOX-' || box_id::text || '-FILE-' || position::text
            ),
            description,
            position
        FROM segments
        """
    )
    op.execute(
        """
        WITH raw_segments AS (
            SELECT
                i.id AS request_item_id,
                btrim(part.segment) AS description,
                part.source_position
            FROM box_request_items AS i
            CROSS JOIN LATERAL string_to_table(i.contents, '|')
                WITH ORDINALITY AS part(segment, source_position)
            WHERE btrim(part.segment) <> ''
        ),
        segments AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY request_item_id ORDER BY source_position
                ) AS position
            FROM raw_segments
        )
        INSERT INTO box_request_item_file_snapshots (
            request_item_id,
            reference,
            description,
            position,
            snapshot_kind
        )
        SELECT
            request_item_id,
            'LEGACY-REQUEST-ITEM-' || request_item_id::text ||
                '-FILE-' || position::text,
            description,
            position,
            'legacy_contents'
        FROM segments
        """
    )


def _backfill() -> None:
    context = op.get_context()
    if not context.as_sql:
        _backfill_online()
    elif context.dialect.name == "postgresql":
        _backfill_offline_postgresql()


def upgrade() -> None:
    _add_box_composite_identity()
    _create_box_file_tables()
    _create_request_snapshot_table()
    _backfill()


def _expected_legacy_state(
    box_rows: list[Mapping[str, Any]],
    request_item_rows: list[Mapping[str, Any]],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    expected_files: list[tuple[Any, ...]] = []
    for box in box_rows:
        for position, description in enumerate(
            split_legacy_contents(box["contents"]),
            start=1,
        ):
            reference = legacy_file_reference(
                str(box["box_number"]),
                int(box["id"]),
                position,
            )
            expected_files.append(
                (
                    int(box["lot_id"]),
                    int(box["id"]),
                    reference,
                    reference.lower(),
                    description,
                    None,
                    position,
                    1,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            )

    expected_snapshots: list[tuple[Any, ...]] = []
    for item in request_item_rows:
        for position, description in enumerate(
            split_legacy_contents(item["contents"]),
            start=1,
        ):
            expected_snapshots.append(
                (
                    int(item["id"]),
                    None,
                    legacy_request_snapshot_reference(int(item["id"]), position),
                    description,
                    None,
                    position,
                    _SNAPSHOT_KIND_LEGACY,
                )
            )
    return expected_files, expected_snapshots


def _assert_safe_downgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        if context.dialect.name == "postgresql":
            op.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM box_file_events)
                       OR EXISTS (
                            WITH raw_segments AS (
                                SELECT
                                    b.id AS box_id,
                                    b.lot_id,
                                    b.box_number,
                                    btrim(part.segment) AS description,
                                    part.source_position
                                FROM boxes AS b
                                CROSS JOIN LATERAL string_to_table(b.contents, '|')
                                    WITH ORDINALITY AS part(segment, source_position)
                                WHERE btrim(part.segment) <> ''
                            ),
                            segments AS (
                                SELECT
                                    *,
                                    row_number() OVER (
                                        PARTITION BY box_id ORDER BY source_position
                                    ) AS position
                                FROM raw_segments
                            ),
                            expected AS (
                                SELECT
                                    lot_id,
                                    box_id,
                                    'LEGACY-' ||
                                        regexp_replace(
                                            btrim(box_number),
                                            '[[:space:]]+',
                                            ' ',
                                            'g'
                                        ) ||
                                        '-BOX-' || box_id::text ||
                                        '-FILE-' || position::text AS reference,
                                    description,
                                    position
                                FROM segments
                            )
                            SELECT 1
                            FROM box_files AS actual
                            FULL OUTER JOIN expected
                                ON expected.box_id = actual.box_id
                               AND expected.position = actual.position
                            WHERE actual.id IS NULL
                               OR expected.box_id IS NULL
                               OR actual.lot_id IS DISTINCT FROM expected.lot_id
                               OR actual.reference IS DISTINCT FROM expected.reference
                               OR actual.normalized_reference IS DISTINCT FROM
                                    lower(expected.reference)
                               OR actual.description IS DISTINCT FROM expected.description
                               OR actual.barcode IS NOT NULL
                               OR actual.version <> 1
                               OR actual.archived_at IS NOT NULL
                               OR actual.archived_by_user_id IS NOT NULL
                               OR actual.archive_reason IS NOT NULL
                               OR actual.created_by_user_id IS NOT NULL
                               OR actual.updated_by_user_id IS NOT NULL
                       )
                       OR EXISTS (
                            WITH raw_segments AS (
                                SELECT
                                    i.id AS request_item_id,
                                    btrim(part.segment) AS description,
                                    part.source_position
                                FROM box_request_items AS i
                                CROSS JOIN LATERAL string_to_table(i.contents, '|')
                                    WITH ORDINALITY AS part(segment, source_position)
                                WHERE btrim(part.segment) <> ''
                            ),
                            expected AS (
                                SELECT
                                    request_item_id,
                                    description,
                                    row_number() OVER (
                                        PARTITION BY request_item_id
                                        ORDER BY source_position
                                    ) AS position
                                FROM raw_segments
                            )
                            SELECT 1
                            FROM box_request_item_file_snapshots AS actual
                            FULL OUTER JOIN expected
                                ON expected.request_item_id = actual.request_item_id
                               AND expected.position = actual.position
                            WHERE actual.id IS NULL
                               OR expected.request_item_id IS NULL
                               OR actual.file_id IS NOT NULL
                               OR actual.reference IS DISTINCT FROM
                                    'LEGACY-REQUEST-ITEM-' ||
                                    expected.request_item_id::text ||
                                    '-FILE-' || expected.position::text
                               OR actual.description IS DISTINCT FROM
                                    expected.description
                               OR actual.barcode IS NOT NULL
                               OR actual.snapshot_kind <> 'legacy_contents'
                       ) THEN
                        RAISE EXCEPTION
                            '0035 downgrade blocked: first-class file data is not '
                            'exactly represented by legacy contents';
                    END IF;
                END
                $$;
                """
            )
        return

    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM box_file_events LIMIT 1")).first():
        raise RuntimeError("0035 downgrade blocked: first-class file audit data exists")

    boxes = list(
        bind.execute(
            sa.text("SELECT id, lot_id, box_number, contents FROM boxes ORDER BY id")
        ).mappings()
    )
    request_items = list(
        bind.execute(sa.text("SELECT id, contents FROM box_request_items ORDER BY id")).mappings()
    )
    expected_files, expected_snapshots = _expected_legacy_state(
        boxes,
        request_items,
    )
    actual_files = list(
        bind.execute(
            sa.text(
                """
                SELECT
                    lot_id,
                    box_id,
                    reference,
                    normalized_reference,
                    description,
                    barcode,
                    position,
                    version,
                    archived_at,
                    archived_by_user_id,
                    archive_reason,
                    created_by_user_id,
                    updated_by_user_id
                FROM box_files
                ORDER BY box_id, position
                """
            )
        ).tuples()
    )
    actual_snapshots = list(
        bind.execute(
            sa.text(
                """
                SELECT
                    request_item_id,
                    file_id,
                    reference,
                    description,
                    barcode,
                    position,
                    snapshot_kind
                FROM box_request_item_file_snapshots
                ORDER BY request_item_id, position
                """
            )
        ).tuples()
    )
    if actual_files != expected_files or actual_snapshots != expected_snapshots:
        raise RuntimeError(
            "0035 downgrade blocked: first-class file data is no longer "
            "exactly represented by legacy contents"
        )


def _drop_box_composite_identity() -> None:
    context = op.get_context()
    if context.dialect.name == "sqlite":
        with op.batch_alter_table("boxes") as batch_op:
            batch_op.drop_constraint(
                _BOX_ID_LOT_CONSTRAINT,
                type_="unique",
            )
        return
    op.drop_constraint(
        _BOX_ID_LOT_CONSTRAINT,
        "boxes",
        type_="unique",
    )


def downgrade() -> None:
    _assert_safe_downgrade()
    op.drop_index(
        "ix_request_item_file_snapshots_kind",
        table_name="box_request_item_file_snapshots",
    )
    op.drop_index(
        "ix_request_item_file_snapshots_file_id",
        table_name="box_request_item_file_snapshots",
    )
    op.drop_table("box_request_item_file_snapshots")
    op.drop_index(
        "ix_box_file_events_type_occurred",
        table_name="box_file_events",
    )
    op.drop_index(
        "ix_box_file_events_actor_occurred",
        table_name="box_file_events",
    )
    op.drop_index(
        "ix_box_file_events_file_occurred",
        table_name="box_file_events",
    )
    op.drop_table("box_file_events")
    op.drop_index("ix_box_files_barcode", table_name="box_files")
    op.drop_index("ix_box_files_box_archived", table_name="box_files")
    op.drop_index("ix_box_files_lot_archived", table_name="box_files")
    op.drop_table("box_files")
    _drop_box_composite_identity()
