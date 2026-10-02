# Backend Service (`backend/`)

## What Belongs Here
- FastAPI service application code (`app/api/`).
- Core business engine: LangGraph question flow, retrieval orchestration, ingestion pipeline, extraction, and security guards (`app/core/`).
- Engine ports/interfaces (`app/ports/`).
- Swappable adapter implementations for models, storage, parsers, and task queues (`app/adapters/`).
- Database models, migrations (Alembic), and PostgreSQL RLS policies (`app/db/`).
- Structured logging, run tracing, and audit telemetry (`app/observability/`).
- Runtime configuration profiles (`config/profiles/`).
- Automated tests: unit, integration, invariant, and eval tests (`tests/`).

## What Must NEVER Go Here
- Vendor SDK imports inside `app/core/` (all external vendors belong solely in `app/adapters/`).
- Real client documents or real protected health information (PHI).
- Hardcoded secrets, production credentials, or un-scrubbed API tokens.
- Frontend code or static UI bundle assets.

## Local Database Setup & Testing

### 1. Start PostgreSQL Container
Integration tests run against real PostgreSQL 16 (with pgvector) in Docker:
```bash
docker run -d --name cka-postgres -e POSTGRES_USER=cka_admin -e POSTGRES_PASSWORD=$POSTGRES_PASSWORD -e POSTGRES_DB=knowledge_assistant -p 5432:5432 pgvector/pgvector:pg16
```


### 2. Bootstrap Roles & Run Migrations
Bootstrap non-superuser roles (`app_user`, `retriever_user`) and run Alembic migrations:
```bash
cd backend
uv run python scripts/bootstrap_db_roles.py
uv run alembic upgrade head
```

### 3. Run Tests
- **Invariants only (fast, sub-second, runs on every pre-commit hook)**:
  ```bash
  uv run pytest tests/invariants
  ```
- **Full test suite (unit + invariants + integration against PostgreSQL)**:
  ```bash
  uv run pytest
  ```
- **Integration tests only**:
  ```bash
  uv run pytest tests/integration
  ```
