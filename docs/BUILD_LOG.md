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




