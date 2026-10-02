# Client Knowledge Assistant

A staff-facing, per-client knowledge assistant engineered with a fixed custom engine and swappable infrastructure, capable of running locally at $0 and deploying onto Google Cloud Platform (GCP) under a Business Associate Agreement (BAA).

Designed to replace third-party black-box tools (such as Chatbase) with strict evidence grounding, multimodal page-level processing, deterministic field extraction for protected health information (PHI), and zero cross-tenant data leakage.

## Architecture & Specification
- Architecture Spec: [`Client Knowledge Assistant Architecture and Build Plan.md`](file:///Client%20Knowledge%20Assistant%20Architecture%20and%20Build%20Plan.md)
- System Rules & Invariants: [`AGENTS.md`](file:///AGENTS.md) and [`.agents/rules/invariants.md`](file:///.agents/rules/invariants.md)
- Development Status: [`docs/STATUS.md`](file:///docs/STATUS.md)
- Build History: [`docs/BUILD_LOG.md`](file:///docs/BUILD_LOG.md)
- Phase Roadmaps: [`docs/phases/`](file:///docs/phases/)

## Repository Structure
```text
chatbots-project/
  AGENTS.md           # rules Antigravity loads every session
  .agents/            # rules/ and workflows/
  backend/            # FastAPI, core LangGraph engine, ports, adapters, db, tests
  prompts/            # registry.yaml (every prompt with id and version)
  evals/              # golden/ (synthetic docs + questions) and reports/ (saved results)
  web/                # Next.js web application
  docker-compose.yml  # postgres(pgvector), api, worker, web, langfuse (optional)
  docs/               # phases/, decisions/, STATUS.md, BUILD_LOG.md, etc.
```

## Getting Started
1. Review [`AGENTS.md`](file:///AGENTS.md) before starting any development session.
2. Inspect [`docs/STATUS.md`](file:///docs/STATUS.md) for current phase objectives.
3. Configure environment variables using [`.env.example`](file:///.env.example).
