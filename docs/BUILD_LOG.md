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
