# Build Log

Append-only record of all completed tasks, architectural choices, and test records.

---

### [2026-10-02] TASK 0: Project Bootstrap, Architecture Rules, and Build Planning

- **Task ID**: TASK 0
- **What Was Built**:
  - Root configuration and rules: `AGENTS.md`, `.gitignore`, `.env.example`, `docker-compose.yml`, root `README.md`.
  - Agent governance and workflows: `.agents/rules/invariants.md`, `.agents/rules/architecture.md`, `.agents/rules/security-phi.md`, `.agents/rules/coding.md`, `.agents/workflows/next-task.md`, `.agents/workflows/review-task.md`, `.agents/workflows/close-task.md`.
  - Repository directory structure across `backend/` (`app/api`, `app/core`, `app/ports`, `app/adapters`, `app/db`, `app/observability`, `tests/`), `web/`, `eval/golden/`, `evals/reports/`, `prompts/`, and `docs/`.
  - Top-level `README.md` files defining folder scope and anti-patterns for each directory.
  - Central versioned prompt registry: `prompts/registry.yaml`.
  - Runtime configuration profiles in `backend/config/profiles/`: `mock.yaml`, `office-cpu.yaml`, `desktop-gpu.yaml`, `home-gpu.yaml`, `cloud.yaml`.
  - Phase implementation roadmaps: `docs/phases/PHASE-0.md` through `PHASE-7.md`.
  - Failure modes and fallback matrix: `docs/FAILURE_MODES.md`.
  - Architectural decision records: `docs/decisions/ADR-0000-template.md` and `docs/decisions/ADR-0001-prompt-registry.md`.
  - Unresolved architectural gaps and questions: `docs/OPEN_QUESTIONS.md`.
- **Tests Added**:
  - Planning and scaffolding only; test suites scheduled for TDD implementation starting in Task 1.1.
- **Decisions Made**:
  - Adopted runtime profile `desktop-gpu` (i7-12700F, 32 GB RAM, RTX 3050 6 GB with `qwen3-vl:4b`) as default real-model development target (ADR-0001 context).
  - Adopted centralized, versioned prompt registry in `prompts/registry.yaml` to ensure audit traceability (ADR-0001).
  - Formatted Phase 1 to execute on the 8 GB RAM dev machine with mock profile and 3 GB Docker cap.
- **Open Issues**:
  - Exact 9 node names in LangGraph question flow and 8 steps in ingestion pipeline noted from diagram placeholders; documented in `docs/OPEN_QUESTIONS.md`.
  - Compliance reviews for AI Studio migration and pilot client retention policy pending with Dan.

---

### [2026-10-02] TASK 0.1: Re-Sync Workspace with Updated Architecture Spec

- **Task ID**: TASK 0.1
- **What Was Built**:
  - Re-synced roadmap documents `docs/phases/PHASE-0.md` through `PHASE-7.md` to align with the authoritative roadmap table and exit gates:
    - Phase 0: Foundations (runs on planning, exit gate: Compliance route confirmed in writing).
    - Phase 1: Engine on mock profile (runs on 8 GB office PC, exit gate: Every invariant and failure-mode test passes on mock profile).
    - Phase 2: Ingestion (runs on office PC then desktop-gpu, exit gate: Golden docs ingested twice with zero duplicates; includes Knowledge page, no admin dashboard).
    - Phase 3: Retrieval and answers (runs on desktop-gpu, exit gate: Baseline metrics recorded, zero cross-tenant leaks; chat UI with streaming status lines and pdf.js source panel built here for Dan's demo).
    - Phase 4: Vision and extraction (runs on desktop-gpu, exit gate: False-fill and visual recovery baselines recorded; question-time vision read, dual read compare, normalized visual value comparison, side-by-side extraction table UI).
    - Phase 5: Hardening (runs on desktop-gpu, exit gate: Security suite passes, capacity curve drawn; guardrails, injection suite, cross-tenant adversarial suite, rate limits, Presidio scrubbing, feedback capture, Locust capacity curve, backups with tested restore).
    - Phase 6: Cloud staging on GCP (runs on GCP, exit gate: Scores hold on cloud, monthly cost measured; Cloud SQL, Cloud Storage, Cloud Run, Vertex AI, Secret Manager, self-hosted BGE-M3, synthetic data only).
    - Phase 7: Pilot with one client (runs on GCP, exit gate: Dan signs off and Chatbase is cancelled; real data under BAA, 2-week side-by-side with Chatbase).
  - Merged `eval/` into `evals/`: unified structure into `evals/golden/` and `evals/reports/`. Updated `evals/README.md`, root `README.md`, and all phase references. Removed obsolete `eval/` directory.
  - Updated `docker-compose.yml` to match spec comment line `# postgres(pgvector), api, worker, web, langfuse (optional)` and explicitly budget service memory to the 3 GB dev constraint.
  - Verified `backend/config/profiles/desktop-gpu.yaml` matches the spec (`qwen3-vl:4b` for answer and vision roles, embeddings and reranker on CPU).
  - Updated `docs/FAILURE_MODES.md` aligning all 12 failure modes with the 9 query flow nodes (1 `guard_input`, 2 `route`, 3 `retrieve`, 4 `rerank`, 5 `assess_evidence`, 6 `vision_read`, 7 `generate`, 8 `validate`, 9 `respond`, and `safe_failure`).
  - Updated `.agents/rules/architecture.md` and `.agents/rules/security-phi.md` incorporating normalized visual comparisons, request-scoped context, and no-admin-dashboard constraints.
  - Updated `docs/OPEN_QUESTIONS.md`: closed #1 (diagrams), #8 (self-hosted BGE-M3 in cloud), #10 (Presidio deferred to Phase 5), the dual-vision reading risk (normalized values), and IDs-only-state risk (request-scoped context). Added new gaps regarding Clarify path topics, extraction path graph structure, and BGE-M3 hosting topology.
- **Tests Added**:
  - Documentation and planning task; no application tests written.
- **Decisions Made**:
  - Realigned all phases and naming to match spec's build roadmap table.
  - Standardized on `evals/` root folder with `golden/` and `reports/` subfolders.
- **Open Issues**:
  - Specific GCP Cloud Run hosting configuration for self-hosted BGE-M3 (deferred to Phase 6).

---

### [2026-10-02] TASK 0.2: Clarify Grounding and Extraction Subgraph Architecture Decisions

- **Task ID**: TASK 0.2
- **What Was Built**:
  - Recorded architectural decisions resolving the open questions:
    - **Clarify Grounding**: Clarify path in Node 2 retrieves top 3 topics strictly via a lightweight read-only search of document titles and section headings through the `Retriever` port (scoped to tenant). Model never invents topics, enforcing Invariant 4.
    - **Extraction Subgraph**: Modeled as an independent LangGraph subgraph (`extract_fields` → `dual_read_compare` → `classify_confidence` → `validate_fields`) invoked by the main graph's route node, reusing `guard_input` and `respond`.
    - **BGE-M3 on GCP**: Sits behind `EmbeddingProvider` interface; hosting topology (dedicated internal Cloud Run microservice vs co-located inside API container) formally deferred to Phase 6 based on measured cold-start latency and cost.
  - Authored [`docs/decisions/ADR-0002-clarify-topics-and-extraction-subgraph.md`](file:///d:/chatbots-project/docs/decisions/ADR-0002-clarify-topics-and-extraction-subgraph.md).
  - Updated [`docs/OPEN_QUESTIONS.md`](file:///d:/chatbots-project/docs/OPEN_QUESTIONS.md) closing items 3.1 and 3.2, and marking 3.3 as a Phase 6 decision.
  - Updated [`docs/phases/PHASE-1.md`](file:///d:/chatbots-project/docs/phases/PHASE-1.md), [`docs/phases/PHASE-4.md`](file:///d:/chatbots-project/docs/phases/PHASE-4.md), and [`docs/phases/PHASE-6.md`](file:///d:/chatbots-project/docs/phases/PHASE-6.md) to integrate these contracts.
- **Tests Added**:
  - Documentation and architectural planning only.
- **Decisions Made**:
  - Adopted ADR-0002.

---

### [2026-10-02] TASK 0.3: Update .gitignore and First Commit Preparation

- **Task ID**: TASK 0.3
- **What Was Built**:
  - Rewrote `.gitignore` structured into 8 distinct categories with descriptive group headers:
    1. Secrets (`.env`, `.env.*`, `.env.local`, preserving `!.env.example`)
    2. Python (virtual environments, bytecode, caches, coverage, build/dist, egg-info)
    3. Node & Next.js (`node_modules/`, `web/node_modules/`, `.next/`, `out/`, build caches)
    4. Runtime Data & Storage (`data/`, `uploads/`, `backend/data/`, `postgres_data/`, local SQLite/DB files)
    5. AI Models and Caches (`models/`, `.cache/`, huggingface, ollama caches, model weight formats)
    6. Logs (`logs/`, `*.log`, npm/yarn debug logs)
    7. Tool State & Local Infrastructure (`.langgraph/`, `.langsmith/`, `langfuse_data/`, `docker_volumes/`)
    8. OS & Editor Clutter (`.DS_Store`, `Thumbs.db`, `.vscode/`, `.idea/`, editor swap files)
  - Verified repository tracking: confirmed that `.agents/`, `docs/`, `prompts/`, `evals/golden/`, `evals/reports/`, `AGENTS.md`, `.env.example`, and the spec file are fully unignored and tracked.
  - Workspace audit: verified that 0 current workspace files are ignored (all 59 files are tracked).
- **Tests Added**:
  - Configuration verification only.
- **Decisions Made**:
  - Maintained explicit inclusion of `evals/reports/` for saved evaluation reports.
  - Retained `!.env.example` exception.

---

### [2026-10-02] TASK 0.3b: Fix .gitignore Path Scoping

- **Task ID**: TASK 0.3b
- **What Was Built**:
  - Anchored runtime and model folders to the repository root in `.gitignore` using leading slashes:
    - `/data/`, `/uploads/`, `/backend/data/`, `/postgres_data/`
    - `/models/`, `/huggingface/`, `/.huggingface/`, `/ollama_models/`
    - Prevents accidental ignoring of nested source code folders with matching names, specifically ensuring `backend/app/adapters/models/` is tracked.
  - Added `.langgraph_api/` under Tool State & Local Infrastructure.
- **Tests Added**:
  - Configuration verification only; no git commands run.
- **Decisions Made**:
  - Explicitly anchored root data/model directories with leading slashes.

---

### [2026-10-02] Task 1.1: Pre-Commit Quality Checks, Tooling, and Python 3.12 Pinning

- **Task ID**: Task 1.1
- **What Was Built**:
  - Configured project line endings: added `.gitattributes` enforcing `eol=lf` for all repository text files.
  - Installed and pinned Python 3.12 via `uv` (`cpython-3.12.15-windows-x86_64-none`), creating virtual environment in `backend/.venv`.
  - Configured `backend/pyproject.toml` with version ranges for `ruff`, `mypy`, `pytest`, `pre-commit`, and `detect-secrets`.
  - Configured strict type checking in Mypy targeting `app.core.*` and `app.ports.*` (`disallow_untyped_defs = true`, `check_untyped_defs = true`).
  - Configured Ruff with Python 3.12 target, 100 char line limit, and comprehensive lint rules (`E`, `W`, `F`, `I`, `B`, `C4`, `UP`, `ARG`, `SIM`).
  - Generated and locked dependencies into `backend/uv.lock` via `uv lock`.
  - Created `.pre-commit-config.yaml` at repo root with hooks:
    - `trailing-whitespace`
    - `end-of-file-fixer`
    - `check-added-large-files` (`--maxkb=1000`)
    - `detect-private-key`
    - `mixed-line-ending` (`--fix=lf`)
    - `detect-secrets` (excluding `uv.lock`)
    - `ruff` and `ruff-format`
    - `mypy`
- **Tests Added**:
  - Validated via direct execution of acceptance check suite (`uv run python --version`, `uv lock --check`, `uv run ruff check .`, `uv run mypy app`, `uv run pytest`, `pre-commit run --all-files`).
- **Decisions Made**:
  - Kept version ranges in `pyproject.toml` and pinned exact hashes in `uv.lock`.
  - Configured `backend/app` package packaging via Hatchling.

---

### [2026-10-02] Task 1.2: Architecture Import Boundary Enforcement Rules

- **Task ID**: Task 1.2
- **What Was Built**:
  - Implemented AST boundary analyzer `backend/tests/invariants/boundary_checker.py`:
    - Strict allowlist enforcement:
      - `app/core/`: Only Python standard library (`sys.stdlib_module_names`), `app.core.*`, `app.ports.*`, `langgraph`, `pydantic`, `typing_extensions`.
      - `app/ports/`: Only Python standard library, `app.ports.*`, `pydantic`, `typing_extensions`.
      - `app/adapters/`: May import `app.ports.*` and vendor libraries, but NEVER `app.core.*`.
      - Composition root: Only `app/wiring.py` is authorized to import `app.core.*` and `app.adapters.*` together.
    - Relative import resolution (`.` and `..` resolved against module hierarchy).
    - Inspection of type checking blocks (`if TYPE_CHECKING:` traversed).
    - Dynamic import ban: Flagging `__import__` and `importlib.import_module` inside `core` and `ports`.
    - Environment read ban: Flagging `os.environ`, `os.getenv`, direct imports (`from os import getenv`), and aliased imports (`import os as o; o.environ[...]`) inside `app/core/` (configuration must be injected).
    - Directory scanner: `scan_directory_boundaries(root_dir)` recursive codebase scanner.
  - Authored Architectural Decision Record: `docs/decisions/ADR-0003-composition-root.md` establishing `app/wiring.py` as the sole composition root.
  - Added package initializers `backend/tests/__init__.py` and `backend/tests/invariants/__init__.py`.
- **Tests Added**:
  - `backend/tests/invariants/test_architecture_boundaries.py` (20 tests):
    - 16 self-tests with synthetic violation code snippets testing all boundary rules, dynamic import bans, environment access bans (including aliases and direct imports), ports vendor bans, and composition root rules.
    - 4 repository scan invariant tests running across `backend/app/core`, `backend/app/ports`, `backend/app/adapters`, and the entire codebase.
  - Followed strict TDD protocol:
    1. Tests written first with boundary checker raising `NotImplementedError` -> 20 failed.
    2. Boundary checker implemented -> 20 passed.
- **Decisions Made**:
  - Placed checker in `backend/tests/invariants/` (not in `app/`) to avoid polluting application production code.
  - Formulated ADR-0003 designating `app/wiring.py` as the sole composition root.
  - Added a fast local pre-commit hook `boundary-invariants` executing `uv --directory backend run pytest tests/invariants` (<0.05s) on every commit.
  - Extended boundary checker to enforce that no module anywhere in `app/` (including `app/api/`) outside `app/adapters/` and `app/wiring.py` may import `app.adapters.*`.
- **Follow-up Fixes Added**:
  - `boundary-invariants` hook registered in `.pre-commit-config.yaml` (`always_run: true`, `pass_filenames: false`).
  - Added self-test `test_violation_api_imports_adapters` (confirmed failing prior to checker update).
  - Added codebase scan test `test_repo_scan_app_api_clean`.
  - Expanded test suite to 22 invariant tests (all passing in 0.04s).
- **Open Issues**:
  - None. Clean pass across pytest, ruff lint, ruff format, mypy, and all pre-commit hooks.

---

### [2026-10-02] Task 1.3: Port Interface Definitions (Layer 5)

- **Task ID**: Task 1.3
- **What Was Built**:
  - Added approved `pydantic>=2.10.0,<3.0.0` dependency to `backend/pyproject.toml`, pinned exact hashes in `backend/uv.lock`, and synced `.venv`.
  - Implemented 9 pure Abstract Base Class (`abc.ABC`) interfaces in `backend/app/ports/`:
    - `ModelProvider`: `generate(...)`, `read_page(...)`, `extract_fields(...)`
    - `EmbeddingProvider`: `embed(...)`, abstract properties `model_id` and `version`
    - `DocumentParser`: `split_pages(...) -> AsyncIterator[PageArtifact]`, `extract(...) -> ParsedPageContent` (page-first invariant)
    - `Retriever`: `search(...) -> list[EvidenceId]`, `search_titles(...) -> list[str]` (ADR-0002 clarify grounding)
    - `StorageBackend`: `put(...)`, `get(...)`, `signed_url(...)`, `delete(...)`
    - `JobQueue`: `enqueue(...)`, `status(...)`
    - `AuthProvider`: `authenticate(...)`, `session(...)`, `roles(...)`
    - `Tracer`: `step_start(...)`, `step_end(...)`, `step_error(...)`
    - `Reranker`: `rerank(...)`
  - Defined shared domain error hierarchy in `backend/app/ports/errors.py`:
    - `PortError`, `ProviderTimeout` (retryable), `ProviderUnavailable` (retryable), `MalformedOutput`, `EmptyOutput`, `AuthenticationError`, `StorageError`, `ParseError`, `QueueError` (retryable).
  - Defined typed data contracts in `backend/app/ports/types.py`:
    - `UserRole` enum (`platform_admin`, `client_admin`, `staff`)
    - `TenantContext` model derived from session
    - `EvidenceType` enum (`text`, `visual`, `table`) and typed `EvidenceId` model
    - `ModelResponse`, `PageReading`, `ExtractedFields` carrying `model_id` and `model_version`
    - `ReadState` enum (`present`, `unreadable`, `not_present`) with explicit `UNREADABLE` support for single-read observations
    - `StorageKey` enforcing `tenant_id` on all object storage operations
    - Typed telemetry models: `StepStartPayload`, `StepEndPayload`, `StepErrorPayload` (error code and error type only, no free-form content)
  - Authored Architectural Decision Record: `docs/decisions/ADR-0004-port-interfaces.md`.
  - Updated `docs/FAILURE_MODES.md` adding Tracer store failure mode with fail-safe request survival verification.
  - Closed Tracer execution mode item 1.8 in `docs/OPEN_QUESTIONS.md`.
- **Tests Added**:
  - `backend/tests/unit/test_ports_contracts.py` (29 test assertions across 10 test suites):
    - Red phase: tests written first, verified failing with `ImportError`.
    - Green phase: all 29 unit tests passing + 22 boundary invariant tests passing = 51 total tests passing in 0.21s.
- **Decisions Made**:
  - All I/O operations are async coroutines across all 9 ports (ADR-0004).
  - Tracer operates asynchronously for `run_steps` database persistence; fail-safe contract guarantees tracing failures never fail user requests.
  - Operational logs (metadata only) use standard Python logging outside `ports/`.
  - Restructured `backend/pyproject.toml` dependencies: moved `ruff`, `mypy`, `pytest`, `pre-commit`, and `detect-secrets` into `[dependency-groups] dev = [...]`. Only `pydantic` remains in `[project].dependencies` as runtime dependency. Relocked with `uv lock` and confirmed local tool installation with `uv sync`.
- **Open Issues**:
  - None. Clean pass across pytest, ruff lint, ruff format, mypy strict check, and pre-commit hook suite.

---

### [2026-10-02] Task 1.4a: Database Schema & Row-Level Security (RLS)

- **Task ID**: Task 1.4a
- **What Was Built**:
  - Added pinned dependencies: `sqlalchemy[asyncio]>=2.0.36,<3.0.0`, `alembic>=1.14.0,<2.0.0`, `psycopg[binary]>=3.2.3,<4.0.0` in `[project].dependencies`, and `pytest-asyncio>=0.24.0,<1.0.0` in `[dependency-groups] dev`. Locked with `uv lock` and synced `.venv`.
  - Created idempotent local role bootstrap script `backend/scripts/bootstrap_db_roles.py`: creates non-superuser roles `app_user` (read-write) and `retriever_user` (read-only) with `NOSUPERUSER NOBYPASSRLS` and passwords read securely from `.env` (never in migrations or repo).
  - Created Alembic environment in `backend/app/db/migrations/` and initial migration `0001_initial_phase1_schema_and_rls.py`:
    - 8 Phase 1 tables: `tenants`, `users`, `assistants`, `memberships`, `conversations`, `messages`, `run_steps`, `audit_events`.
    - All tables use UUID primary keys with PostgreSQL `gen_random_uuid()`.
    - Composite foreign keys: `(tenant_id, parent_id) REFERENCES parent(tenant_id, id)` strictly preventing cross-tenant referencing.
    - Check constraint on `memberships.role` allowing only `'client_admin'` and `'staff'`.
    - Check constraint on `assistants` referencing `prompt_id` and `prompt_version` from `prompts/registry.yaml` (no `system_prompt` column).
    - Invariant 10 telemetry schema on `run_steps`: contains only `status`, `model_id`, `model_version`, `prompt_id`, `prompt_version`, `evidence_ids: UUID[]`, `public_error_ref`; zero prompt, document, or message content columns.
    - Enabled and forced Row-Level Security (`ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`) on every tenant table.
    - Transaction-local RLS policy reading `NULLIF(current_setting('app.current_tenant_id', true), '')::uuid`, returning 0 rows without raising errors when unscoped.
    - Restricted permissions: `app_user` has `SELECT, INSERT, UPDATE, DELETE` on operational tables but `INSERT` only on `audit_events`; `retriever_user` has `SELECT` only on tenant tables.
  - Implemented SQLAlchemy 2.0 async models in `backend/app/db/models.py`.
  - Implemented authorized session manager in `backend/app/db/session.py`:
    - `tenant_session(tenant_ctx: TenantContext)`: the single authorized gateway to tenant tables, executing `SET LOCAL app.current_tenant_id = :tenant_id` within a transaction.
    - `retriever_tenant_session(tenant_ctx: TenantContext)`: read-only tenant session bound to `retriever_user`.
    - `unscoped_session()`: session for global bootstrap / tests only.
  - Added boundary AST invariant test: `app/core/` importing `app.db` is strictly forbidden.
  - Tested migration rollback (`downgrade base`) and re-application (`upgrade head`).
- **Tests Added**:
  - `backend/tests/integration/test_db_schema_and_rls.py` (14 comprehensive integration tests against PostgreSQL in Docker `cka-postgres`):
    - Table existence and UUID primary keys across all 8 tables.
    - Meta-test: every table is either tenant-scoped with forced RLS or in `GLOBAL_TABLE_ALLOWLIST` (including LangGraph checkpoint tables).
    - Assistants schema lacks `system_prompt` and uses prompt registry references.
    - `run_steps` schema carries zero content/prompt columns.
    - `memberships.role` check constraint rejects unauthorized roles; `users.is_platform_admin` validated.
    - Composite FKs reject cross-tenant parent-child references.
    - Role privilege verification: `app_user` and `retriever_user` own 0 tables, are not superusers, and have `NOBYPASSRLS`.
    - Unscoped queries return 0 rows without error under RLS.
    - Tenant isolation: Tenant A cannot read, update, or delete Tenant B rows.
    - Cross-tenant insertion blocked (Tenant A context cannot insert row with Tenant B `tenant_id`).
    - Pooled connection leak test: consecutive transactions on the same pooled connection never leak tenant context.
    - Retriever role is strictly read-only (insert/update/delete denied).
    - `audit_events` insert-only policy: `app_user` can insert audit events, but update and delete are denied by PostgreSQL permission.
  - Added `test_violation_core_imports_db` to `backend/tests/invariants/test_architecture_boundaries.py`.
  - Total test suite: 66 passed (29 unit + 23 invariant + 14 integration).
- **Decisions Made**:
  - PostgreSQL container `cka-postgres` (`pgvector/pgvector:pg16`) established on port 5432 for local integration tests.
  - Passwords managed via environment variables and bootstrap script, keeping migration files and repo green under secret scanning.
  - Windows async psycopg requires `WindowsSelectorEventLoopPolicy` set at test runner and module entry points.
- **Follow-up Pre-Commit Hardening**:
  - `detect-secrets`:
    - Removed dummy default `sqlalchemy.url` connection string from `backend/alembic.ini`. `env.py` builds the URL dynamically from environment variables.

    - Added lightweight `.env` loader utility in `backend/app/db/env_utils.py` and called it in `migrations/env.py`, `session.py`, `bootstrap_db_roles.py`, and `tests/conftest.py`.
    - Removed password literal `"hash"` in `tests/integration/test_db_schema_and_rls.py`; test now consumes `test_user_password_hash` fixture from `tests/conftest.py` backed by `TEST_USER_PASSWORD_HASH` environment variable.
    - Scanned entire repository for password literals: removed dev password fallbacks from `docker-compose.yml`, `scripts/bootstrap_db_roles.py`, `backend/app/db/session.py`, and `backend/README.md`. Only `.env` holds actual values; `.env.example` holds empty placeholders.
  - `mypy hook`:
    - Replaced `mirrors-mypy` hook in `.pre-commit-config.yaml` with local system hook `uv --directory backend run mypy app tests` (`pass_filenames: false`, `always_run: true`). It runs within the project's virtualenv, resolving `sqlalchemy`, `pydantic`, `alembic`, and all test modules cleanly without isolated environment stub errors.
- **Open Issues**:
  - Platform Admin cross-tenant query topology documented in `docs/OPEN_QUESTIONS.md` (Item 2.1) for stakeholder review before Phase 5.

---

### [2026-10-02] Task 1.4b: Three Log Stores (Operational Logger, Tracer Store, Audit Store)

- **Task ID**: Task 1.4b
- **What Was Built**:
  - Operational Logger in `backend/app/observability/logger.py`:
    - Formats log records into strictly allowlisted, single-line JSON.
    - Enforces hard field allowlist: `ts`, `request_id`, `tenant_id`, `node`, `status`, `latency_ms`, `counts`, `route`, `profile`, `model_id`, `error_code`, `error_type`.
    - Automatically drops and counts any unallowlisted field (e.g. `question_body`, `prompt_content`, `document_text`) via `get_dropped_field_count()`.
    - Omits free-form format messages from output JSON.
    - Enforces Invariant 10: exception logging captures only `error_type` and `error_code`, strictly excluding raw exception messages and stack traces.
    - Contextvars propagation: `request_id_var` and `tenant_id_var` propagate across asynchronous execution tasks.
  - Trace Store Adapter in `backend/app/adapters/observability/tracer.py`:
    - Implements `Tracer` port (`step_start`, `step_end`, `step_error`).
    - Writes telemetry to `run_steps` in its own independent database transaction (`tenant_session`), ensuring trace records survive application request rollbacks.
    - Enforces fail-open contract with configurable `write_timeout_seconds` (default 1.0s). Bounded timeouts and database outages log warnings and return safely without raising exceptions to user requests.
  - Audit Log Store in `backend/app/observability/audit.py`:
    - `AuditLogger` appends compliance actions (`action`, `event_type`, `resource_id`, `user_id`) to `audit_events`.
    - Enforces fail-closed contract: if writing an audit event fails, `AuditError` is raised, forcing the audited compliance operation to abort and refuse.
  - Authored Architectural Decision Record: `docs/decisions/ADR-0005-observability-stores.md`.
- **Tests Added**:
  - `backend/tests/unit/test_operational_logger.py` (4 unit tests):
    - Validates allowed fields formatting and ISO 8601 UTC timestamp.
    - Validates unallowlisted fields are dropped and counted.
    - Validates no free-form message in JSON output.
    - Validates exception message and stack trace scrubbing.
  - `backend/tests/integration/test_tracer_and_audit_stores.py` (5 integration tests):
    - Tracer writes in own transaction and survives request transaction rollback.
    - Tracer write timeout aborts slow writes within bounded time without raising.
    - Tracer database outage fails open without raising.
    - Audit event successfully logged in `audit_events`.
    - Audit failure fails closed (raises `AuditError`).
  - Total test suite: 75 passed (33 unit + 23 invariant + 19 integration).
- **Decisions Made**:
  - Documented resilience difference in ADR-0005: Tracer is fail-open (telemetry never breaks requests); Audit is fail-closed (unrecorded compliance actions are forbidden).
- **Open Issues**:
  - None. Clean pass across pytest (75 passed in 1.20s), ruff, mypy, and pre-commit hook suite.

---

### [2026-10-02] Task 1.5: Mock ModelProvider with Configurable Failure Modes

- **Task ID**: Task 1.5
- **What Was Built**:
  - Implemented `MockModelProvider` in `backend/app/adapters/models/mock.py` implementing `ModelProvider`:
    - Normal operation:
      - `generate`: returns deterministic `ModelResponse` with content and usage tokens; generates schema-compliant structured output when `schema` (Pydantic model) is provided.
      - `read_page`: returns `PageReading` with visual text, confidence score, and normalized comparison values.
      - `extract_fields`: returns `ExtractedFields` with `ReadState.PRESENT` mapped to requested schema fields.
    - Explicit failure injection triggers via `set_failure(mode, failure_count, delay_seconds)`:
      - `MockFailureMode.TIMEOUT`: raises `ProviderTimeout` (retryable=True, error_code="PROVIDER_TIMEOUT").
      - `MockFailureMode.UNAVAILABLE`: raises `ProviderUnavailable` (retryable=True, error_code="PROVIDER_UNAVAILABLE").
      - `MockFailureMode.MALFORMED_OUTPUT`: raises `MalformedOutput` (retryable=False).
      - `MockFailureMode.EMPTY_OUTPUT`: raises `EmptyOutput` (retryable=False).
      - `MockFailureMode.SLOW_RESPONSE`: injects async sleep delay.
      - Transient recovery: fails exactly N times before cleanly recovering, enabling deterministic retry loop tests in the LangGraph graph.
- **Tests Added**:
  - `backend/tests/unit/test_mock_provider.py` (10 unit tests):
    - Tests text generation, structured output schema generation, page visual reading, and field extraction.
    - Tests failure injection for timeout, unavailable, malformed output, empty output, slow response, and transient failure recovery after N attempts.
  - Total test suite: 85 passed (43 unit + 23 invariant + 19 integration).
- **Decisions Made**:
  - Added deterministic failure counters to test transient retry recovery paths without flaky network dependencies.
- **Open Issues**:
  - None. Clean pass across pytest (85 passed in 1.32s), ruff, mypy, and all pre-commit hooks.

---

### [2026-10-02] Task 1.6: LangGraph 9-Node StateGraph, Extraction Subgraph, and Contract Tests

- **Task ID**: Task 1.6
- **What Was Built**:
  - Implemented the Primary 9-Node Knowledge StateGraph and Extraction Subgraph in `backend/app/core/graph.py` matching the architecture spec lines 185-235:
    - **Zero-Content Checkpoint Architecture**: `GraphState` holds control fields and entity IDs only (`request_id`, `tenant_id`, `route`, `answer_mode`, `evidence_ids`, `evidence_state`, `attempt_counters`, `status`, `public_error_ref`).
    - **Runtime Context (`GraphRuntimeContext`)**: Question text, evidence text, document binaries, and ports (`ModelProvider`, `Retriever`, `Tracer`) pass through LangGraph's unpersisted runtime context (`context_schema=GraphRuntimeContext`, `runtime: Runtime[GraphRuntimeContext]`). Checkpointers never store question, answer, or document content.
    - **9 Main Nodes**: `guard_input`, `route`, `retrieve`, `rerank`, `assess_evidence`, `vision_read`, `generate`, `validate`, `respond` + fallback `safe_failure`.
    - **Extraction Subgraph**: 4-node subgraph (`extract_fields` -> `dual_read_compare` -> `classify_confidence` -> `validate_fields`) that terminates at `validate_fields` and returns cleanly to the main graph for the shared `respond` node (no internal respond node).
    - **3-Branch Vision Read Conditional Edge**:
      1. `vision_ok` -> `generate`
      2. `vision_failed` + OCR confidence >= 0.70 -> `generate` (using OCR evidence)
      3. `vision_failed` + OCR confidence < 0.70 -> `respond` (with page refusal)
    - **Bounded Validation & Recursion Protection**: `validate` -> `generate` retries at most once tracked by counter in state; invocation `recursion_limit` safely traps infinite cycles and routes to `safe_failure`.
    - **Internal Retrieval Fallbacks**: Hybrid vector and keyword retrieval failure fallbacks handled seamlessly and gated by `assess_evidence`.
    - **Fail-Open Telemetry**: All node executions wrapped with `_execute_with_telemetry` recording `step_start`, `step_end`, and `step_error` via `Tracer` fail-open.
    - **Documentation Generator Script**: `backend/scripts/generate_graph_docs.py` compiles the StateGraph and writes exact Mermaid topology to `docs/graph.md`.
- **Tests Added**:
  - `backend/tests/unit/test_graph_contracts.py` (8 unit tests):
    - Knowledge graph nodes and edges match architecture spec.
    - Extraction subgraph ends after `validate_fields` and returns to main graph.
    - Committed `docs/graph.md` matches compiled Mermaid diagram.
    - In-memory `MemorySaver` checkpoint state AND metadata contain zero content strings.
    - Validation loop bounded to at most 1 retry, transitioning to refusal.
    - Vision read conditional edge tests all 3 branches (vision ok, ocr fallback, page refusal).
    - Invocation recursion limit traps infinite cycles into safe failure.
    - Telemetry wrapper calls tracer fail-open across nodes.
  - `backend/tests/unit/test_retrieval_fallbacks.py` (4 unit tests):
    - Vector index down falls back to keyword retrieval.
    - Keyword index down falls back to vector retrieval.
    - Reranker service down falls back to RRF retrieval rank.
    - Clarify route fetches grounded topic titles strictly from Retriever.
  - `backend/tests/integration/test_graph_end_to_end.py` (8 integration tests covering all 8 paths):
    - Path 1: Reliable text answer.
    - Path 2: Visual answer.
    - Path 3: General task route.
    - Path 4: Clarify route.
    - Path 5: Extraction route through subgraph to shared respond.
    - Path 6: Conflicting evidence detection.
    - Path 7: Refusal on weak or no evidence.
    - Path 8: Safe failure fallback on non-retryable error.
  - `backend/tests/integration/test_langgraph_postgres_checkpointer.py` (1 integration test):
    - PostgreSQL `AsyncPostgresSaver` checkpoint persistence verifies that both saved state channel values AND metadata contain zero prompt or evidence text.
  - Total test suite: 106 passed (55 unit + 23 invariant + 28 integration).
- **Decisions Made**:
  - Preserved strict architecture boundaries: `app/core/` does not import `langchain_core` directly; relies on `langgraph` core state and casting.
  - Used `AsyncConnectionPool(..., kwargs={"autocommit": True})` for postgres checkpointer to accommodate `CREATE INDEX CONCURRENTLY` in LangGraph migrations.
- **Open Issues**:
  - None. Clean pass across pytest (106 passed in 2.82s), ruff, mypy, and all 10 pre-commit hooks.

---

### [2026-10-02] Task 1.7: LangGraph Studio Setup for Local Debugging

- **Task ID**: Task 1.7
- **What Was Built**:
  - Configured LangGraph Studio setup for local, visual node-by-node debugging of the 9-node knowledge graph on the mock profile with zero cloud telemetry egress:
    - **Single Configuration Point (`langgraph.json`)**:
      - Retained single root `langgraph.json` pointing to `./backend/studio/entry.py:make_studio_graph` and removed duplicate `backend/langgraph.json`.
    - **Dedicated Studio Entrypoint (`backend/studio/entry.py`)**:
      - Separated all Studio-specific helpers cleanly away from production composition wiring (`backend/app/wiring.py`).
      - Created `make_studio_graph()` factory function compiling the knowledge StateGraph with an in-memory checkpointer (`MemorySaver`).
      - Created `StudioCompiledGraph` (subclass of `CompiledStateGraph`) providing synthetic healthcare default context (`get_default_studio_context()`) with `MockModelProvider` and `MockRetriever` whenever `context is None` (the Studio UI default invocation mode), while preserving user-provided context when passed explicitly.
      - Overrode `copy(update=...)` and added default `thread_id` generation in `_ensure_config` to ensure LangGraph server internal graph cloning preserves the subclass wrapper.
    - **Zero-Content Checkpoint & Answer Text Isolation**:
      - Removed `response_payload` completely from `GraphState`. Answer text lives strictly in unpersisted `GraphRuntimeContext.response_text`; `GraphState` retains only `response_message_id`.
      - Isolated extraction field values to `GraphRuntimeContext.extracted_fields`, storing only `extracted_field_ids` in `GraphState`.
      - Extended in-memory and PostgreSQL checkpointer invariant tests to assert `ALLOWED_CONTROL_KEYS`, ban answer content, ban free-text sentences across state channels, and confirm test catches `response_payload`.
    - **Untrusted Input State Protection**:
      - `node_guard_input` enforces `tenant_id` from `runtime.context.tenant` exclusively and initializes `status = "in_progress"`, rejecting forged caller `tenant_id` and `status`.
      - `node_route` computes `route` and `answer_mode` dynamically from question context or mock context forcing (`forced_route`, `forced_answer_mode`), ignoring caller input state.
      - `node_assess_evidence` computes `evidence_state` dynamically from retrieved evidence and mock context triggers (`ports.mock_evidence_state`), ignoring caller input state.
      - Studio and mock tests may force execution paths exclusively via runtime mock context, never via input state.
    - **Pure Production Wiring (`backend/app/wiring.py`)**:
      - Verified production wiring contains zero monkeypatching or developer shims; serves strictly as the application composition root.
    - **Architectural Decision Record (`docs/decisions/ADR-0006-langgraph-studio-debugging.md`)**:
      - Formally documented why `StudioCompiledGraph` is required: LangGraph Studio UI only exposes inputs for graph state channels and configuration, not runtime context dataclasses (`GraphRuntimeContext`).
    - **Telemetry Scrubbing & Zero Egress**:
      - Enforced `LANGSMITH_TRACING=false` in `.env` and `.env.example`.
    - **Documentation**:
      - Updated `backend/README.md` with exact start command (`uv run --project backend langgraph dev`), sample JSON payload, and expected zero-content state output.
- **Tests Added**:
  - `backend/tests/unit/test_langgraph_config.py` (8 unit tests):
    - `langgraph.json` exists at root, specifies python 3.12, dependencies, and valid graph entrypoint; confirms duplicate in `backend/` does not exist.
    - `LANGSMITH_TRACING` is set to `false` in `.env` and `.env.example`.
    - Production `app/wiring.py` defines `build_app_graph` without monkeypatching.
    - `make_studio_graph()` produces valid graph with in-memory checkpointer.
    - Studio graph provides default mock context when `context` is omitted.
    - Studio graph preserves explicit context when provided.
    - Studio graph executes full run end-to-end to `status: answered`.
  - `backend/tests/unit/test_graph_contracts.py` (added 6 tests / updated):
    - Extended `test_checkpoint_state_and_metadata_contains_zero_content_memory_saver` inspecting `ALLOWED_CONTROL_KEYS`, banning answer content, and rejecting free text in state channels.
    - `test_zero_content_inspection_catches_free_text_and_response_payload`: confirms validator detects answer content and `response_payload`.
    - `TestUntrustedInputProtection`:
      - `test_caller_forged_tenant_id_overwritten_by_tenant_context`
      - `test_caller_forged_route_and_answer_mode_overwritten`
      - `test_caller_forged_evidence_state_overwritten_by_assess_evidence`
      - `test_caller_forged_status_overwritten_by_guard_input`
      - `test_studio_forcing_paths_via_mock_context_only`
  - Total test suite: 120 passed in 3.10s (unit, integration, and invariants).
- **Decisions Made**:
  - Adopted ADR-0006 (`docs/decisions/ADR-0006-langgraph-studio-debugging.md`).
  - Retained single `langgraph.json` at root directory.
  - Removed `response_payload` from `GraphState` in favor of `response_message_id`.
- **Open Issues**:
  - None. Clean pass across full pytest suite (120 passed in 3.10s), ruff, mypy, and all 10 pre-commit hooks.

---

### [2026-10-05] Task 1.7 Follow-up: Path-Forcing Switches Production Hardening

- **Task ID**: Task 1.7 Tightening
- **What Was Built**:
  - Hardened path-forcing switches (`mock_forced_route`, `mock_forced_answer_mode`, `mock_evidence_state`) against production leakage:
    - **Removed from Runtime Context**: Completely removed `forced_route` and `forced_answer_mode` from `GraphRuntimeContext`. Production request contexts cannot instantiate or carry forcing switches.
    - **Port-Level Simulation Gating**: Placed simulation flags into `GraphPorts` as `is_mock_profile: bool = True`, `mock_forced_route: str | None = None`, `mock_forced_answer_mode: str | None = None`, and `mock_evidence_state: str | None = None`.
    - **Engine Gating (Zero `if production:` branches in engine)**: `node_route` and `node_assess_evidence` check `if ports.is_mock_profile:` before inspecting any mock forcing flags. If `is_mock_profile` is False (production), all forcing switches are completely ignored, and the engine computes route, answer mode, and evidence state dynamically from the authenticated question and retrieved evidence.
    - **Production Composition Wiring (`backend/app/wiring.py`)**:
      - Added `create_production_ports(...)` which explicitly sets `is_mock_profile=False` with all mock flags `None`.
      - Added `create_production_context(...)` constructing a clean `GraphRuntimeContext` with zero simulation parameters.
- **Tests Added**:
  - `backend/tests/unit/test_graph_contracts.py`:
    - Added `test_production_wiring_leaves_forcing_switches_unset_and_engine_ignores_outside_mock`: verifies `create_production_ports` sets `is_mock_profile=False` with unset switches, and verifies that if forcing switches are somehow injected into a non-mock ports configuration, the engine ignores them and computes standard `route="knowledge"`, `answer_mode="short"`, and `evidence_state="NO_EVIDENCE"`.
  - Updated all unit and integration tests to pass simulation triggers via `ports.mock_forced_route` / `ports.mock_forced_answer_mode` where needed.
  - Full test suite: 121 passed in 3.04s. All 10 pre-commit hooks passed.
- **Decisions Made**:
  - Path-forcing is isolated strictly to the mock profile / testing / Studio environments. Production wiring cannot set them, and engine ignores them if `is_mock_profile` is False.
- **Open Issues**:
  - None.

---

### [2026-10-05] Task 1.8: Built-in AuthProvider, Tenant Roles, and FastAPI Application Core

- **Task ID**: Task 1.8
- **What Was Built**:
  - **Dependencies (Resolved & Pinned at Next Major Version)**:
    - Runtime: `fastapi>=0.142.2,<1.0.0`, `uvicorn[standard]>=0.54.0,<1.0.0`, `argon2-cffi>=25.1.0,<26.0.0`.
    - Dev: `httpx>=0.28.1,<1.0.0`.
  - **Database Migration (`0002_sessions_auth_events.py`)**:
    - Created `sessions` table (UUID PK, `user_id`, `token_hash`, `created_at`, `expires_at`, `revoked_at`, `last_seen_at`). Registered on `GLOBAL_TABLE_ALLOWLIST` (zero PHI).
    - Created `auth_events` table (UUID PK, `user_id`, `outcome`, `reason_code`, `client_ip_hash`, `created_at`). Registered on `GLOBAL_TABLE_ALLOWLIST` (zero PHI). App role (`app_user`) has `INSERT` only (no `UPDATE` or `DELETE`).
    - Added database function `lookup_assistant_tenant(uuid)` (`SECURITY DEFINER`) granting `app_user` safe pre-tenant assistant routing without weakening RLS on `assistants`.
  - **Built-in `AuthProvider` (`backend/app/adapters/auth/builtin.py`)**:
    - Implements [`AuthProvider`](file:///d:/chatbots-project/backend/app/ports/auth.py) using Argon2id password hashing and opaque URL-safe tokens stored only as SHA-256 hashes.
    - Dual timeout enforcement: idle timeout (configurable, default 30 min) and absolute expiry (configurable, default 24 hr).
    - Session revocation on logout marking `revoked_at`.
    - Tenant roles resolved strictly from `memberships` (`client_admin` and `staff`); platform admin flag on users provides platform access but zero tenant data access without a membership.
  - **FastAPI Core & Security (`backend/app/api/`)**:
    - Application factory (`create_app` in `app.py`) with CORS restricted to configured web origins (asserts never `*`).
    - Rate limiter (`LoginRateLimiter`) enforcing sliding window attempt limits for logins.
    - State-changing CSRF protection validating `X-CSRF-Token` header against `csrf_token` cookie for cookie-authenticated sessions.
    - Cookie hardening: login writes session token strictly to an `httpOnly`, `Secure` (in prod), `SameSite=Lax` cookie. Plaintext token is never returned in the JSON response body.
    - Tenant context injection (`get_tenant_context_for_assistant`): derives `TenantContext` strictly from assistant lookup and user membership. Never accepts caller-supplied `tenant_id`. Non-members (and platform admins without membership) asking for an assistant receive HTTP 404 (not 403), preventing ID probing.
  - **Endpoints**:
    - `GET /health`: Public. Returns only `{"status": "ok"}` with zero diagnostic disclosure.
    - `GET /v1/admin/health`: Platform admin only. Returns database connection and profile status.
    - `POST /v1/auth/login`: Verifies credentials, records success/failure to `auth_events`, rate limits, issues cookies.
    - `POST /v1/auth/logout`: Revokes session token in database and clears cookies.
    - `POST /v1/questions`: Validates assistant membership, executes under `tenant_session(tenant_ctx)`, saves user question and assistant answer as `messages` rows, records compliance audit event, and returns response with citations.
  - **Synthetic Seed Script (`backend/scripts/seed_dev_data.py`)**:
    - Idempotently provisions synthetic tenants, users, memberships, and assistants with Argon2id passwords.
  - **Architectural Decision Record (`docs/decisions/ADR-0007-session-auth-and-csrf.md`)**:
    - Recorded decisions on cookie hardening, CSRF double-submit token pattern, anti-probing 404s, and global auth events.
- **Tests Added**:
  - `backend/tests/unit/test_builtin_auth_provider.py` (6 unit tests): password hashing, opaque tokens, idle/absolute expiry, session resolution, revocation, membership roles.
  - `backend/tests/unit/test_api_auth_and_security.py` (6 unit tests): public health ok-only, admin health restriction, CORS origin enforcement, CSRF token checks.
  - `backend/tests/integration/test_db_sessions_and_rls.py` (2 integration tests): session hash storage, insert-only permission on `auth_events`.
  - `backend/tests/integration/test_api_auth_endpoints.py` (7 integration tests): login cookie and body omit, failed login generic errors, rate limiting 429, logout invalidation, cross-client assistant 404, platform admin no-membership 404, forged `tenant_id` ignored under tenant RLS.
  - Total test suite: 143 passed in 4.84s. All 10 pre-commit hooks passed.
- **Decisions Made**:
  - Adopted ADR-0007 (`docs/decisions/ADR-0007-session-auth-and-csrf.md`).
  - Documented in `docs/OPEN_QUESTIONS.md` that in-memory login rate limiter must move to shared store in Phase 6.
- **Open Issues**:
  - None for Phase 1.

---

### [2026-10-05] Task 1.8 Follow-up: Authentication & Security Hardening

- **Task ID**: Task 1.8 Follow-up
- **What Was Built**:
  - **`lookup_assistant_tenant` Function Hardening (`backend/app/db/migrations/versions/0002_sessions_auth_events.py`)**:
    - Added `REVOKE ALL ON FUNCTION lookup_assistant_tenant(uuid) FROM PUBLIC;` and `GRANT EXECUTE ON FUNCTION lookup_assistant_tenant(uuid) TO app_user;`.
    - Added fixed execution search path: `SET search_path = pg_catalog, pg_temp` with table reference fully qualified as `public.assistants`.
  - **Timing Attack Mitigation in `BuiltinAuthProvider` (`backend/app/adapters/auth/builtin.py`)**:
    - Pre-computed a constant Argon2id dummy hash `self._dummy_hash` on initialization.
    - When credentials specify an unknown email (`user is None`), the provider runs `self.verify_password(credentials.password, self._dummy_hash)` before raising `AuthenticationError("Invalid email or password")`, ensuring the execution time of unknown-account lookups closely matches failed-password lookups on existing accounts.
  - **HMAC IP Pseudonymization (`backend/app/api/deps.py`, `backend/app/api/config.py`, `.env.example`, `.env`)**:
    - Updated `get_client_ip_hash()` to compute HMAC-SHA256 hex digest using `auth_hmac_key` (loaded from `.env` via `AUTH_HMAC_KEY`) rather than a plain unsalted SHA-256 hash, preventing IPv4 rainbow table inversion.
    - Added `AUTH_HMAC_KEY=placeholder-hmac-secret-key-for-client-ip` in `.env.example` and local secret key in `.env`.
  - **Secure Cookie Default (`backend/app/api/config.py`)**:
    - Changed `cookie_secure` in `ApiSettings` to default to `True` (secure HTTPS cookies). Only an explicit local-dev setting (`COOKIE_SECURE=false`) turns it off.
  - **Per-Account Lockout Documentation (`docs/OPEN_QUESTIONS.md`)**:
    - Recorded Section 3.3 in `docs/OPEN_QUESTIONS.md` tracking per-account lockout (in addition to per-IP sliding window rate limiting) as a Phase 5 ("Hardening") decision item alongside Presidio exception scrubbing.
- **Tests Added**:
  - `backend/tests/integration/test_db_sessions_and_rls.py`:
    - `test_lookup_assistant_tenant_execution_permissions`: asserts `retriever_user` executing `lookup_assistant_tenant` is denied with `permission denied`, while `app_user` executes successfully.
    - `test_lookup_assistant_tenant_fixed_search_path_and_security_definer`: inspects `pg_proc` to verify `prosecdef is True` and `proconfig` contains `search_path=pg_catalog, pg_temp`.
  - `backend/tests/unit/test_builtin_auth_provider.py`:
    - `test_dummy_verify_runs_for_unknown_emails`: asserts dummy Argon2 verify runs with `_dummy_hash` on unknown email lookups.
  - `backend/tests/unit/test_api_auth_and_security.py`:
    - `test_secure_cookie_defaults_to_true`: asserts `ApiSettings().cookie_secure is True` by default and `False` only under explicit local-dev environment override (`COOKIE_SECURE=false`).
    - `test_client_ip_hash_uses_hmac_with_secret_key`: asserts `get_client_ip_hash` produces HMAC-SHA256 matching the secret key and does not match plain unsalted SHA-256.
  - Full test suite: 148 passed in 6.61s. All 10 pre-commit hooks passed.
- **Decisions Made**:
  - `lookup_assistant_tenant` strictly isolated to `app_user` with `search_path = pg_catalog, pg_temp` and schema qualification.
  - Client IP pseudonymization uses HMAC-SHA256 with key from `.env`.
  - Secure cookies default to on (`True`).
  - Per-account lockout scheduled for Phase 5 hardening.
- **Open Issues**:
  - None for Phase 1.

---

### [2026-10-05] Task 1.8 Follow-up: Commit Verification & Secrets Hardening

- **Task ID**: Task 1.8 Pre-Commit & Secrets Hardening
- **What Was Built**:
  - **Pre-Commit Task Protocol (`AGENTS.md`, `.agents/workflows/close-task.md`)**:
    - Enforced mandatory rule: Before reporting any task done, run `git add -A`, followed by `pre-commit run --all-files`, and report that output.
  - **Secret Keyword & Detect-Secrets Remediation (`backend/tests/`)**:
    - Replaced all string literal passwords across test files with dynamically generated high-entropy passwords at runtime (`runtime_test_password`, `runtime_alt_password`, `make_test_password` using `secrets.token_urlsafe` in `conftest.py`).
    - Dynamically generated runtime `hmac_key` via `secrets.token_hex(16)` in `test_api_auth_and_security.py`.
    - Removed unused `engine` local variables and used `test_user_password_hash` fixture in `test_db_sessions_and_rls.py`.
    - No `pragma: allowlist secret` comments added.
  - **Admin Health Safe Error Handling (`backend/app/api/routes/health.py`)**:
    - Updated `/v1/admin/health` to never return raw exception text.
    - Returns fixed detail message with error code (`Database diagnostic failure (error_code: DB_DIAGNOSTIC_FAILURE)`), logs only `error_type=%s` via logger, and raises with `from None` to avoid leaking diagnostic internals.
  - **Authentication Failure Detail & Exception Propagation (`backend/app/api/deps.py`, `backend/app/api/routes/auth.py`)**:
    - Updated `get_current_session` in `deps.py` to return fixed generic detail `Invalid or expired session` (never `str(e)`) and raised `from None`.
    - Added `from None` to `asst_uuid` validation in `deps.py` and `AuthenticationError` in `routes/auth.py`.
  - **Ruff Lint & Line Length Compliance**:
    - Wrapped long docstrings in `test_api_auth_endpoints.py`, `test_db_sessions_and_rls.py`, `test_api_auth_and_security.py`, and `test_builtin_auth_provider.py`.
    - Shortened description in `ApiSettings.cookie_secure` to fit 100-char line limit.
- **Verification**:
  - Staged with `git add -A`.
  - All 10 pre-commit hooks passed on all files without error:
    - `trim trailing whitespace`: Passed
    - `fix end of files`: Passed
    - `check for added large files`: Passed
    - `detect private key`: Passed
    - `mixed line ending`: Passed
    - `Detect secrets`: Passed
    - `ruff`: Passed
    - `ruff-format`: Passed
    - `mypy`: Passed
    - `boundary-invariants`: Passed
  - Full test suite: 148 passed in 8.28s with 0 warnings.
- **Decisions Made**:
  - Strictly adhere to runtime password generation for tests; zero plaintext literals in repo.
  - `git add -A` followed by `pre-commit run --all-files` is now a mandatory protocol in `AGENTS.md`.
- **Open Issues**:
  - None for Phase 1.

---

### [2026-10-05] Task 1.9: Phase 1 Close-out, Gate Verification Report, and GitHub Actions CI

- **Task ID**: Task 1.9
- **What Was Built**:
  - **Phase 1 Gate Verification Report (`docs/phases/PHASE-1-GATE.md`)**:
    - Comprehensive mapping of all 15 architecture invariants to automated tests across unit, integration, and invariant suites.
    - Comprehensive mapping of all 13 failure modes in `docs/FAILURE_MODES.md` to automated tests.
    - Verified zero gaps in Phase 1 scope; confirmed Phase 1 exit gate is passed.
  - **GitHub Actions CI Workflow (`.github/workflows/ci.yml`)**:
    - Triggers on push and pull requests targeting branch `main`.
    - Leverages Python 3.12 with `astral-sh/setup-uv@v5` and lockfile caching (`backend/uv.lock`).
    - Runs full pre-commit checks on all files (`uv --directory backend run pre-commit run --all-files`).
    - Spawns a PostgreSQL service container running `pgvector/pgvector:pg17`.
    - Bootstraps roles (`scripts/bootstrap_db_roles.py`) and runs Alembic migrations (`alembic upgrade head`).
    - Executes the complete test suite (148 tests) including all integration tests against the live pgvector container.
    - Uses only throwaway, non-production test credentials defined locally in workflow env; requires zero repository secrets.
- **Verification**:
  - Staged all changes with `git add -A`.
  - Executed `pre-commit run --all-files` (all 10 hooks passed).
  - Executed full test suite with `pytest` (148 passed in 7.79s).
- **Decisions Made**:
  - Pinned CI service container to `pgvector/pgvector:pg17` with pgvector extension support.
  - Phase 1 marked complete in `docs/STATUS.md` and ready for Phase 2 ("Ingestion Pipeline").
- **Open Issues**:
  - None. Ready for Task 2.1.

---

### [2026-10-05] Hotfix: Editor Pyrefly Configuration for StateGraph Specialization

- **Issue**:
  - The Pyrefly language extension flagged LangGraph's internal `StateT` bound protocol on `TypedDict` schemas with `[bad-specialization]` in `backend/app/core/graph.py` and `backend/app/wiring.py`.
- **Resolution**:
  - Reverted all source code changes in `app/core/graph.py` and `app/wiring.py`. Mypy is the sole type authority for the project; Python source types are never weakened to satisfy editor extensions.
  - Handled via configuration and documentation only:
    - Added `[tool.pyrefly.errors] bad-specialization = false` to `backend/pyproject.toml`.
    - Documented in `backend/README.md` under "Type Checking & Editor Diagnostics" that `mypy` is the type authority, detailing the Pyrefly configuration and option to disable the extension in favour of Mypy.
- **Verification**:
  - `mypy app tests`: 0 issues found in 60 source files.
  - `pyrefly check backend/app`: 0 errors.
  - Full test suite: 148 passed.
  - Pre-commit: all 10 hooks passed.

---

### [2026-10-06] Task 2.1: Ingestion Database Schema, Idempotency Migration, and Isolated Migration Testing

- **What Was Built**:
  - **8 Ingestion Relational Schema Tables**:
    - `documents`: Master record with circular composite foreign key `active_version_id` pointing to `document_versions(tenant_id, document_id, id)` (`ON DELETE RESTRICT`, `DEFERRABLE INITIALLY DEFERRED`, `use_alter=True`). Single source of truth for active version (no version-level `is_active`).
    - `document_versions`: Immutable content-hashed version of uploaded document with `UNIQUE (tenant_id, sha256)` preventing duplicate uploads per tenant, and composite parent FK to `documents`.
    - `pages`: Single-page evidence boundary carrying page classification, sequence number, and `image_key` storage path with check constraint enforcing tenant prefix and traversal prevention (`position(chr(92) in image_key) = 0`, rejecting `..`, `//`, and leading slash).
    - `page_readings`: Extracted text from digital parsing, Docling OCR, or upload-time vision transcription referencing `(tenant_id, page_id)`.
    - `chunks`: Searchable text chunks with `embedding vector(1024)`, HNSW cosine similarity index (`vector_cosine_ops`), `ts_config` check constraint (`english`, `spanish`, `simple`), GIN-indexed generated `tsv` column, and `is_active DEFAULT false`.
    - `tables_data`: Tabular spreadsheet rows (CSV / Excel) preserved as structured JSON without chunking.
    - `jobs`: Background ingestion workflow execution tracking document versions with attempts counter.
    - `job_steps`: Granular pipeline step execution tracking state and retries with PostgreSQL 15+ `UNIQUE NULLS NOT DISTINCT (tenant_id, document_version_id, page_number, step, processing_version)`.
  - **Row Level Security (RLS) & Role Privileges**:
    - ENABLE and FORCE RLS on all 8 tables using `NULLIF(current_setting('app.current_tenant_id', true), '')::uuid` for both `USING` and `WITH CHECK`.
    - `app_user` granted `SELECT, INSERT, UPDATE` on all 8 tables (`DELETE` revoked; all FKs `ON DELETE RESTRICT`).
    - `retriever_user` granted `SELECT` exclusively on the 6 evidence tables (`documents`, `document_versions`, `pages`, `page_readings`, `chunks`, `tables_data`); access denied to `jobs` and `job_steps`.
    - `PUBLIC` role privileges revoked across all tables.
  - **Alembic Migration (`0003_ingestion_pipeline_schema.py`)**:
    - Clean upgrade applying `CREATE EXTENSION IF NOT EXISTS vector;`, tables, constraints, indexes, RLS policies, and role grants.
    - Strict downgrade dropping created objects and `DROP EXTENSION IF EXISTS vector;` without `CASCADE` to fail loudly if unexpected external dependents exist.
  - **Architectural Decision Record (`ADR-0008`)**:
    - Documented multi-language full-text search tsvector generation, ingestion relational schema, and idempotency keys in `docs/decisions/ADR-0008-tsvector-and-ingestion-schema.md`.
- **Key Decisions & Bug Fixes**:
  - **tsvector Generation Immutability Fix**: PostgreSQL generated columns require strictly `IMMUTABLE` expressions. Dynamic cast `ts_config::regconfig` is classified as `STABLE` due to search_path catalog lookups. Resolved using a deterministic compile-time `CASE` expression with constant literals (`WHEN ts_config = 'english' THEN to_tsvector('english'::regconfig, text) ...`) matching the check constraint.
  - **Storage Key Backslash Constraint Fix**: In SQL `LIKE`, backslash is the escape character, so `NOT LIKE '%\%'` tested for literal `%`. Replaced with `position(chr(92) in image_key) = 0` in both migration and models, and added tests verifying that valid keys with `%` succeed while validly prefixed paths with backslashes fail with 23514.
  - **Execution Rule**: Updated `.agents/rules/execution.md` requiring all commands to execute in foreground and mandating `git add -A` and `pre-commit run --all-files` before any handoff.
  - **Destructive Phase 1 Migration Test Fix**: `test_alembic_migrations_upgrade_and_downgrade` in `test_db_schema_and_rls.py` previously executed `command.downgrade(cfg, "base")` against the shared database (`knowledge_assistant`), wiping data on every run. Moved both migration lifecycle tests to use a shared `throwaway_db` fixture in `conftest.py` executing against isolated throwaway databases (`cka_test_throwaway_*`). Added suite-level and test-level persistence assertions verifying that the shared database is never wiped.
- **Verification**:
  - `git add -A` and `pre-commit run --all-files` passed with 100% compliance across all 10 hooks.
  - Full test suite passed: 169 passed in 7.73s with zero failures and zero skipped tests.

---

### [2026-10-06] Task 2.2: Document Upload Validation & Tenant-Scoped Deduplication

- **Task ID**: Task 2.2
- **What Was Built**:
  - **Upload Content Validation & Sniffing** (`backend/app/core/ingestion/validation.py`):
    - Content-based MIME inspection via magic bytes: PDF (`%PDF-`), XLSX (`[Content_Types].xml`, `xl/workbook.xml`, rejecting `.docx` and `.xlsm`), CSV/TXT with encoding detection (`utf-8`, `utf-8-sig`, `windows-1252`).
    - Delimiter check applies strictly to `.csv` files; plain `.txt` files with standard sentences and no commas or tabs are accepted (`text/plain`).
    - Enforced strict 50 MB file size limit and rejection of NUL bytes or binary disguised as text.
    - Sanitized error messages to guarantee zero PHI/PII leakage (error codes only; no raw filenames or content).
  - **Tenant-Scoped Upload Orchestration** (`backend/app/core/ingestion/upload.py`):
    - Role authorization strictly restricted to `client_admin` (`staff` and `platform_admin` rejected with `AuthorizationError`).
    - Tenant-scoped deduplication lookup by SHA-256 before storage writes. Existing duplicates bypass storage writes entirely.
    - Storage write before DB insert to prevent orphaned database records.
    - Status initialized to `received` (reserving `ready` for pipeline completion).
    - Fail-closed audit logging: failure during audit logging aborts upload, deletes the stored file, and re-raises.
  - **Document Repository Port & PostgreSQL Adapter**:
    - Port interface (`backend/app/ports/document_repository.py`) and PostgreSQL adapter (`backend/app/adapters/db/postgres_document_repository.py`).
    - Atomic `create_document_with_version` executes inside `async with session.begin_nested():` savepoint.
    - Brand-new upload concurrency race: when unique violation (`uq_document_versions_tenant_sha256`) is caught, savepoint rollback rolls back both the `DocumentVersion` row and the new `Document` row cleanly in PostgreSQL without requiring `DELETE` permissions (respecting `app_user` grants).
  - **Profile Allowlist Wiring Guards** (`backend/app/wiring.py`):
    - `create_malware_scanner` and `create_storage_backend` guarded by `SYNTHETIC_LOCAL_PROFILES` allowlist (`mock`, `office-cpu`, `desktop-gpu`, `home-gpu`, `dev-free-api`).
    - `cloud`, `frontier`, or unknown profile names raise `ConfigurationError`.
  - **StorageKey Traversal Protections** (`backend/app/ports/types.py`):
    - Enforced UUID typing for `tenant_id` and rejected `..`, backslashes (`\`), leading slashes, and double slashes (`//`).
- **Tests Added**:
  - `backend/tests/unit/test_upload_validation.py`: 25 unit tests covering MIME sniffing, extension matching, boundary file size (exactly 50MB), plain text without delimiters, Windows-1252 accents, UTF-8 BOM, deterministic binary rejection, macro rejection, malware profile allowlist, storage profile allowlist, StorageKey path sanitization, zero-PHI error sanitization.
  - `backend/tests/integration/test_upload_service.py`: 12 integration tests covering initial upload, duplicate upload returning existing version, duplicate targeting different document ID, concurrent barrier race asserting exactly 1 document and 1 version in DB, storage failure cleanup, cross-tenant isolation (Invariant 15), role authorization rejection, zero-PHI operational audit logging, new version leaves active_version_id NULL, cross-tenant RLS query isolation, normal duplicate never calls storage put, fail-closed audit log cleanup.
- **Decisions Made**:
  - Concurrency rollback handled strictly via savepoint rollback (`begin_nested`), eliminating `DELETE FROM documents` statements since `app_user` has `SELECT, INSERT, UPDATE` grants only.
  - `create_storage_backend` matches `create_malware_scanner` profile allowlist guard.
  - Core domain imports strictly isolated to `app.core` and `app.ports` (ADR-0003).
- **Verification**:
  - `uv run --project backend pytest backend/tests/unit/test_upload_validation.py`: 25 passed.
  - `uv run --project backend pytest backend/tests/integration/test_upload_service.py`: 12 passed.

---

### [2026-10-06] Task 2.3: Document Splitting & Page Classification

- **Task ID**: Task 2.3
- **What Was Built**:
  - **Libraries & Dependencies** (`backend/pyproject.toml`, `backend/uv.lock`):
    - Runtime: pinned `pypdfium2==5.14.0` (Apache-2.0 / BSD-3-Clause) and `pillow==12.3.0` (HPND).
    - Dev-only: pinned `pypdf==6.19.0` (BSD-3-Clause) strictly under `[dependency-groups] dev` for synthetic test fixture generation; confirmed zero runtime presence.
  - **Port Interfaces & Adapters**:
    - Created `DocumentSplitter` port (`backend/app/ports/document_splitter.py`) defining `async def split(content: bytes, extension: str, max_pages: int) -> list[PageArtifact]`.
    - Added `exists(key: StorageKey) -> bool` to `StorageBackend` port and implementations (`InMemoryStorageBackend`, `LocalStorageBackend`).
    - Added `get_page_numbers`, `create_page`, `update_version_status`, and `record_job_step` to `DocumentRepository` port and `PostgresDocumentRepository`.
    - Created `PyPdfiumDocumentSplitter` adapter (`backend/app/adapters/pypdfium_splitter.py`):
      - Thread safety: wrapped all `pypdfium2` calls in a process-wide `threading.Lock()` (`_PDFIUM_LOCK`).
      - Renders pages at 150 DPI with 72 DPI base scale (`RENDER_SCALE = 150 / 72`).
      - Decompression bomb safety: capped `Image.MAX_IMAGE_PIXELS = 50_000_000`, rejected PDF canvas dimensions $> 5000\times 5000\,\text{pt}$, and enforced configurable page limit (default 500).
      - Multi-page TIFF support with frame count and dimension safety checks.
  - **Core Splitting & Classification Domain**:
    - Pure MECE page classifier (`backend/app/core/ingestion/page_classifier.py`): classifies `digital_text`, `scanned` (with text layer detection for searchable scans), `image_heavy`, and `blank`.
    - Routing helper (`backend/app/core/ingestion/splitter_router.py`): routes `.pdf`, `.tiff`, `.tif` to splitter; marks `.txt`, `.csv`, `.xlsx` as skipped with `job_steps` record.
    - Orchestration service (`backend/app/core/ingestion/splitter_service.py`):
      - Preflight validation verifies container integrity, encryption, page count, and dimensions before writing any page rows to database.
      - Enforces `app_user` zero-DELETE privilege: preflight permanent failures fail with 0 page rows written; mid-run corruption preserves committed page rows, updates version to `failed`, records failure in `job_steps`, and purges stored image files from object storage.
      - Idempotent resumption (Invariant 11): checks existing committed pages and skips duplicate storage/database writes.
  - **Database Migration 0004** (`backend/app/db/migrations/versions/0004_page_type_and_status.py`):
    - Added check constraints `ck_document_versions_status` (`received`, `processing`, `needs_review`, `ready`, `failed`) and `ck_pages_page_type` (`digital_text`, `scanned`, `image_heavy`, `blank`).
    - Migration normalizes existing legacy `page_type = 'digital'` to `'digital_text'` before creating the constraint.
    - Updated `backend/app/db/models.py` with SQLAlchemy `CheckConstraint` declarations.
  - **Synthetic Fixtures** (`backend/tests/fixtures/synthetic_documents.py`):
    - Programmatic in-memory generators for blank, digital, scanned, searchable scanned, image-heavy, encrypted, corrupt, and oversized PDFs and multi-page TIFFs. Zero real documents used.
- **Tests Added**:
  - `backend/tests/unit/test_page_splitting_and_classification.py`: 37 unit tests covering MECE decision table boundaries, adapter parsing and rendering, thread-safe concurrent splitting, safety caps, and non-paged routing.
  - `backend/tests/integration/test_document_splitter_service.py`: 4 integration tests covering Migration 0004 check constraint enforcement, temporary failure idempotent resumption (Invariant 11), preflight permanent failure clean termination, and mid-run corruption partial row preservation with storage cleanup.
- **Decisions Made**:
  - `pypdfium2` calls guarded by adapter-level process-wide `threading.Lock()`.
  - Ingestion progress and failure steps recorded in `job_steps`, reserving `run_steps` for query-time graph telemetry.
  - Preflight checks execute prior to DB writes, accommodating `app_user` lack of `DELETE` permissions.
- **Verification**:
  - `uv run --project backend pytest backend/tests/unit/test_page_splitting_and_classification.py`: 37 passed.
  - `uv run --project backend pytest backend/tests/integration/test_document_splitter_service.py`: 4 passed.

---

### [2026-10-08] Task 2.4: Docling Parser Adapter, Review Policy & Migration 0005

- **Task ID**: Task 2.4
- **What Was Built**:
  - **Core Review Policy Domain** (`backend/app/core/ingestion/review_policy.py`):
    - Pure Python implementation (zero vendor imports, AGENTS.md Build Rule 4).
    - Strictly accepts `page_type: PageType` (raises `ValueError` for string or invalid inputs).
    - Evaluates review gating under all 4 page types:
      - `SCANNED`: OCR expected; if `ocr_score` is `NaN`, `None`, or `< 0.70`, flags `needs_review=True`.
      - `IMAGE_HEAVY` without text layer: OCR expected, follows same rule as `SCANNED`.
      - `IMAGE_HEAVY` with native text layer & `DIGITAL_TEXT`: not flagged for missing OCR (`needs_review=False`).
      - `BLANK`: never flagged for review (`needs_review=False`), never indexed (`is_page_indexable` returns `False`).
    - Enforces Invariant 6 (`is_page_indexable`) and version gating status (`evaluate_version_review_status`).
  - **Database Migration 0005** (`backend/app/db/migrations/versions/0005_readings_table_cites.py`):
    - Revision ID: `0005_readings_table_cites` (25 chars <= 32 char limit of `alembic_version.version_num`).
    - Added `pages.ocr_confidence` (Float, nullable) and `pages.needs_review` (Boolean, default False).
    - Added `tables_data.page_id` (UUID, nullable) and `table_index` (Integer, default 0).
    - Added composite foreign key `(tenant_id, document_version_id, page_id) REFERENCES pages(tenant_id, document_version_id, id)` preventing cross-tenant citations (Invariant 8).
    - Added unique constraint `uq_tables_data_idempotency` with `NULLS NOT DISTINCT` on `(tenant_id, document_version_id, sheet_name, page_id, table_index, row_index)` for duplicate spreadsheet re-run rejection.
    - Added unique constraint `uq_page_readings_idempotency` on `(tenant_id, page_id, source, model_id, model_version)`.
    - Updated SQLAlchemy declarative models in `backend/app/db/models.py`.
  - **Docling Parser Adapter** (`backend/app/adapters/parsing/docling.py`):
    - Implemented `DoclingDocumentParser` with lazy `docling` loading inside functions/methods.
    - Strict error boundary: raises `ConfigurationError` when docling is not installed (zero safe fallback).
    - Helper methods: `filter_indexable_pages` (Invariant 6), `build_page_reading_record`, `build_table_data_records`.
  - **Mock Document Parser** (`backend/app/adapters/parsing/mock.py`):
    - Implemented `MockDocumentParser` implementing `DocumentParser` for deterministic test fixtures.
  - **Database Test Hardening**:
    - Added `connect_timeout=3` parameter to `verify_shared_database_persistence_across_suite` and `isolated_throwaway_database` so a stopped PostgreSQL fails fast instead of hanging.
- **Tests Added**:
  - `backend/tests/invariants/test_migration_invariants.py`: Invariant test asserting all migration revision IDs in `versions/` are <= 32 characters.
  - `backend/tests/unit/test_review_policy.py`: 18 pure Python unit tests verifying all 4 page types, NaN / None / threshold scores, blank page non-indexing, string/type validation rejection, and version evaluation. All 18 pass in CI.
  - `backend/tests/integration/test_docling_parser.py`: Contract and idempotency tests verifying page reading unique keys, spreadsheet NULLS NOT DISTINCT re-runs, and Invariant 8 table page citations using `MockDocumentParser` and asserting `ConfigurationError` when docling is absent. Real-model tests marked with `@pytest.mark.models` and skipped when docling is not installed.
  - `backend/tests/integration/test_migration_0005.py`: Integration tests in an isolated throwaway database testing upgrade/downgrade/upgrade lifecycle, duplicate page_readings rejection (23505), duplicate spreadsheet tables_data rejection with page_id NULL (23505), cross-tenant tables_data page reference rejection (23503), and pages.needs_review default false.
- **Decisions Made**:
  - Migration revision IDs capped strictly at 32 characters enforced by AST invariant test.
  - Docling adapter raises `ConfigurationError` when optional parsing dependencies are not installed; tests use `MockDocumentParser`.
  - Review policy logic strictly lives in `app/core/ingestion/review_policy.py` and takes `PageType` enum only.
  - Real-model tests are gated behind `@pytest.mark.models` and run on the desktop GPU.
  - `dev-free-api` uses `DoclingDocumentParser` (matching `office-cpu` CPU ingestion, as generation models alone use Gemini free tier; parsing is local).
  - Production `cloud` parser requires offline flags and pinned model paths, forwarded directly to `DoclingDocumentParser` which sets `HF_HUB_OFFLINE=1`, disables EasyOCR downloads, and sets model directory paths.
- **Verification**:
  - All 18 unit tests in `tests/unit/test_review_policy.py` pass.
  - `test_migration_invariants.py` passes in 0.02s.
  - All 28 tests in `tests/unit/test_upload_validation.py` pass.
  - All 5 database integration tests in `test_migration_0005.py` pass against Postgres.
  - All pre-commit hooks pass on all files (`pre-commit run --all-files`).

---

### [2026-10-08] Task 2.6: Tabular Data Ingestion (CSV / XLSX Spreadsheets)

- **Task ID**: Task 2.6
- **What Was Built**:
  - **Dependencies & Stubs**:
    - Added `openpyxl==3.1.5` and `defusedxml==0.7.1` to runtime dependencies.
    - Added typed stub packages `types-openpyxl==3.1.5.20260827` and `types-defusedxml==0.7.0.20260504` to `[dependency-groups] dev` (confirmed latest via PyPI JSON API). Zero mypy ignore overrides used; full strict typing enforced.
    - Verified `openpyxl.xml.DEFUSEDXML is True` to guard against XML expansion and billion laughs attacks.
  - **Encoding & Parsing Utilities** (`backend/app/ports/encoding.py`):
    - Created shared `decode_text_content` in `app/ports/encoding.py` using standard library only.
    - Robust decoding covering UTF-8, UTF-8 BOM (`\xef\xbb\xbf`), and Windows-1252 (`cp1252`) fallback, rejecting binary NUL bytes.
  - **Port Interfaces** (`backend/app/ports/table_extractor.py`, `backend/app/ports/document_repository.py`):
    - `ExtractedTableSheet`, `TableExtractionResult`, and `TableExtractor` port interfaces.
    - Extended `DocumentRepository` port with `create_table_data_batch(tenant_id: str, records: list[dict[str, Any]]) -> None` and optional `job_id` parameter on `record_job_step`.
  - **Table Extractor Adapter** (`backend/app/adapters/tables/builtin.py`):
    - `BuiltinTableExtractor` implementing `TableExtractor` with dual-read openpyxl (`data_only=True` for cached values and `data_only=False` for formula detection).
    - Distinguishes empty cells from uncalculated formulas cleanly: uncalculated formula cells store `None` in `row["cells"]` and list the column name in `row["unreadable"]`. Zero `#UNREADABLE_FORMULA#` strings in cell values.
    - Numbers stored as native JSON numbers (int/float) without float precision loss. Negative numbers stored as written without formula escaping in the database.
    - Cell `number_format` distinguishes date-only (`YYYY-MM-DD`) vs date-time (`YYYY-MM-DDTHH:MM:SS`).
    - Merged cells preserved verbatim (top-left holds value, others None).
    - Offset headers supported (skipping leading blank rows).
    - Blank headers normalized to `Column_{idx}` and duplicate headers deduplicated to `Name`, `Name_2`.
    - Hidden sheets skipped; count reported in `result.skipped_hidden_sheets_count`. Zero sheet names logged.
    - Limits enforced: 200 columns (`MAX_COLUMNS`), 50,000 data rows (`MAX_ROWS_PER_SHEET`), 1,000,000 cells (`DEFAULT_MAX_WORKBOOK_CELLS`). Exceeding raises `FileValidationError`.
    - CSV/TSV parsing: delimiter sniffing among `,`, `;`, `\t`; exact text preserved with zero type inference.
  - **Core Ingestion Service** (`backend/app/core/ingestion/table_service.py`):
    - `TableIngestionService`: orchestrates extraction, version status transitions (`received` -> `processing` -> `ready`), batch insertions of 1,000 rows into `tables_data`, and records `job_steps` with `step="table_ingestion"`, `status="completed"`, `page_number=None`. Zero ORM imports in core (Rule 4).
  - **Database Repository Adapter** (`backend/app/adapters/db/postgres_document_repository.py`):
    - `create_table_data_batch`: PostgreSQL insert with `ON CONFLICT ON CONSTRAINT uq_tables_data_idempotency DO NOTHING`.
    - `record_job_step`: idempotent insert using PostgreSQL `ON CONFLICT DO NOTHING` on `uq_job_steps_idempotency`.
  - **Wiring** (`backend/app/wiring.py`):
    - Added `create_table_extractor()` and `create_table_ingestion_service(session)` factories.
- **Tests Added**:
  - `backend/tests/unit/test_table_ingestion.py`: 15 unit tests covering defusedxml activation, dual-read formula detection, clean row JSON, header deduplication, CSV exact text and zero inference, XLSX numbers, dates by format, hidden sheets skipped, merged cells verbatim, offset headers, total cell limits, delimiters (`;`, `\t`), encodings (BOM, cp1252), empty/header-only rejection, non-spreadsheet rejection, column limit (200), row limit (50,000), plus 1 slow memory benchmark test (`@pytest.mark.slow`).
  - `backend/tests/integration/test_table_ingestion_service.py`: 4 integration tests against PostgreSQL testing idempotent re-runs, job steps recording status without sheet names and without metadata column, version status lifecycle (`processing` -> `ready`), and RLS multi-tenant isolation.
- **Decisions Made**:
  - Slow 50k-row memory benchmark marked with `@pytest.mark.slow` and excluded by default via `[tool.pytest.ini_options]` (`addopts = "-ra -q -m \"not models and not slow\""`). Runs on demand with `pytest -m slow`.
  - Formula injection escaping deferred to client export endpoints/UI; database stores verbatim values.
  - Shared text decoding placed in `app/ports/encoding.py` to preserve architecture boundary invariants (adapters never import core).
- **Verification**:
  - All 19 Task 2.6 tests pass: 15 unit tests + 4 integration tests (1 slow test deselected by default).
  - All 191 unit tests across the test suite pass cleanly.
  - All 24 boundary and migration invariants pass.
  - Pre-commit passes 100% cleanly on all files (`pre-commit run --all-files`).

---

### [2026-10-08] Task 2.7: Ingestion Pipeline Worker & Version Activation

- **Task ID**: Task 2.7
- **What Was Built**:
  - **Migration 0006 (`0006_procrastinate_schema.py`)**:
    - Revision ID: `0006_procrastinate_schema` (25 characters, strictly $\le$ 32).
    - Creates schema `procrastinate`.
    - Generates and applies Procrastinate queue schema DDL inside `procrastinate` via `SchemaManager(connector=None).get_schema()`.
    - Enforces least privilege grants: `USAGE` on schema (no `CREATE`), `SELECT, INSERT, UPDATE, DELETE` on tables (no `TRUNCATE`), `USAGE, SELECT` on sequences, and `EXECUTE` on functions (`procrastinate_defer_job_v1`, `procrastinate_finish_job_v1`, `procrastinate_retry_job_v2`, worker management functions, and triggers) to `app_user`.
    - Configures `ALTER DEFAULT PRIVILEGES` matching least privilege grants.
    - Strictly revokes `ALL` on schema `procrastinate` from `retriever_user`.
    - Unapplied on shared DB as instructed.
  - **Database Migration 0006 Integration Tests (`backend/tests/integration/test_migration_0006.py`)**:
    - Runs in an isolated throwaway database (`isolated_throwaway_database()`) so it passes before 0006 is applied to the shared DB.
    - Verifies queue tables exist exclusively in schema `procrastinate`, none in `public`.
    - Verifies `app_user` can enqueue, fetch, update, and delete jobs in schema `procrastinate`.
    - Verifies `app_user` cannot `TRUNCATE` tables (SQLSTATE 42501) and cannot `CREATE` tables (SQLSTATE 42501).
    - Verifies `retriever_user` receives permission denied (`SQLSTATE 42501`) on schema `procrastinate`.
  - **In-Process Worker End-to-End Test (`backend/tests/integration/test_procrastinate_worker_e2e.py`)**:
    - Runs in an isolated throwaway database with head migrations applied.
    - Defers `split_document_job` to the real Procrastinate queue in schema `procrastinate`.
    - Executes the real Procrastinate worker in-process (`run_worker_async`).
    - Verifies split job enqueues per-page jobs, worker processes all pages, and `VersionActivationService` activates the version (`ready`), flipping `documents.active_version_id` and activating all chunks (`is_active=True`).
  - **Worker / Tenant Session Separation (`backend/app/db/session.py`)**:
    - `tenant_session(tenant_ctx: TenantContext)`: strictly typed to `TenantContext`. API code cannot pass a `WorkerContext`.
    - `worker_tenant_session(worker_ctx: WorkerContext)`: dedicated session function strictly accepting `WorkerContext`, setting `app.current_tenant_id` while operating under worker system authority.
  - **Worker Audit Actor (`backend/app/ports/audit.py`, `backend/app/observability/audit.py`)**:
    - Added `record_worker(worker_ctx, action, event_type, resource_id)` on `AuditLoggerPort` and `AuditLogger`.
    - Explicitly sets `user_id=None` (nullable in `audit_events`) representing system worker actor rather than a fabricated user UUID.
  - **Split Job Crash Recovery & Idempotency (`backend/app/worker.py`, `backend/app/core/ingestion/pipeline.py`)**:
    - `enqueue_uncompleted_page_jobs`: inspects `job_steps` under version lock and enqueues jobs for every page lacking a completed `page_pipeline` step.
    - Deterministic chunk UUIDs generated using `uuid5(NAMESPACE_URL, f"{tenant_id}:{document_version_id}:{page_number}:{i}")` combined with PostgreSQL `ON CONFLICT (id) DO NOTHING` on `chunks`.
  - **Version Row Locking & Atomic Activation (`backend/app/core/ingestion/activation.py`, `backend/app/adapters/db/postgres_document_repository.py`)**:
    - Row-level lock via `SELECT ... FOR UPDATE` on `document_versions`.
    - Calculates uncompleted page numbers dynamically from `job_steps` (`WHERE step = 'page_pipeline' AND status = 'completed'`).
    - Implements MECE branches:
      - Branch A: All pages completed, 0 flagged for review -> updates version to `ready`, flips `documents.active_version_id` to current version, sets current version chunks to `is_active=True`, and retires previous active version chunks (`is_active=False`) and status (`retired`) atomically in one transaction.
      - Branch B: All pages accounted for, $\ge$ 1 page flagged for review -> updates version to `needs_review`.
      - Branch C: Incomplete pages remaining -> version remains `processing`.
    - Concurrent activation safety: second concurrent activation sees version already activated and exits cleanly.
  - **Page Pipeline Service Error Classification (`backend/app/core/ingestion/pipeline.py`)**:
    - Permanent errors flag page for review (`flag_page_for_review`), record `status="failed"` in `job_steps`, and do not re-raise (preventing infinite retry loops).
    - Transient errors (`ProviderTimeout`, `ProviderUnavailable`, `QueueError`) are re-raised to trigger Procrastinate queue retry backoff.
- **Tests Added**:
  - `backend/tests/unit/test_worker_and_activation.py`: 7 unit tests (WorkerContext definition, session separation rejection, system worker audit actor, Branch A/B/C logic, page pipeline permanent review vs transient retry).
  - `backend/tests/integration/test_version_activation.py`: 4 integration tests (atomic version activation and chunk activation, previous version retirement, review flagging transitions, concurrent activation row locking).
  - `backend/tests/integration/test_pipeline_idempotency.py`: 4 integration tests (re-run duplicate chunk prevention, split job crash re-run recovery, RLS tenant isolation, failed page review flagging).
  - `backend/tests/integration/test_migration_0006.py`: 1 comprehensive integration test in throwaway DB covering schema isolation, app_user queue capabilities, least privilege TRUNCATE/CREATE denial, and retriever_user denial.
  - `backend/tests/integration/test_procrastinate_worker_e2e.py`: 1 end-to-end integration test running the real Procrastinate worker in-process and asserting document version activation.
- **Decisions Made**:
  - `pages` table remains immutable physical page artifact evidence; transient pipeline progress is tracked in `job_steps` under version row lock.
  - Chunks generated with deterministic tenant-and-version-scoped UUIDs to ensure database-level idempotency on re-runs.
  - Enforce strict global row lock order across all paths: parent `documents` row locked FIRST with `FOR UPDATE`, then child `document_versions` row locked SECOND with `FOR UPDATE`.
  - Transaction separation: worker commits page processing (chunks and `job_steps`) in its own transaction first, releasing foreign key share locks before entering a separate short transaction for activation locking.
  - Error classification: `is_retryable_error` classifies PostgreSQL `DeadlockDetected` (40P01) and `SerializationFailure` (40001) as transient errors for worker retry.
- **Verification**:
  - All 17 Task 2.7 tests pass.
  - Concurrency test verified across 30 consecutive runs with 0 failures and 0 deadlocks (stopping on first failure).
  - All 10 pre-commit hooks pass on all files (`pre-commit run --all-files`).

---

## 2026-10-12 - Task 2.5: Upload-Time Vision Reading & Pipeline Wiring

- **Module**: `backend/app/core/ingestion/pipeline.py`, `backend/app/core/ingestion/vision_policy.py`, `backend/app/adapters/images/pillow.py`, `backend/app/adapters/db/postgres_document_repository.py`, `backend/app/wiring.py`
- **Spec Section**: Task 2.5 Upload-Time Vision Reading (Option A Recovery, Dual Reading Selection, Migration 0007, Invariants 5, 6, 11)
- **What Was Built**:
  - `ADR-0011`: Documented `transcribe_page(role, image)` port method on `ModelProvider` without prompt question, downscaling via `ImageProcessor`, and optional `PageReading.confidence` (`float | None`).
  - Migration 0007: Added `review_reason` allowlist (`low_ocr_confidence`, `vision_failed`, `vision_low_confidence`, `vision_confidence_missing`, `image_too_large`, `processing_failed`, `legacy_unspecified`), `ck_pages_needs_review_reason` valid check constraint, and composite foreign key `(tenant_id, page_reading_id)` with `evidence_type` on `chunks`.
  - `ImageProcessor` port and `PillowImageProcessor` adapter: Enforced lossless PNG downscaling respecting max dimensions and bytes with safety floor.
  - Pipeline Step 3 Wiring:
    1. Docling extract, then 2.4 review policy (`evaluate_page_review` flags `low_ocr_confidence`).
    2. Vision trigger rule (`should_trigger_vision_read`): prepares image via `ImageProcessor`, calls `transcribe_page`, and saves with `create_page_reading` (`source="vision_ingest"`).
    3. Option A recovery: successful vision reading clears only `low_ocr_confidence`. Low/missing confidence or `ImageProcessingError` flags matching reason.
    4. Reading selection rule (`select_readings_for_chunking`): reliable text as `evidence_type='text'`, vision reading as `evidence_type='visual'` with `page_reading_id`, low-OCR text never chunked.
    5. Recorded `job_steps` only after vision evaluation.
  - Idempotency & Retry: Re-runs produce zero duplicate chunks and zero duplicate readings. Transient vision errors re-raised for worker retry backoff.
- **Tests Added**:
  - `backend/tests/integration/test_upload_vision.py`: 10 integration tests covering recovery, re-run idempotency, image-heavy dual-indexing, high-OCR skip, digital text skip, empty vision failure, low confidence, missing confidence, image too large, and transient retry.
  - `backend/tests/unit/test_upload_vision_policy.py`: 15 unit tests covering trigger rules, review flag transitions, and reading selection.
  - `backend/tests/integration/test_migration_0007.py`: 4 database schema tests.
  - `backend/tests/unit/test_wiring_profile_guards.py`: 8 profile guard tests.
- **Decisions Made**:
  - `ReviewReason` required literal type on `flag_page_for_review` in both port and adapter (zero silent default).
  - Vision recovers low-OCR pages only; unrelated or legacy flags remain intact.
- **Verification**:
  - All 12 upload vision integration tests and 16 unit wiring tests pass (28 passed).
  - All 10 pre-commit hooks pass cleanly (`uv run pre-commit run --files ...`).

### 2026-10-12 - Task 2.5 Step 3: Additions & Fail-Closed Guards

- **Module**: `backend/app/wiring.py`, `backend/tests/unit/test_wiring_profile_guards.py`, `backend/tests/integration/test_upload_vision.py`
- **Spec Section**: Task 2.5 Step 3 Acceptance Additions (Leakage Invariant, Branch B Activation, Fail-Closed Wiring)
- **What Was Built**:
  - Fail-Closed Page Pipeline Wiring (`backend/app/wiring.py`):
    - In any non-mock profile (`office-cpu`, `desktop-gpu`, `home-gpu`, `dev-free-api`, `cloud`, or unauthorized), building the page pipeline via `create_page_pipeline_service` without a model provider or image processor raises `ConfigurationError`.
    - Removed `try/except ConfigurationError` silent fallback in `create_page_pipeline_service`.
  - Zero Text Leakage Invariant:
    - Added `test_upload_vision_zero_text_leakage_in_job_steps_and_audit` verifying synthetic transcription markers never appear in `job_steps`, `audit_events`, operational logs (`caplog`), or error messages. Confirmed stored only in `page_readings` content table.
  - Branch B Activation on Permanent Vision Error:
    - Added `test_upload_vision_permanent_error_flags_review_and_activates_branch_b` verifying permanent vision failure on a first version activates Branch B (version `needs_review`, `active_version_id` set, clean pages active, flagged page has zero active chunks).
- **Tests Added**:
  - `backend/tests/unit/test_wiring_profile_guards.py`: `TestPagePipelineWiringGuard` (non-mock profile refusal, explicit `model_provider=None` rejection, explicit `image_processor=None` rejection).
  - `backend/tests/integration/test_upload_vision.py`: `test_upload_vision_zero_text_leakage_in_job_steps_and_audit`, `test_upload_vision_permanent_error_flags_review_and_activates_branch_b`.
- **Verification**:
  - 28/28 affected tests pass in 1.51s.
  - All 10 pre-commit hooks pass cleanly on changed files.

---

### [2026-10-12] Task 2.8a Step 1: Knowledge Page Backend API (Upload & Status List)

- **Task ID**: Task 2.8a (Step 1 of 3)
- **Module**: `backend/app/api/routes/knowledge.py`, `backend/app/api/deps.py`, `backend/app/api/config.py`, `backend/app/adapters/db/postgres_document_repository.py`, `backend/app/ports/document_repository.py`, `backend/app/ports/types.py`, `backend/app/core/ingestion/pipeline.py`, `backend/app/db/session.py`
- **Spec Section**: Task 2.8a Knowledge Admin API (Assistant-scoped routing, Upload streaming in RAM, Status List allowlist states, Zero owner role in request path, Zero polling audit, Anti-probing 404)
- **What Was Built**:
  - **Assistant-Scoped Routing Pattern**:
    - Scoped routes under `/v1/assistants/{assistant_id}/knowledge/upload` and `/v1/assistants/{assistant_id}/knowledge/documents`, matching the Questions API pattern.
    - Tenant resolved strictly via `lookup_assistant_tenant(assistant_id)` as `app_user` (`SECURITY DEFINER`).
    - Membership verified via `auth_provider.roles(user_id, tenant_id)` under `app_user`. Unknown assistant or non-member returns `404 Not Found` (anti-probing); member with non-client-admin role (e.g. `staff`) returns `403 Forbidden`.
    - Zero usage of database owner role (`cka_admin`) in request paths. Completely deleted `get_admin_engine` and `get_admin_session_factory` from `app/db/session.py`.
  - **Memory-Only Streaming Upload Endpoint** (`POST /v1/assistants/{assistant_id}/knowledge/upload`):
    - Reads upload bytes directly from `request.stream()` into an in-memory `bytearray` up to `settings.max_upload_bytes`.
    - Strictly never instantiates Starlette's `SpooledTemporaryFile` or writes spool files to disk.
    - Exceeding size cap raises `HTTP 413 Content Too Large` (`HTTP_413_CONTENT_TOO_LARGE`).
    - Tenant isolation: opens `tenant_session(ctx)` directly in the endpoint.
    - Anti-probing: when replacing an existing `document_id`, verifies tenant ownership inside `tenant_session(ctx)` and returns `HTTP 404 Not Found` if missing or cross-tenant.
    - Response strictly returns IDs and status (`document_id`, `document_version_id`, `status`, `is_duplicate`), never filenames.
    - Enqueues background split job with IDs only (`tenant_id`, `document_version_id`) via `split_document_job.defer_async()`.
    - Compliance audit: records `AuditEvent` (`action="document_uploaded"`, `resource_id=document_version_id`) within the tenant transaction.
  - **Derived UI State Status List** (`GET /v1/assistants/{assistant_id}/knowledge/documents`):
    - Lists documents and versions for tenant scoped under `tenant_session(ctx)`.
    - Explicit allowlist state mapping: `ready` -> `Ready`, `needs_review` -> `Ready (N pages need review)`, `failed` -> `Failed`, `retired` -> `Retired`, `received` -> `Queued`, `processing` -> `Scanning images` / `Reading` / `Queued`.
    - Any unknown status raises `ValueError` and is logged by code only (`UNKNOWN_DOCUMENT_VERSION_STATUS`); never falls back to `Queued`.
    - Compliance rule: zero audit events logged on status list polling.
  - **Vision Job Step Start Telemetry** (`backend/app/core/ingestion/pipeline.py`):
    - `PagePipelineService.process_page` records `step="vision"`, `status="processing"` in `job_steps` when vision is triggered, followed by completed/failed step upon finish.
  - **CSRF Protection & Deprecation Hardening**:
    - CSRF protection required on every state-changing POST and DELETE request via `validate_csrf`.
    - Test harness sets cookies on `AsyncClient` instance directly, eliminating all `httpx` deprecation warnings.
- **Tests Added**:
  - `backend/tests/invariants/test_architecture_boundaries.py`:
    - `test_invariant_api_never_uses_owner_role_session`: AST scan of `app/api/` ensuring no import or call of `cka_admin`, `admin_session`, `get_admin_engine`, or `get_admin_session_factory`.
  - `backend/tests/integration/test_api_knowledge_endpoints.py`: 10 integration tests covering:
    - `test_knowledge_upload_streaming_memory_no_tempfile`
    - `test_knowledge_upload_streaming_size_limit_413`
    - `test_knowledge_upload_csrf_required`
    - `test_knowledge_upload_roles_staff_and_platform_admin_403`
    - `test_knowledge_upload_cross_tenant_document_id_returns_404`
    - `test_knowledge_multi_tenant_admin_and_staff_roles`
    - `test_knowledge_documents_status_list_derived_states_all_five`
    - `test_knowledge_documents_status_list_failed_and_unknown_status`
    - `test_knowledge_documents_status_list_no_audit_logged`
    - `test_knowledge_documents_roles_staff_and_platform_admin_403`
- **Verification**:
  - All 10 integration tests pass in 5.83s with 0 warnings.
  - All 24 boundary invariant tests pass in 0.39s.
  - All pre-commit hooks pass cleanly across all modified files.

---

### [2026-10-13] TASK 2.8a Step 2: Migration 0008, Review List, Page Image Proxy, Retire Endpoint, and Concurrency Gating

- **Task ID**: TASK 2.8a Step 2
- **What Was Built**:
  - **Alembic Migration 0008** (`backend/app/db/migrations/versions/0008_excluded_and_retired.py`):
    - Added `documents.is_retired` (Boolean, default False, server_default=False, nullable=False).
    - Added `pages.is_excluded` (Boolean, default False, server_default=False, nullable=False).
    - Added check constraint `ck_pages_excluded_needs_review` (`CHECK (NOT (is_excluded = true AND needs_review = true))`).
    - Revision ID `0008_excluded_and_retired` (25 characters, strictly $\le 32$ characters).
    - Tested exclusively in isolated throwaway databases; unapplied to the shared database.
  - **SQLAlchemy Models Update** (`backend/app/db/models.py`):
    - Added `is_retired` mapped column on `Document`.
    - Added `is_excluded` mapped column and `ck_pages_excluded_needs_review` constraint on `Page`.
  - **Domain Ports & Repository Methods**:
    - Updated `DocumentRecord` and `DocumentStatusInfo` with `is_retired: bool = False`.
    - Updated `PageRecord` with `is_excluded: bool = False`.
    - Added `DocumentRetiredError` exception to `app.ports.errors`.
    - Added `ReviewReadingInfo` and `ReviewPageInfo` data models to `app.ports.types`.
    - Extended `DocumentRepository` port and `PostgresDocumentRepository` adapter with:
      - `retire_document(tenant_id, document_id)`: locks Document row (`FOR UPDATE`), sets `is_retired = True`, clears `active_version_id = None`, sets `chunks.is_active = False` across all document chunks, and marks document versions `retired`.
      - `get_version_review_pages(tenant_id, version_id)`: validates version tenant ownership (404 anti-probing), retrieves flagged pages where `needs_review.is_(True)`, loads page readings, and formats review payloads.
  - **Version Activation Concurrency Gating** (`backend/app/core/ingestion/activation.py`):
    - `VersionActivationService.check_and_activate_if_complete` checks `doc.is_retired` under row lock and refuses activation (`return False`).
    - `VersionActivationService.activate_version` raises `DocumentRetiredError` if `doc.is_retired` is True.
  - **API Endpoints** (`backend/app/api/routes/knowledge.py`):
    - `GET /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/review`: client_admin only, assistant-scoped, returns flagged pages with readings, logs `AuditEvent(action="review_inspected")`.
    - `GET /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/image`: client_admin only, retrieves image bytes via `storage.get()`, sets `Content-Type: image/png` and `Cache-Control: private, no-transform, max-age=3600`, logs `AuditEvent(action="page_image_viewed")`.
    - `DELETE /v1/assistants/{assistant_id}/knowledge/documents/{document_id}`: client_admin only, CSRF double-submit token validated (`validate_csrf`), invokes `repo.retire_document()`, logs `AuditEvent(action="document_retired")`.
    - Anti-probing: all endpoints return 404 for unknown assistant, cross-tenant assistant, cross-tenant document/version/page.
    - RBAC: staff users return 403 Forbidden across all review, image proxy, and retire routes.
- **Tests Added**:
  - `backend/tests/integration/test_migration_0008.py`:
    - `test_migration_0008_upgrade_downgrade_lifecycle`: upgrade -> downgrade -> upgrade in throwaway DB.
    - `test_migration_0008_check_constraint_ck_pages_excluded_needs_review`: verifies rejection of `is_excluded=true AND needs_review=true` (SQLSTATE 23514).
  - `backend/tests/integration/test_api_knowledge_step2.py`:
    - `test_review_list_and_audit`: review list contents, reading text, image presence, compliance audit event.
    - `test_page_image_proxy_headers_and_audit`: binary png delivery, image cache-control headers, compliance audit event.
    - `test_retire_document_csrf_mutation_and_audit`: CSRF enforcement on DELETE, row lock mutation, active_version clearing, chunk deactivation, compliance audit event.
    - `test_anti_probing_cross_tenant_and_staff_rbac`: anti-probing 404s for cross-tenant calls and 403s for staff users.
    - `test_concurrency_retire_during_processing_never_goes_live`: document retired while version is processing -> completion activation refuses, active_version_id remains None, chunks stay inactive.
- **Verification**:
  - All 7 affected integration tests pass in 5.82s.
  - All 25 boundary and migration invariant tests pass in 0.23s.
  - All pre-commit hooks pass cleanly across all modified files.
  - Migration 0008 tested on throwaway DB only, unapplied to shared DB.

---

### [2026-10-13] TASK 2.8a Step 3 (a): Knowledge Review Actions & Promote (Accept, Exclude, Promote)

- **Task ID**: TASK 2.8a Step 3 (a)
- **What Was Built**:
  - **Domain Errors** (`backend/app/ports/errors.py`):
    - Added `NoUsableReadingError` (status code 422, error code `"NO_USABLE_READING"`).
    - Added `InvalidPageStateError` (status code 409, error code `"INVALID_PAGE_STATE"`).
  - **Domain Port Extensions** (`backend/app/ports/document_repository.py`):
    - Added `accept_page_review(tenant_id, version_id, page_number) -> tuple[PageRecord, bool]`: accepts a flagged page, indexes visual reading if available else text, clears `needs_review`, keeps `review_reason` for audit, and executes Branch A auto-promote or Branch B ready transition if last flag resolved.
    - Added `exclude_page_review(tenant_id, version_id, page_number) -> tuple[PageRecord, bool]`: excludes a flagged page (`is_excluded=True`, `needs_review=False`), deactivates chunks, and executes Branch A auto-promote or Branch B ready transition if last flag resolved.
    - Added `promote_version(tenant_id, version_id) -> DocumentVersionRecord`: manually promotes a version with exclusions, activates non-excluded chunks, retires previous active version, and sets status to `ready` (if no flagged pages remain) or retains `needs_review` (if flagged pages remain).
  - **Repository Implementation** (`backend/app/adapters/db/postgres_document_repository.py`):
    - Strict lock order: `SELECT ... FOR UPDATE` on `Document` row, then `DocumentVersion` row, then `Page` row.
    - Document retirement refusal: raises `DocumentRetiredError` (409) if `doc.is_retired is True`.
    - Double-click idempotency: returns immediately `(page_rec, False)` if page is already accepted or already excluded.
    - Invalid page state validation: raises `InvalidPageStateError` (409) if page is not flagged (`needs_review=False`) and not in the requested state.
    - Reading selection on accept: prefers visual reading from `page_readings` (`reading_type == "visual"`, referencing `page_reading_id`), falls back to OCR text reading (`reading_type == "text"`); raises `NoUsableReadingError` (422) if no usable reading exists.
    - Auto-promote on last flag resolved: activates version, retires prior version and deactivates its chunks, activates clean chunks, sets version status to `ready`, and writes separate `version_auto_promoted` compliance audit event with actor recorded as system (`user_id=None`).
    - Branch B transition: if version is already active and last flagged page is resolved, transitions status from `needs_review` to `ready`.
  - **API Endpoints** (`backend/app/api/routes/knowledge.py`):
    - `POST /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/accept` -> `PageActionResponse`.
    - `POST /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/exclude` -> `PageActionResponse`.
    - `POST /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/promote` -> `VersionPromoteResponse`.
    - All routes enforce `get_client_admin_tenant_context_for_assistant`, `validate_csrf` on POST, map domain errors to HTTP responses with specific detail codes (`INVALID_PAGE_STATE`, `NO_USABLE_READING`, `DOCUMENT_RETIRED`), and record compliance audit events (`page_accepted`, `page_excluded`, `version_promoted`).
- **Tests Added**:
  - `backend/tests/integration/test_api_knowledge_step3.py`: 11 integration tests covering:
    - `test_accept_flagged_page_with_vision_reading_indexes_visual`
    - `test_accept_flagged_page_with_ocr_reading_indexes_text`
    - `test_accept_flagged_page_without_reading_returns_422_no_usable_reading`
    - `test_accept_and_exclude_double_click_no_ops`
    - `test_accept_or_exclude_invalid_page_state_returns_409`
    - `test_exclude_flagged_page_keeps_chunks_inactive`
    - `test_auto_promote_on_last_flagged_page_resolved_retires_previous_version`
    - `test_branch_b_status_transitions_to_ready_when_last_flagged_page_resolved`
    - `test_promote_with_exclusions_status_rules`
    - `test_all_actions_refused_on_retired_document_returns_409`
    - `test_cross_tenant_anti_probing_and_staff_rbac_and_csrf`
- **Verification**:
  - All 11 integration tests pass.
  - All 24 boundary invariant tests pass.
  - Pre-commit run passes cleanly on all modified files.

---

### [2026-10-13] TASK 2.8a Step 3 (b): Knowledge Page Review Actions (Retry & Promote Preconditions)

- **Task ID**: TASK 2.8a Step 3 (b)
- **What Was Built**:
  - **Domain Errors** (`backend/app/ports/errors.py`):
    - Added `InvalidVersionStateError` (HTTP 409, error code `"INVALID_VERSION_STATE"`).
    - Added `RetryNotUsefulError` (HTTP 409, error code `"RETRY_NOT_USEFUL"`).
    - Added `PageJobConflictError` (HTTP 409, error code `"PAGE_JOB_ALREADY_ACTIVE"`).
  - **Port Interfaces** (`backend/app/ports/document_repository.py`):
    - Added `check_page_retry_eligibility(tenant_id, version_id, page_number, current_model_id, current_model_version, current_processing_version) -> None`.
    - Added `enqueue_page_retry(tenant_id, version_id, page_number) -> PageRecord`.
    - Added `re_evaluate_version_after_retry(tenant_id, version_id) -> tuple[DocumentVersionRecord, bool]`.
  - **Repository Implementation** (`backend/app/adapters/db/postgres_document_repository.py`):
    - **Promote Preconditions**:
      - Promoting version with status other than `needs_review` (e.g. `processing`, `failed`, `retired`, `received`) raises `InvalidVersionStateError` (409).
      - Promoting an already-active ready version is an idempotent no-op returning 200 with the active version record.
    - **Retry Eligibility** (`check_page_retry_eligibility`):
      - Strict lock order: `Document` -> `DocumentVersion` -> `Page` with `FOR UPDATE`.
      - Refuses with 409 `DocumentRetiredError` if document is retired.
      - Refuses with 409 `InvalidPageStateError` if page is not flagged (`needs_review=False`).
      - Refuses with 409 `PageJobConflictError` if a job step for that page is active or queued (`status in ('processing', 'todo', 'doing')`).
      - Refuses with 409 `RetryNotUsefulError` if a reading already exists for the failed step with current model and version and the processing version has not changed.
      - Allows retry if no reading row exists (e.g. `vision_failed`), or if the processing version has changed (e.g. from `v0` to `v1`).
    - **Retry Enqueueing** (`enqueue_page_retry`):
      - Creates or marks active job step with `status = "processing"`.
      - Keeps `page.needs_review = True` (page stays flagged until retried run finishes).
    - **Post-Retry Version Re-evaluation** (`re_evaluate_version_after_retry`):
      - Strict lock order: locks `Document` then `DocumentVersion` row.
      - On live version: activates clean page chunks; transitions version status to `ready` when 0 unexcluded flagged pages remain (Branch B).
      - On non-live version (Branch A): if 0 unexcluded flagged pages remain, auto-promotes version (`active_version_id = version_id`, `status = "ready"`, retires old active version and deactivates old chunks, activates clean chunks), and logs separate compliance audit event `version_auto_promoted` with system actor (`user_id = None`).
  - **API Endpoints** (`backend/app/api/routes/knowledge.py`):
    - `POST /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/retry` -> `PageRetryResponse`.
    - Enforces client_admin role, CSRF token validation, assistant tenant scoping (anti-probing 404), and maps domain errors to 409 detail codes (`PAGE_JOB_ALREADY_ACTIVE`, `RETRY_NOT_USEFUL`, `INVALID_PAGE_STATE`, `DOCUMENT_RETIRED`).
    - Emits compliance audit event `page_retry_enqueued` with resource ID `{version_id}:{page_number}`.
    - Updated `promote_version` route to map `InvalidVersionStateError` to 409 `INVALID_VERSION_STATE`.
  - **Integration Tests** (`backend/tests/integration/test_api_knowledge_step3.py`):
    - `test_promote_version_status_preconditions_409_invalid_version_state`
    - `test_promote_already_active_ready_version_is_noop_200`
    - `test_retry_page_not_flagged_returns_409_invalid_page_state`
    - `test_retry_retired_document_returns_409`
    - `test_retry_not_useful_when_reading_exists_with_current_model_and_version`
    - `test_retry_allowed_when_no_reading_row_exists_for_failed_step`
    - `test_retry_allowed_when_processing_version_changed`
    - `test_retry_refused_when_job_already_queued_or_running_returns_409`
    - `test_retry_success_on_live_version_activates_page_chunks`
    - `test_retry_success_resolves_last_flag_on_branch_a_auto_promotes`
    - `test_retry_cross_tenant_404_staff_403_csrf_required`
    - `test_retry_defer_failure_rolls_back_and_leaves_page_retryable`
    - `test_retry_end_to_end_through_run_page_job_activates_chunks_and_promotes`
    - `test_retry_passing_run_does_not_clear_legacy_unspecified_flag`
    - `test_retry_legacy_unspecified_hits_low_ocr_and_successful_vision_stays_flagged`
  - **Worker & Route Wiring**:
    - Dispatched `process_page_job.defer_async` in `retry_page` within `tenant_session` transaction with `raise HTTPException(...) from None` so failed defer aborts the session and rolls back `JobStep.status = 'processing'` without leaking connection traces, preventing the page from getting stuck in `PAGE_JOB_ALREADY_ACTIVE`.
    - Wired `VersionActivationService.check_and_activate_if_complete` to call `re_evaluate_version_after_retry` on retried pages for `needs_review` and `ready` versions.
    - Constrained pipeline review flag clearing: retried page can clear strictly reasons the re-run is responsible for (`processing_failed`, `vision_failed`, `image_too_large`, `low_ocr_confidence`), never clears `legacy_unspecified`, never replaces `legacy_unspecified` with another reason on re-runs (SQL `case` preservation), and never alters excluded pages.
  - **Verification**:
    - All 26 tests in `test_api_knowledge_step3.py` pass.
    - All 24 boundary invariant tests in `test_architecture_boundaries.py` pass.
    - Pre-commit hooks (`trailing-whitespace`, `end-of-file-fixer`, `check-added-large-files`, `detect-private-key`, `mixed-line-ending`, `detect-secrets`, `ruff`, `ruff-format`, `mypy`, `boundary-invariants`) pass cleanly across all modified files.
