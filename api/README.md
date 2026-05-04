# Warehouse Tracker API

FastAPI service that backs the warehouse box tracker.

## Local development (without Docker)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

# point at any postgres
export DATABASE_URL=postgresql+psycopg://warehouse:warehouse@localhost:5432/warehouse
alembic upgrade head
uvicorn app.main:app --reload
```

OpenAPI docs at <http://localhost:8000/api/docs>.

## Tests / lint

```bash
ruff check app alembic
pytest
```

## Layout

```
app/
  main.py          FastAPI app + lifespan + router wiring
  config.py        pydantic-settings: env -> Settings
  db.py            SQLAlchemy engine + session
  auth.py          Entra JWT validation (JWKS)
  deps.py          current_user / require_role
  events.py        in-process pub/sub (SSE backend)
  models/          SQLAlchemy ORM models
  schemas/         pydantic schemas (request/response)
  routers/         FastAPI routers, one per resource
  services/        domain logic (boxes, alerts, exports, graph email)
  jobs/scheduler.py APScheduler tick that calls services.alerts.evaluate_safe
alembic/
  env.py
  versions/0001_initial.py   creates schema + seeds 3 warehouses
```
