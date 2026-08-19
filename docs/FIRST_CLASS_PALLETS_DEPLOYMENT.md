# First-class Pallets migration deployment

Migration `0032_first_class_pallets` adds durable Pallets between Lots and
Boxes. It creates `pallets` and `pallet_events`, adds nullable `boxes.pallet_id`,
adds immutable `box_request_items.pallet_id`/`pallet` snapshots, records Pallet
data in Lot purge audits, extends Box event types, and changes Box/request-item
item descriptions from 200-character strings to text. It intentionally does
not invent Pallets for legacy inventory.

## Release contract

- Legacy rows may keep `pallet_id = NULL` and appear as **Unassigned**.
- The matching API requires a Pallet for every new manual, XLSX, staged,
  direct-inbound, and request-completion receipt.
- A Box can reference only an active Pallet in the same Lot and warehouse.
- Old API instances must not write while the database/API/web versions are
  being switched.
- PostgreSQL is the production migration target. SQLite is an online
  compatibility path for migration tests, not a production offline artifact.

## Safe production upgrade

1. Announce the write outage and stop/drain all API instances, workers, and
   schedulers.
2. Back up PostgreSQL and RustFS as one recoverable pair and test that the
   database backup can be opened.
3. Confirm a single Alembic head:

   ```bash
   cd api
   uv run alembic heads
   ```

4. Generate and retain PostgreSQL offline SQL for review:

   ```bash
   uv run alembic upgrade head --sql > /tmp/warehouse-upgrade.sql
   test -s /tmp/warehouse-upgrade.sql
   ```

5. Apply the migration against the intended PostgreSQL database:

   ```bash
   uv run alembic upgrade head
   uv run alembic current
   ```

6. Before allowing writes, run the read-only Pallet preflight against the
   migrated database:

   ```bash
   uv run python scripts/pallet_preflight.py \
     --database-url "$DATABASE_URL"
   ```

7. Review every reported group: legacy Unassigned Boxes, missing references,
   Lot/warehouse mismatches, inactive assignments, and duplicate normalized
   Pallet numbers. The script never changes data. Exit code `0` means no
   integrity conflicts; legacy Unassigned inventory is reported separately and
   remains valid for compatibility.
8. Deploy/restart the matching API, then the matching web build. Smoke-test
   manual receipt, XLSX mapping, inbound completion, Pallet list/detail, move,
   and CSV/XLSX export before reopening writes.

Do not deploy the new web/API against a database below
`0032_first_class_pallets`, and do not restart an old API after the migration.

## Temporary PostgreSQL rehearsal

Never rehearse downgrade against user data. One disposable local example is:

```bash
docker run --rm --name pallet-migration-postgres \
  -e POSTGRES_DB=pallet_migration_test \
  -e POSTGRES_USER=pallet_test \
  -e POSTGRES_PASSWORD=pallet_test \
  -p 55432:5432 -d postgres:16-alpine
cd api
export DATABASE_URL='postgresql+psycopg://pallet_test:pallet_test@localhost:55432/pallet_migration_test'
uv run alembic upgrade head
uv run python scripts/pallet_preflight.py
uv run alembic downgrade 0031_return_target_warehouse
uv run alembic upgrade head
cd ..
docker stop pallet-migration-postgres
```

Adapt credentials/port to the disposable environment. Confirm the database name
before every downgrade.

## Rollback constraints

Stop all matching API writers before rollback. Downgrade to
`0031_return_target_warehouse` is refused if any of the following exists:

- a Pallet row or Box assignment,
- a request-item Pallet ID/number snapshot,
- a Lot purge audit that recorded Pallets,
- Box or request-item item-description text longer than 200 characters.

These guards prevent silent loss of identity, audit history, request history,
or truncation when restoring the old schema. Reconcile or restore from the
paired backup instead of bypassing the guard. PostgreSQL enum labels
`pallet_assigned` and `pallet_unassigned` remain as harmless unused labels
because removing enum values is not a safe in-place operation.

The downgrade removes request-item and Box Pallet foreign keys/indexes before
dropping Pallet event/table state, restores the old item-description type, and
then removes Pallet fields from the purge ledger. After a successful rollback,
deploy only the matching old API/web build.

## Verification checklist

- `alembic heads` prints exactly `0032_first_class_pallets`.
- PostgreSQL offline SQL is nonblank and contains both new tables, both Box
  event labels, request snapshots, purge-audit fields, and foreign keys.
- The migration-specific SQLite upgrade/downgrade test passes.
- The disposable PostgreSQL upgrade → preflight → guarded downgrade → re-upgrade
  sequence passes.
- Preflight reports no mismatched, inactive, duplicate, or missing-reference
  assignments.
- Legacy null assignments remain visible as **Unassigned**.
- New receipt paths reject a missing Pallet.
- API tests, Ruff, frontend tests/lint/typecheck/build, and `git diff --check`
  all pass before release.
