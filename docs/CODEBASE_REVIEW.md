# Codebase review — 14 October 2026

This is the original review snapshot. Remediation, task priorities, verification,
deployment notes, and remaining architectural work are tracked in
[REVIEW_FIX_PLAN.md](REVIEW_FIX_PLAN.md).

Scope: local review of the FastAPI/SQLAlchemy backend, React frontend, authentication, warehouse ACLs, inventory/request workflows, spreadsheet handling, SSE, storage, scheduling, tests, and deployment configuration. No production database or external account was modified. Application code was not changed.

Priorities: **P1** should be addressed first because it affects confidentiality, authentication, or historical data integrity; **P2** affects correctness, permissions, or operational reliability; **P3** is lower-impact correctness/maintenance work. Runtime reproductions and source-derived findings are distinguished below. This is not a claim that every execution path has been audited.

## Verification

| Check | Result |
| --- | --- |
| Backend suite | 519 passed, 2 failed, 1 skipped; 89.87 seconds |
| Frontend suite | 130 passed in 18 test files |
| TypeScript | Passed (`npm run lint`) |
| Production frontend build | Passed; main JS bundle 1,092.66 kB / 276.36 kB gzip |
| Backend Ruff checks | Passed |
| Additional bug reproductions | 11 passed, asserting the problematic current behavior |
| npm dependency audit | 114 affected package entries: 107 high, 7 moderate; includes transitive development dependencies |

The temporary reproduction script is `/tmp/test_warehouse_review.py`. Run it from the repository root with `PYTHONPATH=api:. .venv/bin/python -m pytest -p api.tests.conftest /tmp/test_warehouse_review.py -q`. These are demonstrations of existing bugs, not regression tests asserting corrected behavior. The audit output is `/tmp/warehouse-review-npm-audit.json`.

Node checks used the existing Linux Node 22.18.0 installation directly because the default npm launcher failed with a WSL detection error. The application build succeeded without installing or upgrading dependencies.

Production PostgreSQL concurrency, real RustFS, Microsoft Entra token issuance, Graph delivery, and browser end-to-end account switching were not exercised. The existing backend fixtures use SQLite, and the RustFS integration test was skipped.

## Findings

### 1. P1 — Authenticated browser caches survive account changes

**Locations:** `web/vite.config.ts:90`, `web/src/main.tsx:44`, `web/src/api/hooks.ts:207`, `web/src/auth/local.ts:40`.

**Evidence:** Source review and inspection of the generated service worker. All ordinary `/api/` GET responses, including `/auth/me`, share `warehouse-api-get-v2`. There is no account-specific cache key or logout cleanup. The API does not add an Authorization-dependent `Vary` header to ordinary JSON responses. The singleton React Query client also survives local logout/login, and its keys omit the user identity.

**Impact:** On a shared warehouse device, a new user can see the previous user's cached identity and inventory. A quick local account switch can reuse still-fresh React Query data; an offline connection or network request exceeding the three-second timeout can reuse another account's persistent service-worker response. Stale identity data also drives frontend role gates. Backend permissions still protect fresh network calls, but cannot protect already cached data.

**Change:** Cancel outstanding queries and clear memory caches when the identity changes. Exclude authentication, authorization-sensitive metadata, and binary responses from offline caching. If offline inventory is required, use deliberate per-user cache ownership and deletion on logout, with visible stale/offline indicators. Include discrepancy-photo downloads in the binary exclusion; they currently fall through to the general GET cache even though the API marks them `no-store`.

### 2. P1 — Changing local credentials does not revoke existing tokens

**Locations:** `api/app/routers/auth_local.py:141`, `api/app/deps.py:32`, `api/app/security.py:62`.

**Evidence:** HTTP reproduction changed a local administrator's password, then successfully called `/api/users` with the token issued before the change.

**Impact:** A copied or compromised administrator token remains usable after password/username rotation until its original expiry; the default lifetime is eight hours. There is no server-side session revocation mechanism.

**Change:** Add a session/credential version or token invalidation timestamp, validate it on authenticated requests, increment it when credentials change, and issue a replacement token to the legitimate session. Coordinate this with SSE connection invalidation.

### 3. P1 — Employee transfers permit historical records to move across warehouse ACLs

**Locations:** `api/app/routers/productivity.py:178`, `api/app/routers/productivity.py:190`, `api/app/routers/productivity.py:213`, `api/app/routers/employees.py:194`.

**Evidence:** Two HTTP reproductions created output in warehouse 1, transferred the employee to warehouse 2, and resubmitted the original date. The existing record's warehouse changed to 2. An operator granted only warehouse 2 could perform this update even though the original entry was invisible to that operator's GET request.

**Impact:** Correcting historical output silently subtracts it from the original warehouse and adds it to the current one. A user can overwrite historical output belonging to an inaccessible warehouse because authorization checks the employee's current warehouse instead of the existing entry's warehouse.

**Change:** Preserve an existing entry's historical warehouse and authorize against that record before editing it. Make intentional reassignment an explicit administrator action with an audit record. Define how backdated entries and employee period averages should behave after transfers.

### 4. P1 — Spreadsheet formula protection is only applied to selected exports

**Locations:** `api/app/services/exports.py:224`, `api/app/services/exports.py:314`, `api/app/services/exports.py:360`, `api/app/services/exports.py:433`, `api/app/services/exports.py:539`.

**Evidence:** Exporting a box whose Lot name is `=1+1` produced an XLSX cell with `data_type == "f"`. Lot name validation accepts that text. CSV writers also retain formula-like strings. `_safe_spreadsheet_text` exists but is applied mainly to physical File exports; other exports pass user-controlled names, contents, notes, reasons, and labels through unchanged.

**Impact:** Opening an export in spreadsheet software can evaluate user-controlled text as a formula. At minimum this corrupts the apparent exported value; more harmful behavior depends on the spreadsheet application and its security settings.

**Change:** Apply one shared text-sanitization policy to every CSV/XLSX export path while preserving numeric/date types. Test Lot/Pallet names, warehouse and employee names, notes, and reconciliation/report labels. Do not rely on numeric Box-number validation to protect the other columns.

### 5. P2 — Viewers can write discrepancy photos

**Locations:** `api/app/routers/requests.py:1451`, `web/src/pages/RequestDetailPage.tsx:931`.

**Evidence:** An HTTP reproduction authenticated as a Viewer who was not the requester and successfully uploaded a discrepancy photo (201). The storage write was stubbed; the permission check, database metadata write, version increment, and event creation ran normally. The endpoint only checks warehouse visibility, unlike the attachment endpoint's `_can_attach` check. The frontend also exposes the upload control to every viewer of the request.

**Impact:** Read-oriented users can add files and mutate request versions/audit history, potentially causing conflicts for legitimate coordinators.

**Change:** Define and enforce the photo-write permission on the server, normally matching requester/coordinator attachment permissions, and hide the control for other roles. Add explicit Viewer and unrelated-Operator authorization tests.

### 6. P2 — SSE connections retain stale authorization and cannot refresh reconnect tokens

**Locations:** `api/app/routers/stream.py:84`, `web/src/hooks/useLiveStream.ts:39`, `web/src/hooks/useLiveStream.ts:51`.

**Evidence:** Source review. The server snapshots ACLs once and never revisits user activity, role, or token expiry. The frontend acquires a token once; EventSource reconnects to the same token-bearing URL after errors.

**Impact:** Disabling a user or revoking a warehouse grant does not stop an already connected stream from receiving that warehouse's events. After a token expires and the connection drops, automatic retries continue with the expired token and live updates stop until the hook remounts or the page reloads. This concerns event metadata; normal API requests still check authorization.

**Change:** Bound stream lifetimes, periodically revalidate or explicitly revoke connections, and implement reconnects that acquire a fresh token with backoff. Avoid logging full token-bearing URLs: current query tokens are also exposed to ordinary proxy/access logging unless redacted.

### 7. P2 — Scheduler/background-thread events are silently dropped

**Locations:** `api/app/events.py:41`, `api/app/services/alerts.py:141`, `api/app/jobs/scheduler.py:22`.

**Evidence:** Runtime reproduction subscribed on the application event loop, invoked `publish_threadsafe` from a worker thread, and received no event. `get_running_loop()` searches the calling thread; APScheduler's synchronous jobs and synchronous background tasks run outside the application loop.

**Impact:** Alerts persisted by scheduled/background evaluation do not generate the intended SSE refresh. Some dashboard polling masks this, but live alert pages can stay stale.

**Change:** Register the application loop during lifespan startup and dispatch thread-originated events onto that loop, or use an appropriate cross-thread queue. Test a real thread-to-SSE delivery rather than only same-loop publication.

### 8. P2 — Common S3 service errors bypass storage error handling

**Locations:** `api/app/services/object_storage.py:52`, `api/app/services/object_storage.py:75`, `api/app/services/object_storage.py:86`, `api/app/services/object_storage.py:95`.

**Evidence:** A mocked `get_object` raising `ClientError(NoSuchKey)` escaped unchanged. `ClientError` is not a subclass of `BotoCoreError`. `delete_document_strict` already catches both; the other storage operations do not consistently do so.

**Impact:** Missing objects, rejected credentials, access-denied responses, and bucket-creation races can produce unexpected 500 responses instead of the intended controlled storage error. A cleanup failure can also replace the original database failure.

**Change:** Normalize both exception families for create/upload/download. Keep best-effort cleanup from masking the original exception. Include missing object, AccessDenied, failed upload, and failed cleanup tests.

### 9. P2 — XLSX preview silently discards data outside its scan window

**Location:** `api/app/services/xlsx_preview.py:59`.

**Evidence:** A workbook containing a header at row 1 and an inventory value at row 5002 returned a successful preview with only the header. The iterator stops at physical row 5001 while the limit check counts nonempty rows. Columns after column 50 are similarly omitted without a truncation indicator.

**Impact:** A sparse or formatted ERP workbook can produce an apparently complete preview while real inventory rows or mapping columns are missing, undermining receipt completeness.

**Change:** Either reject worksheets exceeding supported dimensions with a clear explanation, or scan/count according to one consistent rule and report truncation explicitly. Never allow the mapped import flow to imply the whole workbook was reviewed when rows were excluded by the server.

### 10. P2 — Local password limits exceed bcrypt's supported input length

**Locations:** `api/app/security.py:25`, `api/app/routers/auth_local.py:32`, `api/app/routers/auth_local.py:48`.

**Evidence:** With pinned bcrypt 4.2.1, hashing a password containing 72 `a` bytes followed by `original` also accepted 72 `a` bytes followed by `different`. The schemas permit 255 characters, while this hashing path only distinguishes the first 72 bytes.

**Impact:** Long passwords can silently share credentials, and a change confined to the ignored suffix appears successful without changing the effective password. Multibyte characters reach the limit sooner than the character count suggests.

**Change:** Validate encoded byte length for the current bcrypt scheme, or migrate to a scheme supporting the desired length with versioned hash handling. Ensure password rotation compares effective credentials correctly and test multibyte inputs.

### 11. P2 — Dependencies need a controlled security update

**Locations:** `api/pyproject.toml:17`, `api/pyproject.toml:25`, `web/package.json`, `web/package-lock.json`.

**Evidence:** `npm audit --json` reported 114 affected package entries (107 high, 7 moderate), including development/build dependencies and transitive propagation. These counts do not represent 114 independently exploitable production flaws. Backend pins include python-jose 3.3.0 and Jinja2 3.1.4, both preceding documented security fixes.

The python-jose maintainer's [3.4.0 release](https://github.com/mpdavis/python-jose/releases/tag/3.4.0) documents fixes for CVE-2024-33663 and CVE-2024-33664. The Jinja maintainer's [sandbox advisory](https://github.com/pallets/jinja/security/advisories/GHSA-q2x7-8rv6-6q7h) includes 3.1.4, but requires attacker-controlled templates; this repository uses its own email templates, so direct applicability was not demonstrated.

**Change:** Triage advisory reachability, upgrade supported dependency families, regenerate lockfiles, and run the existing workflow suites plus production builds. Set the Entra JWT algorithm allowlist from server policy (`RS256`) instead of copying the untrusted token header. Avoid an indiscriminate forced major upgrade based solely on audit counts.

### 12. P2 — The backend test suite is currently failing on brittle SQL assertions

**Locations:** `api/tests/test_lot_merge.py:106`, `api/tests/test_lot_merge.py:120`.

**Evidence:** Both failed in the full suite. Assertions require exact substrings `INNER JOIN barcode_identities` and `LEFT OUTER JOIN lots`. Current SQLAlchemy output renders equivalent joins using different spelling/nesting as barcode joins were added. The lock-target assertions preceding the first failure passed.

**Impact:** The suite is red, obscuring meaningful regression signals. These failures alone do not demonstrate broken production lock behavior.

**Change:** Update tests to check relevant join/lock semantics without depending on superficial SQL formatting. Add PostgreSQL execution tests for the invariants these tests intend to protect.

### 13. P3 — Ordinary SSO requests do not persist last-login updates

**Location:** `api/app/deps.py:103`.

**Evidence:** A runtime reproduction resolved an existing SSO user whose email/name/role were unchanged, rolled back the read-only request's session, and confirmed that `last_login_at` retained its old value. The timestamp assignment does not set `changed`, so no commit occurs unless a profile field changes or a later mutation happens to commit it.

**Impact:** Administrator activity information depends on unrelated writes and can falsely show active users as inactive.

**Change:** Persist activity deliberately, ideally with a throttled update rather than a write for every API call. Keep authentication bookkeeping transactionally separate from business mutations where practical.

### 14. P3 — Whitespace-only warehouse/employee names pass validation

**Locations:** `api/app/schemas/warehouses.py:91`, `api/app/routers/warehouses.py:66`, `api/app/schemas/employees.py:27`, `api/app/routers/employees.py:178`.

**Evidence:** An HTTP reproduction submitted a warehouse name containing only spaces and received 201 with `name: ""`. Length validation occurs before the routers strip whitespace. The same pattern exists for employee creation/update.

**Impact:** Blank identity labels make selectors, imports, reports, and audits ambiguous.

**Change:** Strip and normalize before checking minimum length, and reject blank values consistently on create/update/import paths. Lots already have a suitable blank-name rejection pattern.

## Further improvements

1. **Add browser workflow tests.** Current frontend tests mainly exercise helper functions. Cover shared-device logout/login, service-worker offline behavior, token expiry/reconnect, request completion, inventory relocation acknowledgement, and denied mutations.
2. **Test PostgreSQL transactions and migrations against PostgreSQL.** SQLite does not reproduce row locks, skip-locked claims, or concurrent uniqueness behavior. Exercise concurrent returns/receipts/merges/purges and upgrade from representative older database snapshots.
3. **Make productivity upserts atomic and auditable.** The current select-then-insert can race on `(employee_id, entry_date)` and return an unhandled integrity error; corrections overwrite the creator and previous values. Use a real conflict/update strategy or locks and record correction history.
4. **Avoid synchronous network work on the async request loop.** Async request/upload handlers perform synchronous SQLAlchemy and boto3 operations, sometimes while holding row locks. Slow storage can delay unrelated requests and SSE. Measure under concurrent upload/load and move blocking work into an appropriate execution context.
5. **Improve scheduler delivery recovery.** Productivity email is scheduled once daily; a failed send is recorded as retryable but no automatic same-date retry job exists. Its check-send-record sequence also lacks a durable pre-send claim, so multiple API processes can duplicate delivery. Reuse the durable outbox/claim approach used by request emails.
6. **Improve frontend startup and reporting scale.** Route-lazy-load large pages; the production JS bundle is 1.09 MB. The productivity entries endpoint loads all matching records, and employee dropdown callers assume at most 500 employees. Add bounded pagination or explicit complete-roster loading before warehouse scale exceeds those assumptions. Render account-loading failures explicitly in `CredentialsGate` instead of leaving an indefinite spinner on 403/503.
7. **Make deployment reproducible and reduce exposed trust boundaries.** Use `npm ci` for lockfile-controlled Docker builds; the API image currently resolves an unbounded boto3 requirement without using `uv.lock`. Pin the RustFS image instead of `latest`. Bind object-storage ports to a trusted interface or remove public publishing; current Compose publishes port 9000 on all interfaces with fallback credentials. Restrict forwarded-header trust to the proxy rather than combining a directly published API port with `--forwarded-allow-ips=*`.
8. **Fix the local login timing defense and add throttling.** The missing-user dummy bcrypt hash is invalid and returns immediately. Measured verification was about 0.009 ms for it versus 163 ms for a valid cost-12 hash. Use a valid fixed dummy hash, apply the same response policy to disabled accounts, and rate-limit repeated failed logins.

## Suggested order

First repair browser account isolation, local token revocation, historical productivity authorization, and spreadsheet sanitization. Next fix photo permissions, SSE lifecycle/thread delivery, storage normalization, and XLSX completeness. Restore the test suite and add focused regressions while updating dependencies. Then address browser/PostgreSQL integration coverage, scheduler recovery, performance, and deployment reproducibility.
