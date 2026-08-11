# Warehouse Box Tracker

A small, dockerized web app for tracking the status of boxes across a 3-building
warehouse. Built with **FastAPI**, **React (Vite + TypeScript)**, **Postgres 16**,
**Microsoft Entra ID** (M365 SSO), live updates over **SSE**, CSV/XLSX exports,
and threshold alerts (in-app + email via Microsoft Graph).

**Documentation:** [Ελληνικός οδηγός (Markdown)](docs/USER_GUIDE_EL.md) ·
[Ελληνικός οδηγός (PDF)](docs/USER_GUIDE_EL.pdf)

## Features

- Per-warehouse live dashboard (received today, returned today, in-progress,
  ready-to-return, capacity bar, open alerts).
- Searchable / filterable boxes table with inline status transitions.
- First-class Lots list/detail views with globally case-insensitive identity,
  status distribution, ACL-scoped completion metrics, audited global rename,
  safe explicit merge-on-rename (blocked by active or archived box-number
  overlap), and audited per-box reassignment. Merged source identities remain
  hidden audit tombstones; historical request/XLSX text is never rewritten.
- Full audit trail per box (timeline of events).
- Audited inbound box orders and return requests with the explicit lifecycle
  `submitted → approved → preparing → ready_for_transport → in_transit →
  awaiting_confirmation → completed`. Direction-specific labels distinguish
  dispatch/delivery from collection/warehouse confirmation.
- Structured operational recovery keeps holds, reschedules, and failed
  transport attempts out of the lifecycle status. Every exception records its
  reason, revised window, resume target, actor, timestamps, resolution, SLA
  adjustment, audit event, and notification.
- Request coordination includes priority, requested/SLA dates, transport
  windows, mover assignment, destination details, comments, and supporting
  attachments. Ready and awaiting-confirmation queues support daily operations.
- Fulfilment discrepancies support typed line records and photos. Partial
  inbound deliveries create backorders; partial collections release unresolved
  reservations into non-reserving follow-up drafts.
- Returns are linked to a completed inbound order. Users select all or any
  subset of that order's unreserved boxes currently marked Ready to Return;
  remaining boxes can be included in later return requests.
- XLSX imports and manual box creation automatically produce completed receipt
  batches, so those boxes use the same inbound-linked return workflow. Existing
  unlinked inventory is grouped into clearly labeled legacy receipt batches by
  migration.
- Request-linked boxes cannot be deleted or moved silently. Admin overrides
  require a reason; forced deletion archives the box and preserves its history,
  while conflicting active return requests are cancelled with an audit event.
- Inbound receipt can be entered manually or populated from any `.xlsx`
  layout by choosing the worksheet, mapping columns, and selecting or skipping
  source rows. Repeated rows for the same lot and box number are merged, with
  distinct contents combined into one physical box record.
- Versioned ERP delivery and return notes stored privately in local RustFS.
- Explainable demand recommendations combine minimum stock, outstanding inbound
  and return work, lead-time demand, weighted 30/90-day history, safety stock,
  capacity limits, confidence, and administrator adjustments.
- Saved XLSX mappings can be reused for imports and inbound confirmation.
- Request reconciliation, lifecycle analytics, and ACL-filtered CSV/XLSX
  exports cover discrepancies, documents, overrides, exceptions, SLA breaches,
  preparation, transport, acceptance, and throughput.
- In-app request notifications, email preferences, durable Graph-email outbox,
  and SSE refreshes cover lifecycle, coordination, exception, document, and
  comment events.
- Configurable staged-receipt governance supports administrator review,
  two-person thresholds, required documents, quarantine, release/rejection, and
  audited restoration of archived box identities.
- Employee productivity tracking with positive daily page entries, per-warehouse
  minimum pages/day settings, single top/bottom performers, and weekly,
  monthly, and rolling 90-day employee averages. Employees below the configured
  minimum in all three periods are clearly marked.
- Admin employee XLSX imports support worksheet/column mapping, row exclusion,
  and case-insensitive updates of existing warehouse employees.
- CSV and XLSX inventory exports honouring the current filters, plus
  lot summaries and productivity reports with employee averages and 90 days
  of daily-entry detail.
- Low-inventory and max-capacity alerts in-app and via Graph email.
- Admins can archive empty warehouses after open requests are closed. Archiving
  preserves inventory and audit history, deactivates the roster, resolves open
  alerts, and can be reversed from Settings.
- Microsoft 365 SSO with four roles: **Admin**, **Warehouse Mover**,
  **Operator**, **Viewer**.
- Mobile-friendly responsive UI; installable as a PWA on iOS and Android
  (see [Install on iOS / Android](#install-on-ios--android)).

## Stack

| Layer    | Tech                                                              |
| -------- | ----------------------------------------------------------------- |
| Frontend | React 18, Vite, TypeScript, Tailwind, shadcn/ui, TanStack Query, MSAL.js |
| Backend  | FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, APScheduler, msal    |
| Database | Postgres 16                                                       |
| Documents | RustFS (private, S3-compatible object storage)                  |
| Auth     | Microsoft Entra ID (Authorization Code + PKCE / JWT validation)   |
| Runtime  | Docker Compose                                                    |

## Quick start

```bash
cp .env.example .env
# Fill in ENTRA_*, LOCAL_JWT_SECRET, BOOTSTRAP_ADMIN_*, and RUSTFS_* values:
docker compose up --build
```

Open <http://localhost:8080>.

> **First sign-in.** If Entra ID isn't configured yet, use the **Local admin**
> tab on the sign-in screen with the username/password you set in `.env`
> (defaults: `admin` / `ChangeMe!2026`). The app forces you to pick a new
> username + password before letting you in. The default credentials are an
> **emergency-access fallback** — Microsoft 365 SSO is the recommended path.

The API exposes its OpenAPI docs at <http://localhost:8000/api/docs>.

## Install on iOS / Android

The web app is a Progressive Web App: warehouse staff can pin it to their home
screen, run it full-screen without browser chrome, and read the last-loaded
boxes/alerts list even when their Wi-Fi drops momentarily (writes still need
the network — there's no offline mutation queue). Installation is a one-time
action per device.

### iOS / iPadOS (Safari)

1. Open the app's URL in **Safari** (Chrome/Firefox on iOS won't show
   *Add to Home Screen*).
2. Tap the **Share** button (the square-with-arrow icon at the bottom of the
   screen on iPhone, top-right on iPad).
3. Scroll down and tap **Add to Home Screen**.
4. Confirm the title (defaults to *Boxes*) and tap **Add**.

### Android (Chrome / Edge)

1. Open the app's URL in **Chrome** (or any Chromium-based browser).
2. Chrome usually offers an **Install app** banner after a few seconds; tap
   it. If it doesn't appear, open the **⋮** menu and choose
   **Install app** / **Add to Home screen**.
3. Confirm the prompt — the icon appears in the launcher and behaves like a
   regular app.

After installation:

- Launching from the home-screen icon hides the browser address bar and the
  app fills the full safe-area on notched devices.
- An auto-updating service worker fetches new builds in the background; the
  next time the app is opened the latest version is loaded automatically.

## Setting up Microsoft 365 SSO

This walkthrough uses a **single Entra app registration** for both jobs:

- The SPA signs users in against it (Authorization Code + PKCE).
- The API validates the access tokens it issues, and uses the same client ID
  + a client secret to call Microsoft Graph (`Mail.Send`) for alert emails.

You need an Entra account with at least the **Application Administrator** role,
or a Global Administrator if you want to grant admin consent in the same pass.

### 1. Create the app registration

1. Sign in to the [Entra admin center](https://entra.microsoft.com/) ->
   *Identity* -> *Applications* -> **App registrations** -> **+ New registration**.
2. **Name**: e.g. `Warehouse Box Tracker`.
3. **Supported account types**: *Accounts in this organizational directory only*
   (single tenant). Multi-tenant works too but is rarely needed for an internal app.
4. **Redirect URI**: leave empty for now — we add it in step 2 once we know the
   exact public origin.
5. Click **Register**, then on the *Overview* page copy:
   - **Directory (tenant) ID** -> `ENTRA_TENANT_ID`
   - **Application (client) ID** -> `ENTRA_CLIENT_ID`

### 2. Add the SPA redirect URI

1. In the app registration, go to **Authentication** -> **+ Add a platform** ->
   **Single-page application**.
2. Add every origin from which the SPA will be loaded, including the trailing
   path. The frontend uses MSAL's default redirect (the same origin), so the
   redirect URI is just the origin itself:
   - `http://localhost:8080` (local docker compose)
   - `https://warehouse.example.com` (your production origin behind the
     SSL-terminating proxy)
3. Under **Implicit grant and hybrid flows**, leave both checkboxes unchecked —
   we use Authorization Code + PKCE.
4. Save.

> If you also access the app via a WAN IP (e.g. `https://203.0.113.10`), add
> that as another SPA redirect URI. MSAL must see an exact match.

### 3. Expose the API scope

1. Go to **Expose an API** -> **Set** next to *Application ID URI* and accept
   the default `api://<client-id>`. Copy this value into `ENTRA_API_AUDIENCE`.
2. Click **+ Add a scope**:
   - **Scope name**: `access_as_user`
   - **Who can consent?**: *Admins and users*
   - **Admin consent display name** / description: e.g. `Access Warehouse Box
     Tracker as the signed-in user`.
   - **State**: *Enabled*.
3. Click **Add scope**. The full scope is now
   `api://<client-id>/access_as_user` — that's exactly what `VITE_API_SCOPE`
   resolves to in `.env` (`${ENTRA_API_AUDIENCE}/access_as_user`).

### 4. Pre-authorize the SPA to its own scope

Because the SPA and API share one app registration, you still need to tell
Entra that *this client* may request *this scope* without a consent prompt:

1. Still on **Expose an API**, click **+ Add a client application**.
2. **Client ID**: paste the same Application (client) ID from step 1.
3. Tick the `access_as_user` scope and **Add**.

### 5. Create the four App Roles

The API maps Entra App Roles to its `Admin`, `Warehouse Mover`, `Operator`,
and `Viewer` permissions purely from the `roles` claim — there's no separate
group lookup.

1. Go to **App roles** -> **+ Create app role** and add four:

   | Display name    | Allowed member types | Value             |
   | --------------- | -------------------- | ----------------- |
   | Admin           | Users/Groups         | `Admin`           |
   | Warehouse Mover | Users/Groups         | `Warehouse_Mover` |
   | Operator        | Users/Groups         | `Operator`        |
   | Viewer          | Users/Groups         | `Viewer`          |

   The **Value** is what ends up in the token — the API matches it
   case-insensitively. The mover value must include the underscore:
   `Warehouse_Mover`.
2. Tick *Do you want to enable this app role?* on each, **Apply**.

### 6. Assign users (or groups) to roles

App roles are dormant until users are assigned to them via the matching
*enterprise application*:

1. From the same app, click **Managed application in local directory** at the
   top of the *Overview* page (this opens the enterprise app).
2. **Users and groups** -> **+ Add user/group**.
3. Pick a user or group, then **Select a role** -> choose one of the four
   roles and **Assign**.
4. Repeat for everyone who needs access. Users with **no** role assigned will
   be rejected at sign-in time (the API returns 403 with no `roles` claim).

> Optional but recommended: **Properties** -> set *Assignment required?* to
> **Yes**, so only assigned users can sign in.

### 7. Generate a client secret

The API uses this for the Microsoft Graph (sendMail) client-credentials flow.
The SPA itself never sees it.

1. Back on the app registration, **Certificates & secrets** -> **+ New client
   secret**.
2. Give it a description and pick an expiry that fits your rotation policy
   (24 months max). Click **Add**.
3. **Copy the Value immediately** (it disappears on refresh) into
   `ENTRA_CLIENT_SECRET` in `.env`.

> Set yourself a calendar reminder a few weeks before the expiry — when it
> rolls over, alert emails silently stop until you generate a new one.

### 8. Fill in `.env` and restart

```env
ENTRA_TENANT_ID=<directory-tenant-id>
ENTRA_CLIENT_ID=<application-client-id>
ENTRA_CLIENT_SECRET=<the-secret-value-from-step-7>
ENTRA_API_AUDIENCE=api://<application-client-id>
```

The `VITE_*` mirrors at the bottom of `.env` already pull from these, so you
don't need to duplicate them. Then:

```bash
docker compose up -d --build
```

Open the app, click **Sign in with Microsoft**, accept the consent prompt
once per user, and you should land on the dashboard with the role you were
assigned.

### Troubleshooting SSO

| Symptom                                               | Likely cause                                                                              |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `AADSTS50011 redirect URI mismatch`                   | Origin not listed under **Authentication** as an SPA redirect URI (must be exact, incl. scheme). |
| `AADSTS65001 user has not consented`                  | The SPA isn't pre-authorized for `access_as_user` (step 4) and you don't have admin consent. |
| `AADSTS50020` / `AADSTS50058` for **every** user, error shown on Microsoft's page before any redirect back | `ENTRA_TENANT_ID` in `.env` points at the wrong directory (e.g. the developer's tenant instead of the customer's). The `@<your-domain>` accounts simply don't exist there. Fix the GUID, then `docker compose up -d --build web` so the new value is baked into the SPA bundle. |
| `AADSTS50194` / `AADSTS500200` for every user         | The app registration's **Supported account types** is set to *Personal Microsoft accounts only*. Flip it to *Accounts in this organizational directory only* under **Authentication** -> **Supported account types**. |
| `AADSTS700016` after a fresh `.env` change            | `ENTRA_CLIENT_ID` doesn't exist in the configured tenant, **or** the `web` container wasn't rebuilt — the bundle still has the old client ID baked in. Rerun `docker compose up -d --build web`. |
| `AADSTS53003` / `AADSTS53000`                         | A Conditional Access policy in the user's tenant is blocking the app. Check *Entra admin center* -> *Protection* -> *Sign-in logs* for the user's UPN to see which policy fired, then exclude this app or grant the required control. |
| Generic *"We couldn't sign you in"* with a specific service-account UPN auto-filled (e.g. `exclaimer@…`) | Browser autofill or a stale Microsoft session cookie is feeding a non-interactive service account into the form. Reproduce in an incognito window — the SPA now passes `prompt=select_account` so the picker is forced. |
| Generic *"We couldn't sign you in"* and the failing authorize URL contains `login_hint=<service-account>@…` and `X-AnchorMailbox=Oid:…` | MSAL had a service mailbox (e.g. `exclaimer@…`) cached in `localStorage` from a previous attempt and was silently telling Microsoft "sign this user in as that account". The Sign in button now wipes `getAllAccounts()` via `clearCachedAccounts()` and passes `loginHint: undefined`, but pre-existing tabs may still need a hard refresh after the rebuild. If users on older browser sessions hit this, ask them to clear `localStorage` for the SPA origin once. |
| `AADSTS90014: required field 'request' is missing` after the user pressed Back | Harmless. The browser back button re-submitted the authorize URL without a fresh PKCE state, so Microsoft rejected it. Just click *Sign in with Microsoft* again — the SsoErrorBanner now recognises this code and tells the user the same thing. |
| SPA visibly refreshes several times through Microsoft's *"loading your account"* page before landing on the static *"We couldn't sign you in"* error | The 401 response interceptor in [web/src/api/client.ts](web/src/api/client.ts) used to call `msalInstance.loginRedirect()` with **no arguments** on every 401, which (a) sent only the default `openid profile offline_access` scopes (no `prompt`, no domain hint), and (b) re-fired on every failed `useQuery` request, looping until Microsoft gave up. It now clears the MSAL cache instead so `useIsAuthenticated()` flips to `false` and the AuthGate re-renders with the sign-in form. Make sure you `docker compose up -d --build web` after pulling this fix — the old behaviour is baked into older bundles. |
| Authorize URL contains `X-AnchorMailbox=Oid:…@9188040d-6c67-4c5b-b112-36a304b66dad` for a user whose UPN is on a work domain (`@scanmaster.gr`, `@scanning.gr`, etc.) | `9188040d-6c67-4c5b-b112-36a304b66dad` is the **Microsoft personal-accounts (MSA) tenant**. MSAL has cached the user as a personal Microsoft account, but the authority URL is your single-tenant work tenant — Microsoft can't reconcile the two. Caused either by a one-time MSAL cache corruption (the new `clearCachedAccounts()` on the Sign in button fixes it on next click) or by the user genuinely having a personal MS account with the same email as their work account. If it persists, ask the user to clear `localStorage` for the SPA origin once. |
| API returns 403 right after a successful login        | The user has no App Role assignment in the enterprise app (step 6).                       |
| API returns 401 `invalid token: ...`                  | `ENTRA_API_AUDIENCE` in `.env` doesn't match the *Application ID URI* on the registration. |
| `crypto_nonexistent` in browser console               | You opened the SPA over plain HTTP from a non-`localhost` host. MSAL needs a secure context — put it behind HTTPS or use the local-admin tab. |

## Setting up alert emails (Microsoft Graph)

The API sends low-inventory and max-capacity alerts via Microsoft Graph's
`/users/{from}/sendMail` endpoint, using the **same app registration** with
its client secret. If the Graph variables aren't set, alerts are still raised
in-app — the email step is just skipped.

### 1. Pick (or create) a service mailbox

You need a mailbox to send *from*. Either of these works:

- A **shared mailbox** (no license required, recommended) — e.g.
  `warehouse-alerts@example.com`.
- A regular user mailbox dedicated to the app.

Note the SMTP address; it goes into `ALERT_EMAIL_FROM`.

### 2. Add the Graph permission

1. In the app registration, **API permissions** -> **+ Add a permission** ->
   **Microsoft Graph** -> **Application permissions** (not Delegated).
2. Search for and tick **`Mail.Send`**, then **Add permissions**.
3. Click **Grant admin consent for `<tenant>`** at the top of the list.
   The Status column should turn green for `Mail.Send`.

> `Mail.Send` (Application) lets the app send mail as **any** mailbox in the
> tenant. The next step locks that down to the one mailbox we actually use.

### 3. Restrict the app to one mailbox (recommended)

Use an **Application Access Policy** in Exchange Online so this app
registration can only send as `ALERT_EMAIL_FROM`:

```powershell
# One-time, from a workstation with the Exchange Online module installed.
Connect-ExchangeOnline

# Allow only the alerts mailbox.
New-ApplicationAccessPolicy `
  -AppId           "<ENTRA_CLIENT_ID>" `
  -PolicyScopeGroupId "warehouse-alerts@example.com" `
  -AccessRight     RestrictAccess `
  -Description     "Warehouse Box Tracker - alerts mailbox only"

# Confirm.
Test-ApplicationAccessPolicy `
  -Identity "warehouse-alerts@example.com" `
  -AppId    "<ENTRA_CLIENT_ID>"
# AccessCheckResult should be 'Granted'.

# Test a different mailbox to confirm it's denied.
Test-ApplicationAccessPolicy `
  -Identity "someone-else@example.com" `
  -AppId    "<ENTRA_CLIENT_ID>"
# AccessCheckResult should be 'Denied'.
```

Policy changes can take up to ~30 minutes to propagate — expect the first
real send attempt after rollout to potentially fail and then start working.

### 4. Configure recipients

`ALERT_EMAIL_TO` is a comma-separated list. Use a distribution list so that
the on-call rotation can be changed without a redeploy:

```env
ALERT_EMAIL_FROM=warehouse-alerts@example.com
ALERT_EMAIL_TO=ops@example.com,warehouse-leads@example.com
```

### 5. Verify

Restart the API (`docker compose up -d --build api`) and trigger an alert:

1. Sign in as **Admin** -> **Settings** -> raise *Low inventory threshold* on
   any warehouse to a value above its current stock and save.
2. Within ~30 seconds the in-app *Alerts* panel should show a new entry.
3. Check the inbox of an `ALERT_EMAIL_TO` recipient for a *"Warehouse alert"*
   message.

Tail the API logs while you test — Graph errors are logged at WARNING level
under the `warehouse.graph` logger, which is invaluable when consent or
mailbox-policy hasn't propagated yet:

```bash
docker compose logs -f api | grep warehouse.graph
```

### Troubleshooting alert email

| Log line                                      | What it means                                                                |
| --------------------------------------------- | ---------------------------------------------------------------------------- |
| `graph not configured; skipping alert email`  | One of `ENTRA_*`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` is empty in `.env`.   |
| `graph token acquisition failed: ...`         | Wrong tenant ID, wrong client secret, or admin consent never granted.        |
| `graph sendMail failed: 403 ...`              | `Mail.Send` (Application) is missing, or the access policy denies this mailbox. |
| `graph sendMail failed: 404 ...`              | `ALERT_EMAIL_FROM` doesn't resolve to a mailbox in this tenant.              |
| `graph sendMail error: ...`                   | Network/TLS error reaching `graph.microsoft.com` from the API container.     |

## Layout

```
.
├── docker-compose.yml
├── .env.example
├── api/                     # FastAPI service
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── db.py
│       ├── auth.py          # Entra JWT validation
│       ├── deps.py          # current_user / require_role
│       ├── events.py        # in-process SSE pub/sub
│       ├── models/
│       ├── schemas/
│       ├── routers/
│       ├── services/        # alerts, graph_email, exports
│       └── jobs/scheduler.py
└── web/                     # React SPA + Nginx reverse proxy
    ├── Dockerfile
    ├── nginx.conf
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── auth/
        ├── api/
        ├── pages/
        ├── components/
        └── hooks/
```

## Local (break-glass) admin

There is always a built-in local admin account so you can sign in even if M365
SSO is unavailable (Entra outage, mis-configured app registration, etc.).

- The API provisions it on first startup with `BOOTSTRAP_ADMIN_USERNAME` and
  `BOOTSTRAP_ADMIN_PASSWORD` from `.env`. `must_change_credentials` is set to
  `true` so the next login forces a username + password change.
- The local login endpoint signs an HS256 JWT using `LOCAL_JWT_SECRET`. If
  that env var is empty, `POST /api/auth/login` returns 503 (local auth
  disabled) and only Entra SSO works.
- All gated endpoints return HTTP 428 (Precondition Required) until the admin
  has chosen their own credentials. The SPA renders a forced "Set your
  credentials" wizard automatically.
- Provisioning is idempotent: restarting the API after the admin has rotated
  their password does **not** reset it.

## Roles & access control

Roles come from the `roles` claim on the Entra access token (App Roles assigned
in the Enterprise App). The API also keeps a local `users` row per user and an
admin can disable a user (`is_active = false`) without touching Entra.

| Role            | Can read | Can write boxes | Can fulfil requests | Can manage settings |
| --------------- | -------- | --------------- | -------------------- | ------------------- |
| Viewer          | yes      | no              | no                   | no                  |
| Operator        | yes      | yes             | no                   | no                  |
| Warehouse Mover | yes      | no              | yes                  | no                  |
| Admin           | yes      | yes             | yes                  | yes                 |

Every non-admin role is also restricted by the user's warehouse assignments.
Any user can submit a request for an assigned warehouse. Movers approve or
reject requests, prepare them, mark them ready, attach the corresponding ERP
note, start transport, and record arrival. The original requester confirms an
inbound delivery; a mover confirms returned boxes at the warehouse. Inventory
changes only at that final confirmation step.

## Lots domain and permissions

Lots are durable identities rather than text stored independently on each box.
Names are trimmed, internal whitespace is collapsed, and identity is globally
case-insensitive. A box stores `lot_id`; request items additionally retain an
immutable lot-name snapshot so later corrections do not rewrite history.

The Lots list and detail pages show non-archived boxes, status distribution,
visible warehouses, staged receipts, and completion:

`(Incomplete + Ready to Return + Returned) / (all non-quarantined boxes)`

When the denominator is zero the API returns `completion_percent: null` ("No
eligible boxes"), not `0%`. Non-admin metrics and exports are aggregated only
from warehouses in the caller's ACL. Operators and admins may create/select a
lot while receiving inventory. Only admins may globally rename a lot or
reassign one box to another lot; both operations require a reason, optimistic
version, collision checks, and audit events. Renaming never merges/deletes
identities, and box reassignment never rewrites request snapshots.

## ERP delivery and return notes

The ERP remains the system that creates official delivery/return documents.
This app stores a manually uploaded PDF, JPEG, or PNG plus its ERP reference:

1. A mover approves a submitted request.
2. The mover generates the delivery or return note in the ERP.
3. On the request detail page, upload the document and enter its ERP reference.
4. The applicable current note is required before the request can move to
   **Delivering** or **Collecting**.
5. Uploading a replacement marks the previous version non-current but retains
   it for audit. The RustFS bucket is private; downloads always pass through
   the API's authentication and warehouse ACL.

Set non-default `RUSTFS_ACCESS_KEY` and `RUSTFS_SECRET_KEY` values in `.env`.
The Compose stack stores objects in the `rustfs-data` volume and metadata in
Postgres. Back up **both** `postgres-data` and `rustfs-data` together; restoring
only one side leaves document metadata or files orphaned. Test a paired restore
regularly and do not expose RustFS port 9000 outside the trusted network.

ERP connectivity itself is deliberately deferred. Manual ERP references and
versioned uploads remain the supported integration boundary; see
[`docs/ERP_INTEGRATION_TODO.md`](docs/ERP_INTEGRATION_TODO.md) for the contract
and security decisions required before any connector is built.

## Request workflow configuration

Request planning and receipt governance are configured per warehouse in
**Settings**:

- lead time, safety-stock percentage, 30/90-day history weights, and optional
  forecast adjustment,
- receipt mode (`auto_complete` or `admin_review`),
- required ERP document, quarantine rules for imported/manual receipts, and
  optional two-person approval threshold.

Users can independently enable or disable request email while retaining in-app
notifications. Graph delivery uses the existing `ENTRA_*`,
`ALERT_EMAIL_FROM`, and `PUBLIC_BASE_URL` settings; request mail is persisted in
an outbox and retried by the scheduler. Object limits and RustFS credentials use
the existing `DOCUMENT_MAX_BYTES` and `RUSTFS_*` settings.

## Request API

The OpenAPI page at `/api/docs` is authoritative. Key endpoint groups are:

- `GET/POST /api/requests` plus filters for status, priority, assignee, and
  operational queues,
- lifecycle actions: `approve`, `prepare`, `mark-ready`, `start-transit`,
  `mark-arrived`, and `complete`,
- recovery actions: `hold`, `resume`, `reschedule`,
  `report-failed-delivery`, and `retry-transport`,
- coordination, comments, supporting attachments, ERP documents,
  discrepancies/photos, follow-up submission, events, suggestions, return
  sources/candidates, reconciliation, and analytics,
- `/api/notifications`, `/api/xlsx-mapping-templates`, and request report
  endpoints under `/api/exports`.

Lot endpoints are:

- `GET/POST /api/lots`, `GET /api/lots/options`, and
  `GET /api/lots/{lot_id}`,
- `GET /api/lots/{lot_id}/boxes`,
- `PATCH /api/lots/{lot_id}/rename` (admin, reason + `expected_version`),
- `POST /api/boxes/{box_id}/reassign-lot` (admin, reason +
  `expected_lot_version`),
- ACL-scoped `GET /api/exports/lots.csv` and `/api/exports/lots.xlsx`.

Box, request-item, and return-candidate responses include `lot_id`; `lot`
remains the display/snapshot field. Lot mutations and inventory/request changes
publish warehouse-scoped SSE invalidations.

Every mutating request action requires `expected_version`; completion also uses
an idempotency key. Warehouse ACL and role checks are enforced server-side.
Lifecycle and notification mutations publish SSE events so active clients
invalidate the relevant request, inventory, and dashboard queries.

## Development

You can run things directly without Docker if you prefer; see
[`api/README.md`](api/README.md) and [`web/README.md`](web/README.md).

## Deploying migration 0027

`0027_first_class_lots` is a coordinated cutover from `boxes.lot` to the
required `boxes.lot_id` foreign key. Stop old API instances, run the read-only
`api/scripts/lot_migration_preflight.py` against production, reconcile every
blank lot or normalized `(lot, box_number)` collision (including archived
boxes), apply Alembic, and then deploy the matching API/web build. Generate
offline SQL with the production PostgreSQL dialect. Full commands, rollback
constraints, and operator checks are documented in
[`docs/FIRST_CLASS_LOTS_DEPLOYMENT.md`](docs/FIRST_CLASS_LOTS_DEPLOYMENT.md).
