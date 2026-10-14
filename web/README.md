# Warehouse Tracker Web

React 18 + Vite + TypeScript + Tailwind. MSAL.js handles Entra ID sign-in;
TanStack Query handles API state; an SSE hook keeps the UI live.

## Local development (without Docker)

```bash
npm ci
cp ../.env.example .env.local
# fill in VITE_* values
npm run dev
```

The dev server proxies `/api` to <http://localhost:8000>, so run the API
alongside it.

## Build

```bash
npm test
npm run build
npx tsc -p tsconfig.e2e.json
npx playwright install chromium
npm run test:e2e
```

The Docker image runs the same build behind Nginx and reverse-proxies `/api`
to the `api` service inside the compose network.

Browser tests use mocked API responses for authenticated workflows and the
real production service worker for offline cache isolation. Only the app shell
is available offline; authenticated inventory and identity always use the network.
