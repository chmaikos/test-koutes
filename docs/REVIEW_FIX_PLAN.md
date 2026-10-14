# Review fix priorities and verification

Branch: `fix/codebase-review-hardening`. Target: `main`.

The parent agent owns integration review, dependencies, deployment, CI, and final verification. Subagents work in separate file areas; the parent reviews their patches before committing and opening the PR.

| Order | Review finding | Priority | Owner | Acceptance criteria | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | #1 Browser account isolation | P1 | frontend_live | Identity changes cancel/clear old queries; authenticated responses cannot leak through offline caches | Complete |
| 2 | #2 Credential/token revocation | P1 | auth_security | Old local tokens rejected after rotation; current browser receives replacement token | Complete |
| 3 | #3 Historical productivity ACL | P1 | data_integrity | Entries retain historical warehouse; inaccessible records cannot be overwritten after transfers | Complete |
| 4 | #4 Spreadsheet formulas | P1 | data_integrity | All export text stays text, preserving native numbers/dates | Complete |
| 5 | #5 Discrepancy photo writes | P2 | frontend_live | Server denies unrelated Viewers/Operators; UI reflects permissions | Complete |
| 6 | #6 SSE authorization/reconnection | P2 | frontend_live | Revoked credentials/ACLs stop delivery; reconnect gets a fresh token | Complete |
| 7 | #7 Thread-originated SSE events | P2 | frontend_live | Worker-thread publication reaches subscribers on the application loop | Complete |
| 8 | #8 Storage exceptions | P2 | data_integrity | S3 service errors normalized; cleanup preserves original errors | Complete |
| 9 | #9 XLSX completeness | P2 | data_integrity | Unsupported dimensions rejected explicitly rather than silently truncated | Complete |
| 10 | #10 bcrypt byte limits | P2 | auth_security | Oversized UTF-8 passwords rejected; suffix changes cannot bypass effective rotation | Complete |
| 11 | #11 Dependencies | P2 | parent | Patched supported dependencies locked; advisory audit and builds verified | Complete |
| 12 | #12 SQL assertions | P2 | data_integrity | Relevant lock/join semantics tested without brittle spelling | Complete |
| 13 | #13 SSO last activity | P3 | auth_security | Read-only authentication updates activity durably without unnecessary write volume | Complete |
| 14 | #14 Blank names | P3 | data_integrity | Whitespace normalized before minimum-length validation on create/update/import | Complete |

Related improvements: atomic and audited productivity corrections; valid dummy password verification and failed-login throttling; deterministic container dependency installs; restricted storage/proxy exposure; PostgreSQL migration/concurrency checks; browser account/SSE regression coverage; scheduler retry/claim recovery; explicit account-loading errors; route code splitting. Any remaining architectural work will be recorded explicitly instead of claimed complete.

## Verification gates

1. Review every agent diff for authorization, compatibility, migration order, failure cleanup, and unnecessary scope.
2. Run backend and frontend suites, Ruff, TypeScript, production build, and dependency audits using the new locks.
3. Run migrations and transaction checks on an isolated PostgreSQL instance; run the storage round-trip on isolated RustFS when available.
4. Inspect generated PWA behavior and test identity/token transitions.
5. Check the final diff and clean working tree, commit and push the branch, create a PR targeting `main`, and inspect its CI results. Creating the PR does not automatically merge it.

## Completed verification

| Check | Result |
| --- | --- |
| Full backend suite with real PostgreSQL and RustFS enabled | **634 passed**, no skipped tests |
| PostgreSQL integration | Concurrent productivity upserts and report claims; real shared/exclusive locks; migration/backfill/downgrade in isolated schemas |
| RustFS integration | Real upload/download/delete round-trip |
| Frontend suite | **140 passed** in 22 files |
| Chromium browser tests | **7 passed**: shared-device inventory/identity, stale 401, cross-tab switching, validation, rotation, retry UI, production service worker |
| TypeScript | Application and E2E configuration passed |
| Ruff and whitespace checks | Passed |
| Production frontend build | Passed; main bundle **610.87 kB / 174.41 kB gzip**, about 44% / 37% smaller than the reviewed baseline |
| Docker builds | API and frontend clean installs/builds passed |
| Real container smoke through nginx | PostgreSQL login, token rotation/revocation, private/no-store headers, structured receipt, barcode issuance, safe XLSX export passed |
| Locked backend runtime dependency audit | **No known vulnerabilities** |
| Frontend production dependency audit | **No known vulnerabilities** |

The Chromium authenticated workflows mock API responses; the service-worker
test uses the actual production worker. The container smoke and PostgreSQL/
RustFS checks cover the real backend stack. Microsoft Entra and Graph network
calls were not made; signed RSA token validation and email failure/claim
behavior are verified with controlled fixtures.

## Deployment and behavior changes

- Apply migrations `0037`–`0039` before starting the new API, and drain old
  replicas to make credential revocation effective across all instances.
- Authenticated API data requires the network. Only the PWA shell is cached;
  this deliberately removes shared-device offline inventory exposure.
- Local passwords must fit within 72 UTF-8 bytes. Oversized existing passwords
  need an operational reset; login throttling is per process.
- XLSX preview rejects sheets exceeding 5,000 physical rows or 50 columns,
  and rejects missing dimensions with instructions to resave the workbook.
- Historical productivity records retain their warehouse and creator;
  corrections and deletion leave durable database audit snapshots.
- Daily email reports recover missed schedules and retry with backoff. Claims
  prevent simultaneous sends; Graph acceptance followed by a crash can still
  lead to a recovery duplicate.
- Compose binds API and RustFS ports to loopback and requires storage
  credentials. The web proxy remains accessible on the configured web port.
- Alembic also now handles percent-encoded connection URLs correctly, a bug
  found while exercising real isolated PostgreSQL schemas.

## Remaining follow-up work

These items are not blockers for the 14 reviewed bug fixes:

1. **Build dependency major migration:** the full npm audit retains seven
   affected development-package entries (five high, two moderate) in Tailwind
   3's `braces`/selector dependency chain. These process repository-controlled
   source patterns/CSS, not runtime warehouse inputs; production npm audit is
   clean. Eliminating them requires a separate Tailwind 4 styling migration
   and visual review, rather than a forced upgrade in this security fix.
2. **Large-roster/report pagination:** bounded entry pagination and explicit
   full-roster loading beyond 500 employees need an API/UI contract change.
   Route lazy loading and account-error handling are complete here.
3. **Shared live-event transport:** SSE delivery is still process-local; use a
   shared bus before horizontal scaling. External proxy logs also need stream
   token redaction.
4. **Complete asynchronous data access:** S3 uploads and cleanup now run off
   the request event loop; synchronous SQLAlchemy work remains and needs
   measured concurrency/load testing before a broader async conversion.
5. **Audit viewing and service-provider checks:** productivity audit records
   are durable but do not yet have a UI. Entra tenant login and real Graph
   delivery require an environment with those accounts configured.

One non-failing Starlette TestClient deprecation warning remains: its current
httpx compatibility path is supported but recommends a future httpx2 test-client
upgrade. The new CI runs the backend, PostgreSQL/RustFS integrations, frontend,
browser tests, production build, and runtime dependency audits on the PR.
