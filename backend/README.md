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
