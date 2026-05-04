# Warehouse Tracker Web

React 18 + Vite + TypeScript + Tailwind. MSAL.js handles Entra ID sign-in;
TanStack Query handles API state; an SSE hook keeps the UI live.

## Local development (without Docker)

```bash
npm install
cp ../.env.example .env.local
# fill in VITE_* values
npm run dev
```

The dev server proxies `/api` to <http://localhost:8000>, so run the API
alongside it.

## Build

```bash
npm run build
```

The Docker image runs the same build behind Nginx and reverse-proxies `/api`
to the `api` service inside the compose network.
