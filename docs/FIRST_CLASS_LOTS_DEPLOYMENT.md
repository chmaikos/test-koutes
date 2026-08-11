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

For the release that also includes safe merge, safe purge, and Administrative
Force Lot Purge:

1. Stop all old API instances and background workers.
2. Confirm the preflight exits successfully.
3. Back up PostgreSQL and RustFS as one recoverable pair.
4. Apply Alembic normally through `0027_first_class_lots`,
   `0028_safe_lot_merge`, `0029_lot_purge_audit`, and
   `0030_force_purge_adjustment` with
   `alembic upgrade head`.
5. Deploy only the matching API build after the database reports
   `0030_force_purge_adjustment`, then deploy the matching web build.

Do not allow an old API instance to run during or after the migration. It still
reads and writes `boxes.lot`, which is dropped by the cutover. Rollback restores
that text from `lots.name` before removing the new foreign keys, but rollback
must likewise happen while API instances are stopped.

Migration `0029` is additive and creates the independent purge ledger before
the API exposes purge routes. Its downgrade is intentionally blocked after the
first purge audit is written. Stored-object cleanup and retries run through the
normal API after the database purge commits, so keep RustFS reachable when the
new API starts; an outage is recorded for retry and does not roll back the
already committed database purge.

Migration `0030` adds the `force_purge_adjusted` request-event value used for
preserved requests rewritten by Administrative Force Lot Purge. PostgreSQL enum
changes are applied before the new API starts. Downgrade retains an unused enum
label on PostgreSQL and is blocked while any force-purge adjustment event
exists.

Safe purge removes only an exclusive, archived self-receipt graph. Force purge
is a separate Admin-only permanent recovery path shown only after safe purge is
blocked. It is selected-Lot-only: sibling Lots, their boxes, and surviving
request records remain. Mixed requests are rewritten and audited; zero-item
requests are deleted with their FK-owned graph and incoming source/parent/root
links are detached. A merge source or merge target remains a hard blocker even
in force mode.

Force execution requires the exact Lot name, exact server phrase, a minimum
20-character reason, every safeguard acknowledgement, and matching version and
graph signature under deterministic PostgreSQL locks. There is no undo. The
purge ledger is committed atomically with database mutation. Shared object keys
are never scheduled for deletion; deleted-only keys are retained in the ledger
for durable post-commit cleanup. If RustFS cleanup fails, keep the audit ID and
use the Admin cleanup retry rather than treating the operation as fully
complete.

Production/offline SQL must be generated with the PostgreSQL database URL. The
migration uses deterministic PostgreSQL SQL for validation and backfill so
`alembic upgrade head --sql` remains a valid deployment artifact. Its SQLite
path is an online compatibility path for local migration tests and intentionally
does not claim to be a deployable offline SQLite artifact.
