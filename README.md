# Warehouse Box Tracker

A small, dockerized web app for tracking the status of boxes across a 3-building
warehouse. Built with **FastAPI**, **React (Vite + TypeScript)**, **Postgres 16**,
**Microsoft Entra ID** (M365 SSO), live updates over **SSE**, CSV/XLSX exports,
and threshold alerts (in-app + email via Microsoft Graph).

## Features

- Per-warehouse live dashboard (received today, returned today, in-progress,
  ready-to-return, capacity bar, open alerts).
- Searchable / filterable boxes table with inline status transitions.
- Full audit trail per box (timeline of events).
- CSV and XLSX exports honouring the current filters.
- Low-inventory and max-capacity alerts in-app and via Graph email.
- Microsoft 365 SSO with three roles: **Admin**, **Operator**, **Viewer**.

## Stack

| Layer    | Tech                                                              |
| -------- | ----------------------------------------------------------------- |
| Frontend | React 18, Vite, TypeScript, Tailwind, shadcn/ui, TanStack Query, MSAL.js |
| Backend  | FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, APScheduler, msal    |
| Database | Postgres 16                                                       |
| Auth     | Microsoft Entra ID (Authorization Code + PKCE / JWT validation)   |
| Runtime  | Docker Compose                                                    |

## Quick start

```bash
cp .env.example .env
# Fill in ENTRA_*, LOCAL_JWT_SECRET, BOOTSTRAP_ADMIN_* values, then:
docker compose up --build
```

Open <http://localhost:8080>.

> **First sign-in.** If Entra ID isn't configured yet, use the **Local admin**
> tab on the sign-in screen with the username/password you set in `.env`
> (defaults: `admin` / `ChangeMe!2026`). The app forces you to pick a new
> username + password before letting you in. The default credentials are an
> **emergency-access fallback** — Microsoft 365 SSO is the recommended path.

The API exposes its OpenAPI docs at <http://localhost:8000/api/docs>.

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

### 5. Create the three App Roles

The API maps Entra App Roles to its `Admin`, `Operator`, `Viewer` permissions
purely from the `roles` claim — there's no separate group lookup.

1. Go to **App roles** -> **+ Create app role** and add three:

   | Display name | Allowed member types | Value      |
   | ------------ | -------------------- | ---------- |
   | Admin        | Users/Groups         | `Admin`    |
   | Operator     | Users/Groups         | `Operator` |
   | Viewer       | Users/Groups         | `Viewer`   |

   The **Value** is what ends up in the token — the API matches it
   case-insensitively, so `Admin` / `admin` / `ADMIN` all work, but the value
   must be exactly one of those three words.
2. Tick *Do you want to enable this app role?* on each, **Apply**.

### 6. Assign users (or groups) to roles

App roles are dormant until users are assigned to them via the matching
*enterprise application*:

1. From the same app, click **Managed application in local directory** at the
   top of the *Overview* page (this opens the enterprise app).
2. **Users and groups** -> **+ Add user/group**.
3. Pick a user or group, then **Select a role** -> choose one of the three
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

| Role     | Can read | Can write boxes | Can manage thresholds & users |
| -------- | -------- | --------------- | ----------------------------- |
| Viewer   | yes      | no              | no                            |
| Operator | yes      | yes             | no                            |
| Admin    | yes      | yes             | yes                           |

## Development

You can run things directly without Docker if you prefer; see
[`api/README.md`](api/README.md) and [`web/README.md`](web/README.md).
