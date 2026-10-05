# ADR-0006: LangGraph Studio Local Debugging Entrypoint & Runtime Context Injection

## Status
Accepted (Phase 1, Task 1.7)

## Context
In LangGraph 0.6+ / 1.0+, `StateGraph` supports a runtime context schema (`context_schema=GraphRuntimeContext`).
Per the project architecture specification, this mechanism isolates uncheckpointed prompt strings, evidence text,
and concrete port instances (`ModelProvider`, `Retriever`, `Tracer`) from persisted graph state, guaranteeing
that checkpointers never store PHI or prompt bodies.

However, LangGraph Studio UI and the local development server (`langgraph dev`) only expose input controls for
state channels (`input`) and execution configuration (`thread_id`). There is no mechanism in the Studio web UI
for an operator to instantiate arbitrary Python dataclasses as runtime context. Consequently, when a run is
triggered from the Studio UI, LangGraph server invokes `graph.astream(input, config=config, context=None)`.
Because `runtime.context` evaluates to `None`, the first node (`guard_input`) immediately raises
`AttributeError: 'NoneType' object has no attribute 'ports'`.

We evaluated two architectural questions:
1. Where should Studio debugging configuration live relative to production composition wiring?
2. How should the default mock runtime context be supplied during Studio invocations?

## Decision

### 1. Dedicated Studio Entrypoint (`backend/studio/entry.py`)
Production composition wiring in `backend/app/wiring.py` must remain pure and free from any developer-facing
shims or monkeypatched methods. Therefore:
- `backend/app/wiring.py` defines `build_app_graph()`, serving strictly as the production composition root.
- A dedicated module `backend/studio/entry.py` provides the Studio entrypoint referenced by `langgraph.json`:
  `"knowledge_assistant": "./studio/entry.py:make_studio_graph"`.
- This keeps Studio debugging utilities decoupled from core application code and adheres to architectural
  import boundary invariants.

### 2. Subclass Wrapper (`StudioCompiledGraph`) Over Monkeypatching
To supply the default mock runtime context without mutating or monkeypatching graph instances:
- `StudioCompiledGraph` inherits from `CompiledStateGraph[GraphState, GraphRuntimeContext, Any, Any]`.
- It overrides `ainvoke`, `astream`, `invoke`, `stream`, and `copy()`.
- If `context is None` (the Studio UI default), it supplies `get_default_studio_context()`, which wires
  `MockModelProvider` and `MockRetriever` with synthetic healthcare practice data.
- If `context` is explicitly supplied (e.g. in contract tests or automated callers), the user's context is honored.
- It overrides `copy(update=...)` so that internal graph cloning by LangGraph's API server maintains the
  `StudioCompiledGraph` wrapper without failing on `Pregel` keyword arguments.

### 3. Complete Data Privacy (`LANGSMITH_TRACING=false`)
To ensure zero data egress from the development machine:
- `LANGSMITH_TRACING=false` is enforced in `.env.example` and `.env`.
- Visual debugging operates strictly in-memory using `MemorySaver`.

## Consequences
- Developers can run `uv run langgraph dev` and test the 9-node knowledge graph visually in LangGraph Studio.
- Submitting a test question from Studio UI executes end-to-end to status `answered` out of the box.
- Production wiring in `app/wiring.py` remains pure and production-ready.
- Boundary enforcement rules pass cleanly.
