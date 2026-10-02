# Phase 1: Engine on Mock Profile

## Goal
Build and verify the repository structure, database schema with row-level security (RLS), authentication and roles, ports, mock provider with failure injection, 9-node LangGraph skeleton, three log stores, invariant tests, import boundary rules, graph contract tests, failure-mode tests, pre-commit checks, and LangGraph Studio configuration on the `mock` profile.

- **Runs on**: 8 GB office PC (Docker memory capped at 3 GB).
- **Parallel Execution**: Phase 1 runs in parallel with Phase 0. Uses only the mock profile and synthetic data.

## Scope
- PostgreSQL database schema with Alembic migrations and row-level security (RLS) on tenant tables.
- Authentication and session-derived tenant role handling (`AuthProvider`).
- Pure abstract port interfaces (`backend/app/ports/`).
- Mock `ModelProvider` adapter with configurable failure injection (timeout, error, malformed JSON, empty answer, slow response).
- LangGraph 9-node question flow:
  1. `guard_input`
  2. `route`
  3. `retrieve`
  4. `rerank`
  5. `assess_evidence` (the gate)
  6. `vision_read`
  7. `generate`
  8. `validate`
  9. `respond`
  - Plus `safe_failure` for unrecoverable errors after retries.
- Request-scoped context object: question and evidence text loaded once per request outside checkpointed graph state. Graph state holds IDs only.
- Three log stores: operational logger (metadata only), trace store (`run_steps`), and audit logger (`audit_events`).
- AST import boundary check ensuring `backend/app/core/` never imports vendors or adapters.
- Invariant test harness covering all 15 invariants on the mock profile.
- Graph contract test compiling the graph and validating nodes, transitions, and edge conditions against the spec.
- Failure-mode test suite covering every row of the spec's fallback table.
- Pre-commit checks (Ruff linting, Mypy strict type checking, pytest).
- LangGraph Studio configuration for local visual debugging with `LANGSMITH_TRACING=false`.

## Out of Scope
- Real Ollama model inference or real GPU workloads.
- Real Docling OCR model weights.
- Production GCP cloud deployment.
- Real client documents or real PHI.

## Dependencies
- Phase 0 repository skeleton and configuration files.
- Python 3.12 environment with pinned dependencies.

## Numbered Tasks

### Task 1.1: Pre-commit Quality Checks and Linting Setup
- **Description**: Configure Ruff, Mypy strict type checking, and pre-commit hooks to enforce Python 3.12 typing and code standards.
- **Tests to write first**:
  - `tests/unit/test_precommit_config.py`: Verifies configuration syntax and asserts strict typing flags are enabled.

### Task 1.2: Architecture Import Boundary Enforcement Rules
- **Description**: Implement an automated AST/import analyzer rule that inspects all modules in `backend/app/core/` and fails the build if any module imports from `backend/app/adapters/` or external vendor packages.
- **Tests to write first**:
  - `tests/invariants/test_architecture_boundaries.py`: Assert that AST scanning flags forbidden imports in `core/` and passes only when `core/` depends solely on `ports/`.

### Task 1.3: Port Interface Definitions
- **Description**: Define pure abstract base classes in `backend/app/ports/` for `ModelProvider`, `EmbeddingProvider`, `Reranker`, `DocumentParser`, `StorageBackend`, `JobQueue`, `AuthProvider`, `Tracer`, and `Retriever`.
- **Tests to write first**:
  - `tests/unit/test_ports_contracts.py`: Validate interface method signatures, parameter types, and ensure no interface module contains concrete logic.

### Task 1.4: Database Schema with RLS and Three Log Stores
- **Description**: Implement initial SQLAlchemy models, Alembic migrations, tenant RLS policies, and three separate log stores: operational logger (metadata only), trace store (`run_steps`), and audit log (`audit_events`).
- **Tests to write first**:
  - `tests/integration/test_db_rls_and_telemetry.py`: Verify tenant RLS isolation and assert operational logs contain zero prompt or document text.

### Task 1.5: Mock ModelProvider with Configurable Failure Modes
- **Description**: Build `backend/app/adapters/models/mock.py` implementing `ModelProvider` with deterministic canned responses and explicit failure simulation injection: timeout, network error, malformed JSON, empty answer, and slow response.
- **Tests to write first**:
  - `tests/unit/test_mock_provider.py`: Verify that setting failure triggers raises appropriate exceptions and that normal execution returns schema-compliant JSON payloads.

### Task 1.6: LangGraph 9-Node State Graph, Extraction Subgraph, and Contract Tests
- **Description**: Define `GraphState` containing control fields and entity IDs only (`request_id`, `tenant_id`, `user_id`, `conversation_id`, `route`, `answer_mode`, `language`, `evidence_ids`, `evidence_state`, `needs_vision`, `status`, `public_error_ref`). Implement request-scoped context object for question/evidence text. Build the 9-node knowledge StateGraph (1 `guard_input`, 2 `route`, 3 `retrieve`, 4 `rerank`, 5 `assess_evidence`, 6 `vision_read`, 7 `generate`, 8 `validate`, 9 `respond`) with `safe_failure`. In Node 2, wire `clarify` to retrieve top 3 topics via lightweight read-only document title/heading search through the Retriever (not from the model). Define the dedicated extraction subgraph skeleton (`extract_fields` → `dual_read_compare` → `classify_confidence` → `validate_fields`) reusing `guard_input` and `respond`. Build contract tests that compile both graphs and compare nodes and edges against the spec.
- **Tests to write first**:
  - `tests/integration/test_graph_contract.py`: Assert the compiled knowledge graph contains all 9 nodes with correct numbers and transitions, assert the extraction subgraph compiles cleanly as a separate contract, and assert checkpoints contain IDs only.
  - `tests/integration/test_clarify_search.py`: Assert `clarify` topics are fetched from stored document metadata via Retriever and reject ungrounded model-generated topics.

### Task 1.7: Invariant Test Harness (All 15 Invariants)
- **Description**: Author comprehensive automated tests for all 15 invariants (initially failing or stubbed as red tests) in `backend/tests/invariants/test_invariants.py`.
- **Tests to write first**:
  - `tests/invariants/test_invariants.py`: Formally asserts Invariants 1 through 15 across adapter permissions, tenant authorization, evidence sufficiency, visual labeling, unreadable field states, PHI session boundaries, version/page tracing, telemetry data scrubbing, and tenant hash segregation.

### Task 1.8: Fallback Table Failure-Mode Test Suite
- **Description**: Author integration tests exercising every row of the spec's failure handling table: model timeout retry, model down safe failure, vector search fail, keyword search fail, reranker fail, vision read fail, validation retry and refusal, conflicting evidence, weak evidence refusal, database down, and crash recovery mid-question.
- **Tests to write first**:
  - `tests/integration/test_failure_modes.py`: Execute test scenarios injecting component failures and verifying the graph transitions to the specified fallback and generates exact user-facing error references.

### Task 1.9: LangGraph Studio Setup for Local Debugging
- **Description**: Configure `langgraph.json` pointing to the mock graph definition with `LANGSMITH_TRACING=false` to support visual node-by-node local debugging without external network dependencies.
- **Tests to write first**:
  - `tests/unit/test_langgraph_config.py`: Assert `langgraph.json` points to valid entry points and verifies tracing environment variables default to off.

## Exit Gate (from Spec)
- [ ] Every invariant and failure-mode test passes on the mock profile.

## Risks
- Graph state accidentally capturing full prompt strings or document texts instead of IDs (mitigated by request-scoped context object).
- Complex LangGraph retry loops causing deadlocks during mocked provider timeouts.
