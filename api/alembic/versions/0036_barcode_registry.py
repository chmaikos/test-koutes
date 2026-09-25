"""add the permanent, globally unique barcode registry

Revision ID: 0036_barcode_registry
Revises: 0035_first_class_box_files
Create Date: 2026-09-18 19:30:00.000000

The registry is deliberately irreversible after any post-migration issuance.
Identity rows and issuance numbers are permanent, including after an entity is
archived, merged, or purged.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0036_barcode_registry"
down_revision = "0035_first_class_box_files"
branch_labels = None
depends_on = None

_KINDS = (
    ("lot", "LOT", "lots"),
    ("pallet", "PAL", "pallets"),
    ("box", "BOX", "boxes"),
    ("file", "FIL", "box_files"),
)
_BACKFILL_REASON = "0036 deterministic backfill"


def _check_digit(number: int) -> int:
    digits = f"{number:012d}"
    total = sum(
        int(digit) * (3 if offset % 2 == 0 else 1)
        for offset, digit in enumerate(reversed(digits))
    )
    return (10 - total % 10) % 10


def _barcode(prefix: str, number: int) -> str:
    return f"{prefix}-{number:012d}-{_check_digit(number)}"


def _create_registry() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        op.execute("CREATE SEQUENCE barcode_identity_number_seq START WITH 1 NO CYCLE")
    else:
        op.create_table(
            "barcode_issuance_counter",
            sa.Column("singleton_id", sa.Integer(), primary_key=True),
            sa.Column("next_number", sa.BigInteger(), nullable=False),
        )
        op.execute(
            "INSERT INTO barcode_issuance_counter (singleton_id, next_number) VALUES (1, 1)"
        )

    op.create_table(
        "barcode_identities",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("entity_kind", sa.String(length=16), nullable=False),
        sa.Column("issuance_number", sa.BigInteger(), nullable=False),
        sa.Column("barcode", sa.String(length=18), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("issued_by_user_id", sa.Integer()),
        sa.Column("issuance_reason", sa.Text()),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("retired_by_user_id", sa.Integer()),
        sa.Column("retirement_reason", sa.Text()),
        sa.Column("retirement_metadata", sa.JSON()),
        sa.CheckConstraint(
            "entity_kind IN ('lot', 'pallet', 'box', 'file')",
            name="ck_barcode_identities_kind",
        ),
        sa.CheckConstraint(
            "issuance_number > 0 AND issuance_number <= 999999999999",
            name="ck_barcode_identities_number_range",
        ),
        sa.CheckConstraint(
            "(retired_at IS NULL AND retired_by_user_id IS NULL "
            "AND retirement_reason IS NULL) OR "
            "(retired_at IS NOT NULL AND retirement_reason IS NOT NULL "
            "AND length(trim(retirement_reason)) > 0)",
            name="ck_barcode_identities_retirement_state",
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_user_id"],
            ["users.id"],
            name="fk_barcode_identities_issued_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retired_by_user_id"],
            ["users.id"],
            name="fk_barcode_identities_retired_by_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("issuance_number", name="uq_barcode_identities_number"),
        sa.UniqueConstraint("barcode", name="uq_barcode_identities_barcode"),
        sa.UniqueConstraint(
            "entity_kind",
            "entity_id",
            name="uq_barcode_identities_kind_entity",
        ),
    )
    op.create_index(
        "ix_barcode_identities_entity",
        "barcode_identities",
        ["entity_kind", "entity_id"],
    )
    op.create_index(
        "ix_barcode_identities_retired",
        "barcode_identities",
        ["retired_at"],
    )
    op.create_table(
        "barcode_file_migration_audit",
        sa.Column("file_id", sa.BigInteger(), primary_key=True),
        sa.Column("identity_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("issuance_number", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("old_barcode", sa.String(length=255)),
        sa.Column("new_barcode", sa.String(length=18), nullable=False, unique=True),
        sa.Column(
            "migrated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def _add_identity_links() -> None:
    context = op.get_context()
    for _, _, table_name in _KINDS:
        constraint = f"fk_{table_name}_barcode_identity"
        unique = f"uq_{table_name}_barcode_identity_id"
        if context.dialect.name == "sqlite":
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.add_column(sa.Column("barcode_identity_id", sa.BigInteger()))
                batch_op.create_foreign_key(
                    constraint,
                    "barcode_identities",
                    ["barcode_identity_id"],
                    ["id"],
                    ondelete="RESTRICT",
                )
                batch_op.create_unique_constraint(unique, ["barcode_identity_id"])
        else:
            op.add_column(
                table_name,
                sa.Column("barcode_identity_id", sa.BigInteger(), nullable=True),
            )
            op.create_foreign_key(
                constraint,
                table_name,
                "barcode_identities",
                ["barcode_identity_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            op.create_unique_constraint(unique, table_name, ["barcode_identity_id"])


def _add_request_snapshot_columns() -> None:
    columns = (
        sa.Column("lot_barcode", sa.String(length=18)),
        sa.Column("pallet_barcode", sa.String(length=18)),
        sa.Column("box_barcode", sa.String(length=18)),
    )
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("box_request_items") as batch_op:
            for column in columns:
                batch_op.add_column(column)
    else:
        for column in columns:
            op.add_column("box_request_items", column)


def _next_number(bind: sa.Connection) -> int:
    if bind.dialect.name == "postgresql":
        value = bind.scalar(sa.text("SELECT nextval('barcode_identity_number_seq')"))
    else:
        value = bind.scalar(
            sa.text(
                "UPDATE barcode_issuance_counter "
                "SET next_number = next_number + 1 "
                "WHERE singleton_id = 1 RETURNING next_number - 1"
            )
        )
    if not isinstance(value, int):
        raise RuntimeError("could not reserve barcode issuance number")
    return value


def _backfill_online() -> None:
    bind = op.get_bind()
    for kind, prefix, table_name in _KINDS:
        columns = "id, barcode" if table_name == "box_files" else "id"
        rows = list(
            bind.execute(
                sa.text(f"SELECT {columns} FROM {table_name} ORDER BY id")  # noqa: S608
            ).mappings()
        )
        for row in rows:
            number = _next_number(bind)
            canonical = _barcode(prefix, number)
            bind.execute(
                sa.text(
                    """
                    INSERT INTO barcode_identities (
                        id, entity_kind, issuance_number, barcode, entity_id,
                        issuance_reason, metadata
                    )
                    VALUES (
                        :id, :kind, :number, :barcode, :entity_id, :reason, :metadata
                    )
                    """
                ),
                {
                    "id": number,
                    "kind": kind,
                    "number": number,
                    "barcode": canonical,
                    "entity_id": int(row["id"]),
                    "reason": _BACKFILL_REASON,
                    "metadata": '{"migration":"0036_barcode_registry"}',
                },
            )
            bind.execute(
                sa.text(
                    f"UPDATE {table_name} SET barcode_identity_id = :identity_id "  # noqa: S608
                    "WHERE id = :entity_id"
                ),
                {"identity_id": number, "entity_id": int(row["id"])},
            )
            if table_name == "box_files":
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO barcode_file_migration_audit (
                            file_id, identity_id, issuance_number, old_barcode, new_barcode
                        ) VALUES (
                            :file_id, :identity_id, :number, :old_barcode, :new_barcode
                        )
                        """
                    ),
                    {
                        "file_id": int(row["id"]),
                        "identity_id": number,
                        "number": number,
                        "old_barcode": row["barcode"],
                        "new_barcode": canonical,
                    },
                )
                bind.execute(
                    sa.text(
                        """
                        UPDATE box_request_item_file_snapshots
                        SET barcode = :barcode
                        WHERE file_id = :file_id
                        """
                    ),
                    {"barcode": canonical, "file_id": int(row["id"])},
                )
    _backfill_request_snapshots()
    _backfill_event_evidence_online()


def _backfill_request_snapshots() -> None:
    for column, kind, id_column in (
        ("lot_barcode", "lot", "lot_id"),
        ("pallet_barcode", "pallet", "pallet_id"),
        ("box_barcode", "box", "box_id"),
    ):
        op.execute(
            f"""
            UPDATE box_request_items
            SET {column} = (
                SELECT identity.barcode
                FROM barcode_identities AS identity
                WHERE identity.entity_kind = '{kind}'
                  AND identity.entity_id = box_request_items.{id_column}
            )
            WHERE {id_column} IS NOT NULL
              AND {column} IS NULL
            """  # noqa: S608
        )


def _migration_kind_for_key(key: str) -> str | None:
    for token in reversed(key.removesuffix("_id").split("_")):
        if token in {"lot", "pallet", "box", "file"}:
            return token
    return None


def _migration_enrich_json(
    value: object,
    barcodes: dict[tuple[str, int], str],
    *,
    primary: tuple[str, int] | None = None,
) -> dict[str, object]:
    if isinstance(value, str):
        value = json.loads(value)
    result = dict(value) if isinstance(value, dict) else {}
    if primary is not None and primary in barcodes:
        result.setdefault(f"{primary[0]}_barcode", barcodes[primary])
        if primary[0] == "file":
            result.setdefault("barcode", barcodes[primary])
    for key, item in list(result.items()):
        if isinstance(item, dict):
            result[key] = _migration_enrich_json(item, barcodes)
        elif isinstance(item, list):
            result[key] = [
                _migration_enrich_json(candidate, barcodes)
                if isinstance(candidate, dict)
                else candidate
                for candidate in item
            ]
        if (
            key.endswith("_id")
            and isinstance(item, int)
            and (kind := _migration_kind_for_key(key)) is not None
            and (barcode := barcodes.get((kind, item))) is not None
        ):
            result.setdefault(f"{key.removesuffix('_id')}_barcode", barcode)
    return result


def _backfill_event_evidence_online() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    barcodes = {
        (str(kind), int(entity_id)): str(barcode)
        for kind, entity_id, barcode in bind.execute(
            sa.text(
                "SELECT entity_kind, entity_id, barcode FROM barcode_identities"
            )
        )
    }
    for table, id_column, kind in (
        ("lot_events", "lot_id", "lot"),
        ("pallet_events", "pallet_id", "pallet"),
        ("box_events", "box_id", "box"),
    ):
        if table not in tables:
            continue
        rows = list(
            bind.execute(
                sa.text(f"SELECT id, {id_column}, metadata FROM {table}")  # noqa: S608
            ).mappings()
        )
        for row in rows:
            enriched = _migration_enrich_json(
                row["metadata"],
                barcodes,
                primary=(kind, int(row[id_column])),
            )
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET metadata = :metadata WHERE id = :id"  # noqa: S608
                ),
                {"metadata": json.dumps(enriched), "id": row["id"]},
            )
    if "box_file_events" in tables:
        rows = list(
            bind.execute(
                sa.text(
                    "SELECT id, file_id, metadata, before_snapshot, after_snapshot "
                    "FROM box_file_events"
                )
            ).mappings()
        )
        for row in rows:
            values: dict[str, object] = {
                "metadata": json.dumps(
                    _migration_enrich_json(
                        row["metadata"],
                        barcodes,
                        primary=("file", int(row["file_id"])),
                    )
                )
            }
            assignments = ["metadata = :metadata"]
            for column in ("before_snapshot", "after_snapshot"):
                if row[column] is not None:
                    values[column] = json.dumps(
                        _migration_enrich_json(
                            row[column],
                            barcodes,
                            primary=("file", int(row["file_id"])),
                        )
                    )
                    assignments.append(f"{column} = :{column}")
            values["id"] = row["id"]
            bind.execute(
                sa.text(
                    "UPDATE box_file_events SET "
                    + ", ".join(assignments)
                    + " WHERE id = :id"
                ),
                values,
            )


def _postgresql_format_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION barcode_registry_format(kind text, number bigint)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        STRICT
        AS $$
            WITH body AS (SELECT lpad(number::text, 12, '0') AS digits),
            checksum AS (
                SELECT (
                    10 - sum(
                        substr(digits, position, 1)::integer *
                        CASE WHEN (12 - position) % 2 = 0 THEN 3 ELSE 1 END
                    ) % 10
                ) % 10 AS digit
                FROM body, generate_series(1, 12) AS position
            )
            SELECT
                CASE kind
                    WHEN 'lot' THEN 'LOT'
                    WHEN 'pallet' THEN 'PAL'
                    WHEN 'box' THEN 'BOX'
                    WHEN 'file' THEN 'FIL'
                END || '-' || (SELECT digits FROM body) || '-' ||
                (SELECT digit::text FROM checksum)
        $$
        """
    )


def _backfill_offline_postgresql() -> None:
    for kind, _, table_name in _KINDS:
        old_barcode = "old_barcode text;" if table_name == "box_files" else ""
        file_before = (
            "SELECT barcode INTO old_barcode FROM box_files WHERE id = entity_row.id;"
            if table_name == "box_files"
            else ""
        )
        file_after = (
            """
            INSERT INTO barcode_file_migration_audit (
                file_id, identity_id, issuance_number, old_barcode, new_barcode
            ) VALUES (
                entity_row.id, number, number, old_barcode, canonical
            );
            UPDATE box_request_item_file_snapshots
            SET barcode = canonical
            WHERE file_id = entity_row.id;
            """
            if table_name == "box_files"
            else ""
        )
        op.execute(
            f"""
            DO $$
            DECLARE
                entity_row record;
                number bigint;
                canonical text;
                {old_barcode}
            BEGIN
                FOR entity_row IN SELECT id FROM {table_name} ORDER BY id LOOP
                    {file_before}
                    number := nextval('barcode_identity_number_seq');
                    canonical := barcode_registry_format('{kind}', number);
                    INSERT INTO barcode_identities (
                        id, entity_kind, issuance_number, barcode, entity_id,
                        issuance_reason, metadata
                    ) VALUES (
                        number, '{kind}', number, canonical, entity_row.id,
                        '{_BACKFILL_REASON}', '{{"migration":"0036_barcode_registry"}}'
                    );
                    UPDATE {table_name}
                    SET barcode_identity_id = number
                    WHERE id = entity_row.id;
                    {file_after}
                END LOOP;
            END
            $$;
            """
        )
    _backfill_request_snapshots()
    for table, id_column, kind in (
        ("lot_events", "lot_id", "lot"),
        ("pallet_events", "pallet_id", "pallet"),
        ("box_events", "box_id", "box"),
    ):
        op.execute(
            f"""
            UPDATE {table} AS event
            SET metadata = (
                COALESCE(event.metadata, '{{}}')::jsonb
                || jsonb_build_object(
                    '{kind}_barcode', identity.barcode
                )
            )::json
            FROM barcode_identities AS identity
            WHERE identity.entity_kind = '{kind}'
              AND identity.entity_id = event.{id_column}
            """  # noqa: S608
        )
    op.execute(
        """
        UPDATE lot_events AS event
        SET metadata = (
            COALESCE(event.metadata, '{}')::jsonb
            || jsonb_strip_nulls(jsonb_build_object(
                'source_lot_barcode', (
                    SELECT barcode FROM barcode_identities
                    WHERE entity_kind = 'lot'
                      AND entity_id = (event.metadata->>'source_lot_id')::bigint
                ),
                'target_lot_barcode', (
                    SELECT barcode FROM barcode_identities
                    WHERE entity_kind = 'lot'
                      AND entity_id = (event.metadata->>'target_lot_id')::bigint
                )
            ))
        )::json
        """
    )
    op.execute(
        """
        UPDATE box_file_events AS event
        SET metadata = (
                COALESCE(event.metadata, '{}')::jsonb
                || jsonb_build_object(
                    'file_barcode', identity.barcode
                )
            )::json,
            before_snapshot = CASE
                WHEN event.before_snapshot IS NULL THEN NULL
                ELSE (
                    event.before_snapshot::jsonb
                    || jsonb_build_object(
                        'barcode', identity.barcode,
                        'file_barcode', identity.barcode,
                        'box_barcode', (
                            SELECT barcode FROM barcode_identities
                            WHERE entity_kind = 'box'
                              AND entity_id = (
                                  event.before_snapshot->>'box_id'
                              )::bigint
                        )
                    )
                )::json
            END,
            after_snapshot = CASE
                WHEN event.after_snapshot IS NULL THEN NULL
                ELSE (
                    event.after_snapshot::jsonb
                    || jsonb_build_object(
                        'barcode', identity.barcode,
                        'file_barcode', identity.barcode,
                        'box_barcode', (
                            SELECT barcode FROM barcode_identities
                            WHERE entity_kind = 'box'
                              AND entity_id = (
                                  event.after_snapshot->>'box_id'
                              )::bigint
                        )
                    )
                )::json
            END
        FROM barcode_identities AS identity
        WHERE identity.entity_kind = 'file'
          AND identity.entity_id = event.file_id
        """
    )


def _validate_and_finalize_schema() -> None:
    bind = None if op.get_context().as_sql else op.get_bind()
    if bind is not None:
        for kind, _, table_name in _KINDS:
            missing = bind.scalar(
                sa.text(
                    f"""
                    SELECT count(*)
                    FROM {table_name} AS entity
                    LEFT JOIN barcode_identities AS identity
                      ON identity.id = entity.barcode_identity_id
                     AND identity.entity_kind = :kind
                     AND identity.entity_id = entity.id
                    WHERE identity.id IS NULL
                    """  # noqa: S608
                ),
                {"kind": kind},
            )
            if missing:
                raise RuntimeError(f"0036 barcode backfill left {missing} uncovered {kind} rows")

    context = op.get_context()
    for _, _, table_name in _KINDS:
        if context.dialect.name == "sqlite":
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.alter_column(
                    "barcode_identity_id",
                    existing_type=sa.BigInteger(),
                    nullable=False,
                )
        else:
            op.alter_column(
                table_name,
                "barcode_identity_id",
                existing_type=sa.BigInteger(),
                nullable=False,
            )

    if context.dialect.name == "sqlite":
        indexes = {
            index["name"] for index in sa.inspect(op.get_bind()).get_indexes("box_files")
        }
        if "ix_box_files_barcode" in indexes:
            op.drop_index("ix_box_files_barcode", table_name="box_files")
        with op.batch_alter_table("box_files") as batch_op:
            batch_op.drop_column("barcode")
    else:
        op.drop_index("ix_box_files_barcode", table_name="box_files")
        op.drop_column("box_files", "barcode")


def _sqlite_expected_barcode(alias: str = "NEW") -> str:
    body = f"printf('%012d', {alias}.issuance_number)"
    terms: list[str] = []
    for position in range(1, 13):
        weight = 3 if (12 - position) % 2 == 0 else 1
        terms.append(f"CAST(substr({body}, {position}, 1) AS INTEGER) * {weight}")
    total = " + ".join(terms)
    prefix = (
        f"CASE {alias}.entity_kind WHEN 'lot' THEN 'LOT' "
        "WHEN 'pallet' THEN 'PAL' WHEN 'box' THEN 'BOX' "
        "WHEN 'file' THEN 'FIL' END"
    )
    return f"{prefix} || '-' || {body} || '-' || ((10 - (({total}) % 10)) % 10)"


def _create_sqlite_triggers() -> None:
    expected = _sqlite_expected_barcode()
    op.execute(
        f"""
        CREATE TRIGGER trg_barcode_identity_validate_insert
        BEFORE INSERT ON barcode_identities
        WHEN NEW.id != NEW.issuance_number OR NEW.barcode != ({expected})
        BEGIN
            SELECT RAISE(ABORT, 'invalid canonical barcode identity');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_box_request_item_barcodes_write_once
        BEFORE UPDATE OF lot_barcode, pallet_barcode, box_barcode
        ON box_request_items
        WHEN (
            OLD.lot_barcode IS NOT NEW.lot_barcode
            AND NOT (
                OLD.lot_barcode IS NULL AND NEW.lot_barcode IS NOT NULL
                AND EXISTS (
                    SELECT 1 FROM barcode_identities
                    WHERE entity_kind = 'lot'
                      AND entity_id = NEW.lot_id
                      AND barcode = NEW.lot_barcode
                )
            )
        ) OR (
            OLD.pallet_barcode IS NOT NEW.pallet_barcode
            AND NOT (
                OLD.pallet_barcode IS NULL AND NEW.pallet_barcode IS NOT NULL
                AND EXISTS (
                    SELECT 1 FROM barcode_identities
                    WHERE entity_kind = 'pallet'
                      AND entity_id = NEW.pallet_id
                      AND barcode = NEW.pallet_barcode
                )
            )
        ) OR (
            OLD.box_barcode IS NOT NEW.box_barcode
            AND NOT (
                OLD.box_barcode IS NULL AND NEW.box_barcode IS NOT NULL
                AND EXISTS (
                    SELECT 1 FROM barcode_identities
                    WHERE entity_kind = 'box'
                      AND entity_id = NEW.box_id
                      AND barcode = NEW.box_barcode
                )
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'request item barcode snapshots are write-once');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_request_file_barcode_write_once
        BEFORE UPDATE OF barcode ON box_request_item_file_snapshots
        WHEN OLD.barcode IS NOT NEW.barcode
          AND NOT (
              OLD.barcode IS NULL AND NEW.barcode IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM barcode_identities
                  WHERE entity_kind = 'file'
                    AND entity_id = NEW.file_id
                    AND barcode = NEW.barcode
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'request file barcode snapshot is write-once');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_barcode_identity_immutable
        BEFORE UPDATE ON barcode_identities
        WHEN OLD.id IS NOT NEW.id
          OR OLD.entity_kind IS NOT NEW.entity_kind
          OR OLD.issuance_number IS NOT NEW.issuance_number
          OR OLD.barcode IS NOT NEW.barcode
          OR OLD.entity_id IS NOT NEW.entity_id
          OR OLD.issued_at IS NOT NEW.issued_at
          OR OLD.issued_by_user_id IS NOT NEW.issued_by_user_id
          OR OLD.issuance_reason IS NOT NEW.issuance_reason
          OR OLD.metadata IS NOT NEW.metadata
          OR (
              OLD.retired_at IS NOT NULL AND (
                  OLD.retired_at IS NOT NEW.retired_at
                  OR OLD.retired_by_user_id IS NOT NEW.retired_by_user_id
                  OR OLD.retirement_reason IS NOT NEW.retirement_reason
                  OR OLD.retirement_metadata IS NOT NEW.retirement_metadata
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'barcode identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_barcode_identity_no_delete
        BEFORE DELETE ON barcode_identities
        BEGIN
            SELECT RAISE(ABORT, 'barcode identities are permanently reserved');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_barcode_file_audit_immutable_update
        BEFORE UPDATE ON barcode_file_migration_audit
        BEGIN
            SELECT RAISE(ABORT, 'barcode migration audit is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_barcode_file_audit_immutable_delete
        BEFORE DELETE ON barcode_file_migration_audit
        BEGIN
            SELECT RAISE(ABORT, 'barcode migration audit is permanent');
        END
        """
    )
    for kind, _, table_name in _KINDS:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_barcode_link_insert
            BEFORE INSERT ON {table_name}
            WHEN NOT EXISTS (
                SELECT 1 FROM barcode_identities
                WHERE id = NEW.barcode_identity_id
                  AND entity_kind = '{kind}'
                  AND entity_id = NEW.id
            )
            BEGIN
                SELECT RAISE(ABORT, 'invalid barcode identity link');
            END
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_barcode_link_immutable
            BEFORE UPDATE OF id, barcode_identity_id ON {table_name}
            WHEN OLD.id IS NOT NEW.id
              OR (
                  OLD.barcode_identity_id IS NOT NULL
                  AND OLD.barcode_identity_id IS NOT NEW.barcode_identity_id
              )
            BEGIN
                SELECT RAISE(ABORT, 'barcode identity link is immutable');
            END
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_barcode_retired_before_delete
            BEFORE DELETE ON {table_name}
            WHEN NOT EXISTS (
                SELECT 1 FROM barcode_identities
                WHERE id = OLD.barcode_identity_id
                  AND entity_kind = '{kind}'
                  AND entity_id = OLD.id
                  AND retired_at IS NOT NULL
            )
            BEGIN
                SELECT RAISE(ABORT, 'barcode identity must be retired before deletion');
            END
            """
        )


def _create_postgresql_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION barcode_registry_identity_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'barcode identities are permanently reserved';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.id <> NEW.issuance_number
                   OR NEW.barcode <> barcode_registry_format(
                        NEW.entity_kind, NEW.issuance_number
                   ) THEN
                    RAISE EXCEPTION 'invalid canonical barcode identity';
                END IF;
                RETURN NEW;
            END IF;
            IF ROW(
                OLD.id, OLD.entity_kind, OLD.issuance_number, OLD.barcode,
                OLD.entity_id, OLD.issued_at, OLD.issued_by_user_id,
                OLD.issuance_reason
            ) IS DISTINCT FROM ROW(
                NEW.id, NEW.entity_kind, NEW.issuance_number, NEW.barcode,
                NEW.entity_id, NEW.issued_at, NEW.issued_by_user_id,
                NEW.issuance_reason
            ) OR OLD.metadata::text IS DISTINCT FROM NEW.metadata::text
              OR (
                OLD.retired_at IS NOT NULL AND (
                    ROW(
                        OLD.retired_at, OLD.retired_by_user_id,
                        OLD.retirement_reason
                    ) IS DISTINCT FROM ROW(
                        NEW.retired_at, NEW.retired_by_user_id,
                        NEW.retirement_reason
                    )
                    OR OLD.retirement_metadata::text IS DISTINCT FROM
                       NEW.retirement_metadata::text
                )
              ) THEN
                RAISE EXCEPTION 'barcode identity is immutable';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION barcode_registry_request_item_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.lot_barcode IS DISTINCT FROM NEW.lot_barcode
               AND NOT (
                   OLD.lot_barcode IS NULL AND NEW.lot_barcode IS NOT NULL
                   AND EXISTS (
                       SELECT 1 FROM barcode_identities
                       WHERE entity_kind = 'lot'
                         AND entity_id = NEW.lot_id
                         AND barcode = NEW.lot_barcode
                   )
               ) THEN
                RAISE EXCEPTION 'request item lot barcode snapshot is write-once';
            END IF;
            IF OLD.pallet_barcode IS DISTINCT FROM NEW.pallet_barcode
               AND NOT (
                   OLD.pallet_barcode IS NULL AND NEW.pallet_barcode IS NOT NULL
                   AND EXISTS (
                       SELECT 1 FROM barcode_identities
                       WHERE entity_kind = 'pallet'
                         AND entity_id = NEW.pallet_id
                         AND barcode = NEW.pallet_barcode
                   )
               ) THEN
                RAISE EXCEPTION 'request item pallet barcode snapshot is write-once';
            END IF;
            IF OLD.box_barcode IS DISTINCT FROM NEW.box_barcode
               AND NOT (
                   OLD.box_barcode IS NULL AND NEW.box_barcode IS NOT NULL
                   AND EXISTS (
                       SELECT 1 FROM barcode_identities
                       WHERE entity_kind = 'box'
                         AND entity_id = NEW.box_id
                         AND barcode = NEW.box_barcode
                   )
               ) THEN
                RAISE EXCEPTION 'request item box barcode snapshot is write-once';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_box_request_item_barcodes_write_once
        BEFORE UPDATE OF lot_barcode, pallet_barcode, box_barcode
        ON box_request_items
        FOR EACH ROW EXECUTE FUNCTION barcode_registry_request_item_guard()
        """
    )
    op.execute(
        """
        CREATE FUNCTION barcode_registry_request_file_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.barcode IS DISTINCT FROM NEW.barcode
               AND NOT (
                   OLD.barcode IS NULL AND NEW.barcode IS NOT NULL
                   AND EXISTS (
                       SELECT 1 FROM barcode_identities
                       WHERE entity_kind = 'file'
                         AND entity_id = NEW.file_id
                         AND barcode = NEW.barcode
                   )
               ) THEN
                RAISE EXCEPTION 'request file barcode snapshot is write-once';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_request_file_barcode_write_once
        BEFORE UPDATE OF barcode ON box_request_item_file_snapshots
        FOR EACH ROW EXECUTE FUNCTION barcode_registry_request_file_guard()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_barcode_identity_guard
        BEFORE INSERT OR UPDATE OR DELETE ON barcode_identities
        FOR EACH ROW EXECUTE FUNCTION barcode_registry_identity_guard()
        """
    )
    op.execute(
        """
        CREATE FUNCTION barcode_registry_link_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            linked_kind text;
            linked_entity_id bigint;
        BEGIN
            IF TG_OP = 'UPDATE'
               AND (
                   OLD.id IS DISTINCT FROM NEW.id
                   OR (
                       OLD.barcode_identity_id IS NOT NULL
                       AND OLD.barcode_identity_id IS DISTINCT FROM
                           NEW.barcode_identity_id
                   )
               ) THEN
                RAISE EXCEPTION 'barcode identity link is immutable';
            END IF;
            SELECT entity_kind, entity_id
            INTO linked_kind, linked_entity_id
            FROM barcode_identities
            WHERE id = NEW.barcode_identity_id;
            IF linked_kind IS DISTINCT FROM TG_ARGV[0]
               OR linked_entity_id IS DISTINCT FROM NEW.id THEN
                RAISE EXCEPTION 'invalid barcode identity link';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION barcode_registry_entity_delete_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM barcode_identities
                WHERE id = OLD.barcode_identity_id
                  AND entity_kind = TG_ARGV[0]
                  AND entity_id = OLD.id
                  AND retired_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'barcode identity must be retired before deletion';
            END IF;
            RETURN OLD;
        END
        $$
        """
    )
    for kind, _, table_name in _KINDS:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_barcode_link_guard
            BEFORE INSERT OR UPDATE OF id, barcode_identity_id ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION barcode_registry_link_guard('{kind}')
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_barcode_retired_before_delete
            BEFORE DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION
                barcode_registry_entity_delete_guard('{kind}')
            """
        )
    op.execute(
        """
        CREATE FUNCTION barcode_registry_audit_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'barcode migration audit is immutable';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_barcode_file_audit_guard
        BEFORE UPDATE OR DELETE ON barcode_file_migration_audit
        FOR EACH ROW EXECUTE FUNCTION barcode_registry_audit_guard()
        """
    )


def upgrade() -> None:
    _create_registry()
    _add_identity_links()
    _add_request_snapshot_columns()
    context = op.get_context()
    if context.dialect.name == "postgresql":
        _postgresql_format_functions()
    if not context.as_sql:
        _backfill_online()
    elif context.dialect.name == "postgresql":
        _backfill_offline_postgresql()
    _validate_and_finalize_schema()
    if context.dialect.name == "postgresql":
        _create_postgresql_triggers()
    elif context.dialect.name == "sqlite":
        _create_sqlite_triggers()


def _assert_safe_downgrade() -> None:
    context = op.get_context()
    if context.as_sql and context.dialect.name == "postgresql":
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM barcode_identities
                    WHERE issuance_reason IS DISTINCT FROM '{_BACKFILL_REASON}'
                       OR retired_at IS NOT NULL
                ) THEN
                    RAISE EXCEPTION
                        '0036 downgrade blocked: post-migration barcode identities exist';
                END IF;
            END
            $$;
            """
        )
        return
    if context.as_sql:
        return
    count = op.get_bind().scalar(
        sa.text(
            """
            SELECT count(*) FROM barcode_identities
            WHERE issuance_reason != :reason
               OR issuance_reason IS NULL
               OR retired_at IS NOT NULL
            """
        ),
        {"reason": _BACKFILL_REASON},
    )
    if count:
        raise RuntimeError(
            "0036 downgrade blocked: post-migration barcode identities have been issued"
        )


def _drop_triggers() -> None:
    context = op.get_context()
    if context.dialect.name == "sqlite":
        names = [
            "trg_barcode_identity_validate_insert",
            "trg_barcode_identity_immutable",
            "trg_barcode_identity_no_delete",
            "trg_barcode_file_audit_immutable_update",
            "trg_barcode_file_audit_immutable_delete",
            "trg_box_request_item_barcodes_write_once",
            "trg_request_file_barcode_write_once",
        ]
        for _, _, table_name in _KINDS:
            names.extend(
                [
                    f"trg_{table_name}_barcode_link_insert",
                    f"trg_{table_name}_barcode_link_immutable",
                    f"trg_{table_name}_barcode_retired_before_delete",
                ]
            )
        for name in names:
            op.execute(f"DROP TRIGGER {name}")
    elif context.dialect.name == "postgresql":
        for _, _, table_name in _KINDS:
            op.execute(f"DROP TRIGGER trg_{table_name}_barcode_link_guard ON {table_name}")
            op.execute(
                f"DROP TRIGGER trg_{table_name}_barcode_retired_before_delete "
                f"ON {table_name}"
            )
        op.execute("DROP TRIGGER trg_barcode_identity_guard ON barcode_identities")
        op.execute(
            "DROP TRIGGER trg_barcode_file_audit_guard ON barcode_file_migration_audit"
        )
        op.execute(
            "DROP TRIGGER trg_box_request_item_barcodes_write_once "
            "ON box_request_items"
        )
        op.execute(
            "DROP TRIGGER trg_request_file_barcode_write_once "
            "ON box_request_item_file_snapshots"
        )
        op.execute("DROP FUNCTION barcode_registry_link_guard()")
        op.execute("DROP FUNCTION barcode_registry_entity_delete_guard()")
        op.execute("DROP FUNCTION barcode_registry_identity_guard()")
        op.execute("DROP FUNCTION barcode_registry_audit_guard()")
        op.execute("DROP FUNCTION barcode_registry_request_item_guard()")
        op.execute("DROP FUNCTION barcode_registry_request_file_guard()")


def downgrade() -> None:
    _assert_safe_downgrade()
    _drop_triggers()
    context = op.get_context()

    op.add_column("box_files", sa.Column("barcode", sa.String(length=255)))
    op.execute(
        """
        UPDATE box_files
        SET barcode = (
            SELECT old_barcode
            FROM barcode_file_migration_audit
            WHERE file_id = box_files.id
        )
        """
    )
    op.create_index("ix_box_files_barcode", "box_files", ["barcode"])

    if context.dialect.name == "sqlite":
        with op.batch_alter_table("box_request_items") as batch_op:
            batch_op.drop_column("box_barcode")
            batch_op.drop_column("pallet_barcode")
            batch_op.drop_column("lot_barcode")
    else:
        op.drop_column("box_request_items", "box_barcode")
        op.drop_column("box_request_items", "pallet_barcode")
        op.drop_column("box_request_items", "lot_barcode")

    for _, _, table_name in reversed(_KINDS):
        constraint = f"fk_{table_name}_barcode_identity"
        unique = f"uq_{table_name}_barcode_identity_id"
        if context.dialect.name == "sqlite":
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.drop_constraint(unique, type_="unique")
                batch_op.drop_constraint(constraint, type_="foreignkey")
                batch_op.drop_column("barcode_identity_id")
        else:
            op.drop_constraint(unique, table_name, type_="unique")
            op.drop_constraint(constraint, table_name, type_="foreignkey")
            op.drop_column(table_name, "barcode_identity_id")

    op.drop_table("barcode_file_migration_audit")
    op.drop_index("ix_barcode_identities_retired", table_name="barcode_identities")
    op.drop_index("ix_barcode_identities_entity", table_name="barcode_identities")
    # Trigger removal is the only point at which migration-owned identities may
    # be deleted. Later identities make this downgrade permanently unavailable.
    op.drop_table("barcode_identities")
    if context.dialect.name == "postgresql":
        op.execute("DROP FUNCTION barcode_registry_format(text, bigint)")
        op.execute("DROP SEQUENCE barcode_identity_number_seq")
    else:
        op.drop_table("barcode_issuance_counter")
