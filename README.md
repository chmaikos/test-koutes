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

## Entra ID setup (one-time)

1. **Create an app registration** in the Entra admin centre.
2. **Expose an API** -> set the Application ID URI to
   `api://<application-client-id>` and add a scope `access_as_user`.
3. **Add a redirect URI** of type *Single-page application* pointing to your web
   origin (e.g. `http://localhost:8080`).
4. **App roles** -> create three roles with values `Admin`, `Operator`, `Viewer`
   (member type *Users/Groups*). Assign them via *Enterprise Applications ->
   Users and groups*.
5. **API permissions** -> *Microsoft Graph* -> *Application* -> `Mail.Send`,
   then grant admin consent. The service mailbox in `ALERT_EMAIL_FROM` must be
   reachable by this app (or scoped via an Exchange application access policy).
6. Generate a **client secret** and copy it into `ENTRA_CLIENT_SECRET`.
7. Copy the Tenant ID, Client ID and Application ID URI into `.env`.

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
