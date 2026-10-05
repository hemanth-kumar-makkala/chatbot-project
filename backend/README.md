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

---

## LangGraph Studio (Local Visual Debugging)

LangGraph Studio provides visual, node-by-node debugging of the 9-node knowledge graph on the mock profile with zero cloud telemetry egress (`LANGSMITH_TRACING=false` and in-memory checkpointing via `MemorySaver`).

### 1. Start Studio Dev Server
From the repository root:
```bash
uv run --project backend langgraph dev
```

Output should show:
```
- Ready!
- API: http://127.0.0.1:2024
- Docs: http://127.0.0.1:2024/docs
- Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
```
*(Open the Studio URL in your browser. No data leaves your machine; the web client communicates directly with your local server at `http://127.0.0.1:2024`)*.

### 2. Send a Test Question
In the LangGraph Studio UI under the **Input** pane, select the `knowledge_assistant` graph and submit the following JSON state:
```json
{
  "request_id": "11111111-1111-1111-1111-111111111111",
  "status": "pending"
}
```
*(Note: Per architecture security invariants, `tenant_id`, `route`, `answer_mode`, and `evidence_state` are computed and verified by the engine from the authenticated session context and evidence, never trusted from input state).*

### 3. What You Should See
1. **Interactive Graph Topology**: The Studio canvas displays the 9 primary nodes (`guard_input`, `route`, `retrieve`, `rerank`, `assess_evidence`, `vision_read`, `generate`, `validate`, `respond`) along with the fallback node `safe_failure`.
2. **Step-by-Step Execution Sequence**: The run highlights each active node sequentially:
   `guard_input` &rarr; `route` &rarr; `retrieve` &rarr; `rerank` &rarr; `assess_evidence` &rarr; `generate` &rarr; `validate` &rarr; `respond`
3. **Execution Completion & State Output (Zero Content)**:
   - `status`: `"answered"`
   - `response_message_id`: `<uuid>` *(State holds message and entity IDs only; zero answer or prompt text is ever persisted to checkpoints)*
   - `evidence_ids`: `["chunk-syn-001"]`
   - `public_error_ref`: `null`
   - Duration: < 100ms

---

## Type Checking & Editor Diagnostics

`mypy` is the sole type authority for this codebase. Strict type checking is enforced across `app.core.*` and `app.ports.*` via:
```bash
cd backend
uv run mypy app tests
```

If using an IDE extension such as Pyrefly (`meta.pyrefly`), note that Pyrefly's protocol solver flags LangGraph's internal `StateT` bound protocol on `TypedDict` schemas (`[bad-specialization]`). This false positive is disabled at the project configuration level in `backend/pyproject.toml` via `[tool.pyrefly.errors] bad-specialization = false`. Alternatively, developers may disable the Pyrefly extension in VS Code / Antigravity IDE for this workspace in favour of Mypy. Python source types are never weakened to satisfy editor extensions.
