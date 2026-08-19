# First-class Pallets migration deployment

Migration `0032_first_class_pallets` adds durable Pallets between Lots and
Boxes. Migration `0033_organizational_pallets` removes physical warehouse
ownership from those Pallets. A Pallet is now a Lot-scoped organizational
identity; its current warehouse distribution is derived from assigned Boxes,
and one Pallet may contain Boxes in several warehouses.

`0032` creates `pallets` and `pallet_events`, adds nullable `boxes.pallet_id`,
adds immutable `box_request_items.pallet_id`/`pallet` snapshots, records Pallet
data in Lot purge audits, extends Box event types, and changes Box/request-item
item descriptions from 200-character strings to text. It intentionally does
not invent Pallets for legacy inventory. Do not rewrite `0032`; `0033` is the
forward correction for its original `current_warehouse_id` contract.

## Release contract

- Legacy rows may keep `pallet_id = NULL` and appear as **Unassigned**.
- The matching API requires a Pallet for every new manual, XLSX, staged,
  direct-inbound, and request-completion receipt.
- A Box can reference only an active Pallet in the same Lot. Warehouse moves,
  force/bulk corrections, returns, inbound relocation, and restoration preserve
  that Pallet; Lot reassignment detaches or explicitly reassigns it.
- `warehouse_id` on Pallet creation is authorization context only. It is not
  stored as Pallet location.
- Non-admin identity visibility requires accessible Box or staged Lot context;
  all Pallet counts and warehouse names are derived from ACL-visible Boxes.
- Old API instances must not write while the database/API/web versions are
  being switched.
- PostgreSQL is the production target. Migration `0033` also supports an online
  SQLite upgrade from a `0032` schema and isolated SQLite offline generation;
  both paths must retain all Pallet constraints, foreign keys, unique
  constraints, and non-warehouse indexes during batch recreation.

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

4. Generate and retain the full PostgreSQL upgrade plus isolated `0033`
   PostgreSQL/SQLite upgrade and downgrade SQL for review. The revision range is
   intentional: older migrations contain PostgreSQL-only DDL, so a full-history
   SQLite `--sql` run is not a valid `0033` verification.

   ```bash
   uv run alembic upgrade head --sql > /tmp/warehouse-upgrade.sql
   uv run alembic \
     upgrade 0032_first_class_pallets:0033_organizational_pallets --sql \
     > /tmp/pallet-0033-upgrade-postgresql.sql
   uv run alembic \
     downgrade 0033_organizational_pallets:0032_first_class_pallets --sql \
     > /tmp/pallet-0033-downgrade-postgresql.sql
   DATABASE_URL=sqlite:////tmp/pallet-offline.db \
     uv run alembic \
       upgrade 0032_first_class_pallets:0033_organizational_pallets --sql \
       > /tmp/pallet-0033-upgrade-sqlite.sql
   DATABASE_URL=sqlite:////tmp/pallet-offline.db \
     uv run alembic \
       downgrade 0033_organizational_pallets:0032_first_class_pallets --sql \
       > /tmp/pallet-0033-downgrade-sqlite.sql
   test -s /tmp/warehouse-upgrade.sql
   test -s /tmp/pallet-0033-upgrade-postgresql.sql
   test -s /tmp/pallet-0033-downgrade-postgresql.sql
   test -s /tmp/pallet-0033-upgrade-sqlite.sql
   test -s /tmp/pallet-0033-downgrade-sqlite.sql
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
   Lot mismatches, inactive assignments, duplicate normalized Pallet numbers,
   invalid merged-Lot parents, and each Pallet's Box-derived warehouse
   distribution. A multi-warehouse distribution is valid after `0033`. The
   script never changes data. Exit code `0` means no integrity conflicts;
   legacy Unassigned inventory remains valid.
8. Deploy/restart the matching API, then the matching web build. Smoke-test
   manual receipt, XLSX mapping, existing-Box inbound relocation, Pallet
   list/detail across multiple warehouses, Box movement with preserved Pallet,
   return completion, Lot merge, and CSV/XLSX export before reopening writes.

Do not deploy the new web/API against a database below
`0033_organizational_pallets`, and do not restart an old API after migration.

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
uv run alembic downgrade 0032_first_class_pallets
uv run alembic upgrade head
uv run alembic downgrade 0031_return_target_warehouse
uv run alembic upgrade head
cd ..
docker stop pallet-migration-postgres
```

Adapt credentials/port to the disposable environment. Confirm the database name
before every downgrade.

## Rollback constraints

Stop all matching API writers before rollback. The first rollback step,
`0033 → 0032`, reconstructs `pallets.current_warehouse_id` from assigned Boxes.
It is executable and data-preserving only when **every** Pallet:

- has at least one assigned Box, and
- has assigned Boxes in exactly one distinct warehouse.

The downgrade refuses both empty Pallets and Pallets distributed across
multiple warehouses. Do not bypass this guard: reconcile while running the
matching 0033 application or restore the paired backup. For a safe shape, the
downgrade repopulates the warehouse column before making it non-null, restores
its foreign key/index, and preserves all other constraints, indexes, IDs,
assignments, and audit data.

The subsequent `0032 → 0031` downgrade is refused if any of the following
exists:

- a Pallet row or Box assignment,
- a request-item Pallet ID/number snapshot,
- a Lot purge audit that recorded Pallets,
- Box or request-item item-description text longer than 200 characters.

These guards prevent silent loss of location reconstruction, identity, audit
history, request history, or text truncation. Reconcile or restore from the
paired backup instead of bypassing them. PostgreSQL enum labels
`pallet_assigned` and `pallet_unassigned` remain as harmless unused labels
because removing enum values is not a safe in-place operation.

The downgrade removes request-item and Box Pallet foreign keys/indexes before
dropping Pallet event/table state, restores the old item-description type, and
then removes Pallet fields from the purge ledger. After a successful rollback,
deploy only the matching old API/web build.

## Verification checklist

- `alembic heads` prints exactly `0033_organizational_pallets`.
- PostgreSQL and SQLite offline SQL are nonblank. `0033` SQL drops the Pallet
  warehouse column on upgrade and contains executable empty/multi-warehouse
  guards plus reconstruction on downgrade.
- SQLite batch-recreation tests retain every Pallet check, unique constraint,
  foreign key, and non-warehouse index, and restore the warehouse foreign
  key/index on a safe downgrade.
- Disposable PostgreSQL and SQLite upgrade → preflight → guarded downgrade →
  re-upgrade sequences pass; unsafe empty and multi-warehouse fixtures refuse
  downgrade.
- Preflight reports no mismatched, inactive, duplicate, or missing-reference
  assignments and reports Box-derived warehouse sets without treating
  multi-warehouse Pallets as conflicts.
- Legacy null assignments remain visible as **Unassigned**.
- New receipt paths reject a missing Pallet.
- Pallet options are Lot-scoped and reusable across receipt warehouses.
- The legacy Pallet move endpoint returns HTTP 410 and performs no mutation.
- API tests, Ruff, frontend tests/lint/typecheck/build, and `git diff --check`
  all pass before release.
