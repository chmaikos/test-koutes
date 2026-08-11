# First-class lots migration deployment

Migration `0027_first_class_lots` replaces the mutable `boxes.lot` text column
with the required `boxes.lot_id` foreign key. This is a coordinated column
cutover, not an expand/contract migration.

Before deployment, run the read-only preflight against the production database:

```bash
cd api
uv run python scripts/lot_migration_preflight.py --database-url "$DATABASE_URL"
```

The command checks active and archived boxes. Exit code `2` means migration
`0027` will intentionally refuse to run: reconcile blank lot names and every
duplicate `(normalized lot name, box_number)` pair it reports. Different
display spellings in one normalized group are not blockers; the migration
deterministically keeps the spelling from the oldest box, falling back to the
oldest request-item snapshot.

For the release:

1. Stop all old API instances and background workers.
2. Confirm the preflight exits successfully.
3. Apply Alembic migration `0027_first_class_lots`.
4. Deploy only API instances whose models and services use `lot_id`.

Do not allow an old API instance to run during or after the migration. It still
reads and writes `boxes.lot`, which is dropped by the cutover. Rollback restores
that text from `lots.name` before removing the new foreign keys, but rollback
must likewise happen while API instances are stopped.

Production/offline SQL must be generated with the PostgreSQL database URL. The
migration uses deterministic PostgreSQL SQL for validation and backfill so
`alembic upgrade head --sql` remains a valid deployment artifact. Its SQLite
path is an online compatibility path for local migration tests and intentionally
does not claim to be a deployable offline SQLite artifact.
