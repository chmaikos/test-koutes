# First-class Box Files migration deployment

Migration `0035_first_class_box_files` introduces the complete
`Lot → Pallet → Box → File` inventory hierarchy. A File is a tracked physical
record inside a Box; it is not an uploaded ERP document or request attachment.

The migration creates `box_files`, append-only `box_file_events`, and immutable
`box_request_item_file_snapshots`. It also adds the composite
`boxes(id, lot_id)` identity needed to guarantee that a File and its Box belong
to the same Lot.

## Release contract and compatibility

- Stop every old API writer, worker, and scheduler for the cutover. The new API
  and web build must be deployed together after the database reaches `0035`.
- `boxes.contents` and `box_request_items.contents` are intentionally retained
  as compatibility columns. New behavior uses structured Files and request
  File snapshots; do not drop or manually rewrite the compatibility columns.
- For each legacy value, the migration splits on the literal `|`, trims each
  segment, discards blank segments, preserves source order, and compacts the
  resulting positions from `1`.
- A Box segment receives the deterministic reference
  `LEGACY-<trimmed-box-number>-BOX-<box_id>-FILE-<position>`.
- A request-item segment receives
  `LEGACY-REQUEST-ITEM-<request_item_id>-FILE-<position>` and snapshot kind
  `legacy_contents`.
- The migration does not split on commas, semicolons, or line breaks. A value
  without `|` becomes one File description.
- PostgreSQL is the production target. Online upgrade works on the configured
  database; offline SQL backfill is emitted only for the PostgreSQL dialect.

## Preflight invocation

`api/scripts/box_files_preflight.py` is read-only. It verifies migration
coverage plus operational File integrity and exits `0` when safe or `2` when
reconciliation is required. Because it queries the new tables, run it against
a clone upgraded to `0035` during rehearsal and against production immediately
after migration, before writes reopen. Before the migration, use the legacy SQL
checks in the next section.

Outside Docker:

```bash
cd api
uv run python scripts/box_files_preflight.py \
  --database-url "$DATABASE_URL"
uv run python scripts/box_files_preflight.py \
  --database-url "$DATABASE_URL" --json \
  > /tmp/box-files-preflight.json
```

Inside the running API container:

```bash
docker compose exec api \
  sh -lc 'python scripts/box_files_preflight.py --database-url "$DATABASE_URL"'
docker compose exec api \
  sh -lc 'python scripts/box_files_preflight.py --database-url "$DATABASE_URL" --json'
```

The API image includes `scripts/box_files_preflight.py`. If the API service is
stopped but the image and database are available, bypass the normal API
entrypoint so it does not run migrations implicitly:

```bash
docker compose run --rm --no-deps --entrypoint python api \
  scripts/box_files_preflight.py --json
```

## Legacy pre-upgrade checks

Run these read-only checks before backup and migration:

```bash
docker compose exec -T db sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
SELECT count(*) AS boxes_with_legacy_contents
FROM boxes
WHERE contents IS NOT NULL
  AND EXISTS (
    SELECT 1
    FROM unnest(string_to_array(contents, '|')) AS part(value)
    WHERE btrim(value) <> ''
  );

SELECT count(*) AS expected_box_files
FROM boxes AS b
CROSS JOIN LATERAL string_to_table(b.contents, '|') AS part(segment)
WHERE btrim(part.segment) <> '';

SELECT count(*) AS expected_request_snapshots
FROM box_request_items AS i
CROSS JOIN LATERAL string_to_table(i.contents, '|') AS part(segment)
WHERE btrim(part.segment) <> '';
SQL
```

Retain the two expected counts with the release record. Large descriptions,
embedded `|` characters that were meant as text, and unexpected blank-only
segments require an explicit data-owner decision before cutover.

## Safe production upgrade

1. Announce a write outage and stop/drain all API instances, background jobs,
   and schedulers:

   ```bash
   docker compose stop api web
   ```

2. Confirm exactly one Alembic head and generate PostgreSQL offline SQL:

   ```bash
   cd api
   uv run alembic heads
   uv run alembic upgrade head --sql > /tmp/warehouse-0035-upgrade.sql
   test -s /tmp/warehouse-0035-upgrade.sql
   ```

   `alembic heads` must print `0035_first_class_box_files`. Inspect the SQL for
   `CREATE TABLE box_files`, `CREATE TABLE box_file_events`,
   `CREATE TABLE box_request_item_file_snapshots`, the `string_to_table(...,
   '|')` backfills, and both deterministic `LEGACY-` reference forms.

3. Back up PostgreSQL and RustFS as one recoverable pair. Record checksums and
   rehearse restoring both into an isolated environment. One Compose example:

   ```bash
   cd ..
   mkdir -p backups
   docker compose exec -T db sh -lc \
     'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
     > "backups/postgres-before-0035.dump"
   RUSTFS_VOLUME="$(
     docker inspect "$(docker compose ps -q rustfs)" \
       --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}'
   )"
   test -n "$RUSTFS_VOLUME"
   docker run --rm \
     -v "$RUSTFS_VOLUME:/source:ro" \
     -v "$PWD/backups:/backup" alpine \
     tar -C /source -czf /backup/rustfs-before-0035.tar.gz .
   sha256sum backups/postgres-before-0035.dump \
     backups/rustfs-before-0035.tar.gz \
     > backups/before-0035.sha256
   ```

   Do not claim a recoverable backup until a paired restore has been tested.

4. Apply the migration once, using one of these controlled paths.

   Outside Docker:

   ```bash
   cd api
   uv run alembic upgrade head
   uv run alembic current
   ```

   With the Compose API image while normal services remain stopped:

   ```bash
   docker compose run --rm --no-deps --entrypoint alembic api upgrade head
   docker compose run --rm --no-deps --entrypoint alembic api current
   ```

   The regular `api/entrypoint.sh` also runs `alembic upgrade head` before
   starting the API, but the explicit command above keeps schema verification
   separate from application startup.

5. Run `box_files_preflight.py` using one of the documented invocations. Any
   conflict or exit code `2` keeps writes closed.
6. Deploy/start the matching API and web:

   ```bash
   docker compose up -d --build api
   docker compose up -d --build web
   docker compose ps
   ```

## Database verification

```bash
docker compose exec -T db sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
SELECT version_num FROM alembic_version;

SELECT
  (SELECT count(*) FROM box_files) AS box_files,
  (
    SELECT count(*)
    FROM boxes AS b
    CROSS JOIN LATERAL string_to_table(b.contents, '|') AS part(segment)
    WHERE btrim(part.segment) <> ''
  ) AS expected_legacy_box_files,
  (SELECT count(*) FROM box_request_item_file_snapshots)
    AS request_file_snapshots,
  (
    SELECT count(*)
    FROM box_request_items AS i
    CROSS JOIN LATERAL string_to_table(i.contents, '|') AS part(segment)
    WHERE btrim(part.segment) <> ''
  ) AS expected_legacy_request_snapshots;

SELECT id, lot_id, box_id, reference, position
FROM box_files
WHERE reference NOT LIKE 'LEGACY-%'
ORDER BY id
LIMIT 20;

SELECT bf.id
FROM box_files AS bf
JOIN boxes AS b ON b.id = bf.box_id
WHERE bf.lot_id <> b.lot_id;

SELECT lot_id, normalized_reference, count(*)
FROM box_files
GROUP BY lot_id, normalized_reference
HAVING count(*) > 1;

SELECT box_id, position, count(*)
FROM box_files
GROUP BY box_id, position
HAVING count(*) > 1;

SELECT s.id
FROM box_request_item_file_snapshots AS s
LEFT JOIN box_request_items AS i ON i.id = s.request_item_id
WHERE i.id IS NULL;
SQL
```

Immediately after migration and before any new File writes, both actual counts
must equal their expected legacy counts, `alembic_version` must be
`0035_first_class_box_files`, the non-`LEGACY` query should be empty, and all
integrity queries should return zero rows.

## Integrity endpoint and report

The authenticated Admin endpoint is:

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $ADMIN_ACCESS_TOKEN" \
  http://localhost:8000/api/files/integrity | python -m json.tool
```

Expect `"safe": true` and `"conflict_count": 0`. The report checks cross-Lot
placements, duplicate normalized references, invalid/duplicate positions,
archived Files participating in active workflows, and tracked request
snapshots detached from their File. The command-line preflight additionally
checks blank/noncanonical references, missing Box references, exact legacy
snapshot coverage, and migration counts. Save both JSON reports with the
release evidence.

## Rollback restrictions

Stop all API writers before any rollback. Downgrade `0035 → 0034` is
intentionally refused when:

- any `box_file_events` row exists, or
- the complete `box_files` table differs from the exact deterministic backfill
  of current `boxes.contents`, or
- request File snapshots differ from the exact deterministic backfill of
  current `box_request_items.contents`, including reference, description,
  position, kind, links, barcode, version, archive, or actor metadata.

Normal application use creates File events immediately, so downgrade is not a
general rollback path after writes reopen. Never delete events or rewrite
Files merely to defeat the guard. Use a forward fix or restore the paired
pre-`0035` PostgreSQL/RustFS backup, then deploy only the matching old API/web
build. A database-only restore can orphan uploaded ERP documents/attachments;
a RustFS-only restore can orphan metadata.

## Post-deploy checks

- `/api/healthz` and `/api/docs` respond, and the Files routes appear in
  OpenAPI.
- Admin integrity endpoint and CLI preflight both report safe/zero conflicts.
- Files list/detail search, hierarchy links, inherited warehouse/status, and
  audit timeline work under Admin and restricted warehouse ACLs.
- Manual receipt requires at least one File reference and accepts optional
  description/barcode.
- XLSX mapping requires File reference, accepts optional description/barcode,
  and repeated Box rows create multiple Files.
- Inbound preview reports Files as create/update/move/preserve/blocked and
  requires the separate default-off File-move acknowledgement.
- Return candidates show File count/summary; request details retain immutable
  File snapshots after a current File edit.
- File archive/restore and same-Lot Box move preserve ordering and audit;
  active return reservations block identity/placement changes unless an Admin
  explicitly forces and records a reason.
- Box, Lot, and Pallet pages and exports show active/archived File counts.
- `/api/exports/files.csv` and `/api/exports/files.xlsx` download with the
  caller's filters and warehouse ACL.
- API tests, Ruff, frontend tests/lint/typecheck/build, migration rehearsal,
  and `git diff --check` pass before the outage is closed.
