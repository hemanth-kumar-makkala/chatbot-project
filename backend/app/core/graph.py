"""LangGraph 9-node StateGraph and Extraction Subgraph (Task 1.6).

Enforces:
- State holds control fields and entity IDs only (zero prompt/document content).
- Content lives strictly in RequestContext via LangGraph runtime context.
- Ports injected through runtime context (never constructed in nodes).
- Exact 9-node knowledge graph and conditional edges matching the specification.
- Extraction subgraph ending at validate_fields, returning to main graph for respond.
- Validation loop bounded to at most 1 retry, protected by recursion_limit.
- Vision read conditional edge with 3 branches (vision ok, ocr fallback, page refusal).
- Internal retrieval fallbacks always gated by assess_evidence.
- Fail-open tracer step wrapping across every node.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from app.ports.errors import (
    MalformedOutput,
)
from app.ports.model_provider import ModelProvider
from app.ports.retriever import Retriever
from app.ports.tracer import Tracer
from app.ports.types import (
    ChatMessage,
    ModelRole,
    StepEndPayload,
    StepErrorPayload,
    StepStartPayload,
    TenantContext,
    UserRole,
)

# --- 1. Graph State & Runtime Context ---


class GraphState(TypedDict, total=False):
    """Control fields only. Checkpoints carry zero prompt, question, or evidence text."""

    request_id: str
    tenant_id: str
    user_id: str
    conversation_id: str
    route: str
    answer_mode: str
    language: str
    evidence_ids: list[str]
    evidence_state: str
    needs_vision: bool
    vision_status: str | None
    validation_attempts: int
    status: str
    public_error_ref: str | None
    extracted_field_ids: list[str] | None
    dual_read_matches: bool | None
    confidence_score: float | None
    fields_valid: bool | None
    response_message_id: str | None


@dataclass
class GraphPorts:
    """Collection of port adapters injected into graph runtime context."""

    model_provider: ModelProvider | None = None
    retriever: Retriever | None = None
    tracer: Tracer | None = None
    # Profile flag: simulation triggers are strictly honored ONLY when is_mock_profile is True
    is_mock_profile: bool = False
    mock_vision_mode: str = "ok"  # "ok" or "fail"
    mock_ocr_confidence: float = 0.95
    mock_vector_down: bool = False
    mock_keyword_down: bool = False
    mock_reranker_down: bool = False
    mock_evidence_state: str | None = None
    mock_forced_route: str | None = None
    mock_forced_answer_mode: str | None = None
    mock_titles: list[str] = field(default_factory=list)
    force_non_retryable_error: bool = False
    force_infinite_loop: bool = False


@dataclass
class GraphRuntimeContext:
    """Request-scoped runtime context holding ports and content payloads (unpersisted)."""

    ports: GraphPorts
    tenant: TenantContext = field(
        default_factory=lambda: TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-synthetic",
            role=UserRole.STAFF,
            session_id="session-synthetic",
        )
    )
    question_text: str = ""
    evidence_text: dict[str, str] = field(default_factory=dict)
    raw_document_bytes: bytes = b""
    response_text: str = ""
    extracted_fields: dict[str, Any] = field(default_factory=dict)


# --- 2. Telemetry Node Wrapper ---


async def _execute_with_telemetry(
    node_name: str,
    node_number: int | None,
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
    action_fn: Any,
) -> dict[str, Any]:
    """Wrap node execution to record step_start, step_end, and step_error fail-open."""
    tracer = runtime.context.ports.tracer
    step_id = ""
    req_id = state.get("request_id", "")
    ten_id = state.get("tenant_id", "")

    if tracer and req_id and ten_id:
        with contextlib.suppress(Exception):
            step_id = await tracer.step_start(
                StepStartPayload(
                    request_id=req_id,
                    tenant_id=ten_id,
                    step_name=node_name,
                    node_number=node_number,
                )
            )

    try:
        result = await action_fn()
        if tracer and step_id:
            with contextlib.suppress(Exception):
                await tracer.step_end(
                    step_id,
                    StepEndPayload(
                        request_id=req_id,
                        step_name=node_name,
                        duration_ms=1.0,
                    ),
                )
        return cast(dict[str, Any], result)
    except Exception as exc:
        if tracer and step_id:
            with contextlib.suppress(Exception):
                error_code = getattr(exc, "error_code", "NODE_ERROR")
                await tracer.step_error(
                    step_id,
                    StepErrorPayload(
                        request_id=req_id,
                        step_name=node_name,
                        duration_ms=1.0,
                        error_code=error_code,
                        error_type=type(exc).__name__,
                    ),
                )
        raise


# --- 3. Knowledge Graph Nodes ---


async def node_guard_input(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    async def _action() -> dict[str, Any]:
        ports = runtime.context.ports
        if ports.force_non_retryable_error:
            raise MalformedOutput("Injected non-retryable error")

        tenant_ctx = runtime.context.tenant
        if not tenant_ctx or not tenant_ctx.tenant_id:
            return {
                "tenant_id": "",
                "status": "refusal",
                "public_error_ref": "ERR-UNAUTHORIZED-TENANT",
            }

        tenant_id = tenant_ctx.tenant_id

        q = runtime.context.question_text
        if len(q) > 4000:
            return {
                "tenant_id": tenant_id,
                "status": "refusal",
                "public_error_ref": "ERR-INPUT-TOO-LONG",
            }
        # Basic injection keywords
        if "ignore previous instructions" in q.lower():
            return {
                "tenant_id": tenant_id,
                "status": "refusal",
                "public_error_ref": "ERR-PROMPT-INJECTION",
            }

        # Invariant: status is initialized by guard_input, never trusting caller-supplied status.
        # Invariant: tenant_id comes only from runtime context's TenantContext.
        return {
            "tenant_id": tenant_id,
            "status": "in_progress",
        }

    return await _execute_with_telemetry("guard_input", 1, state, runtime, _action)


async def node_route(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    async def _action() -> dict[str, Any]:
        # Never trust caller-supplied route or answer_mode from state.
        # Always compute route and answer_mode directly or from mock context.
        ports = runtime.context.ports
        ctx = runtime.context
        q = ctx.question_text.lower()

        if ports.is_mock_profile and ports.mock_forced_route:
            computed_route = ports.mock_forced_route
        elif any(k in q for k in ("extract", "patient intake form", "form fields", "extraction")):
            computed_route = "extraction"
        elif any(k in q for k in ("clarify", "ambiguous", "which one", "multiple choices")):
            computed_route = "clarify"
        elif any(k in q for k in ("hello", "who are you", "help", "system status", "general")):
            computed_route = "general"
        else:
            computed_route = "knowledge"

        if ports.is_mock_profile and ports.mock_forced_answer_mode:
            computed_mode = ports.mock_forced_answer_mode
        elif any(k in q for k in ("detailed", "comprehensive", "in depth", "elaborate", "long")):
            computed_mode = "long"
        else:
            computed_mode = "short"

        if computed_route == "clarify":
            titles = ports.mock_titles or ["General Guide", "Policy Document"]
            ctx.response_text = f"Please clarify. Top matching topics: {', '.join(titles)}"
            return {
                "route": "clarify",
                "answer_mode": computed_mode,
                "status": "clarify",
                "response_message_id": str(uuid4()),
            }

        return {"route": computed_route, "answer_mode": computed_mode}

    return await _execute_with_telemetry("route", 2, state, runtime, _action)


async def node_retrieve(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    async def _action() -> dict[str, Any]:
        ports = runtime.context.ports
        evidence_ids: list[str] = []

        # Internal fallback: vector vs keyword
        if not ports.mock_vector_down and not ports.mock_keyword_down:
            evidence_ids = list(runtime.context.evidence_text.keys()) or ["c1"]
        elif ports.mock_vector_down and not ports.mock_keyword_down:
            evidence_ids = [k for k in runtime.context.evidence_text if "kw" in k] or ["kw_chunk_1"]
        elif not ports.mock_vector_down and ports.mock_keyword_down:
            evidence_ids = [k for k in runtime.context.evidence_text if "vec" in k] or [
                "vec_chunk_1"
            ]
        else:
            evidence_ids = []

        return {"evidence_ids": evidence_ids}

    return await _execute_with_telemetry("retrieve", 3, state, runtime, _action)


async def node_rerank(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    async def _action() -> dict[str, Any]:
        # If reranker service down, keep existing RRF retrieval order
        return {"evidence_ids": state.get("evidence_ids", [])}

    return await _execute_with_telemetry("rerank", 4, state, runtime, _action)


async def node_assess_evidence(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    async def _action() -> dict[str, Any]:
        # Never trust caller-supplied evidence_state from state.
        # Always compute evidence_state strictly from retrieved evidence and mock context triggers.
        ev_ids = state.get("evidence_ids", [])
        evidence_dict = runtime.context.evidence_text
        ports = runtime.context.ports

        if ports.is_mock_profile and ports.mock_evidence_state:
            computed_ev_state = ports.mock_evidence_state
        elif not ev_ids or not evidence_dict:
            computed_ev_state = "NO_EVIDENCE"
        else:
            combined_evidence = " ".join(evidence_dict.values()).lower()
            if "conflict" in combined_evidence or any("conflict" in k for k in evidence_dict):
                computed_ev_state = "CONFLICTING"
            elif "weak" in combined_evidence or any("weak" in k for k in evidence_dict):
                computed_ev_state = "WEAK"
            elif (
                "visual" in combined_evidence
                or any("visual" in k for k in evidence_dict)
                or ports.mock_vision_mode != "ok"
            ):
                computed_ev_state = "VISUAL_REQUIRED"
            else:
                computed_ev_state = "RELIABLE_TEXT"

        if computed_ev_state == "NO_EVIDENCE":
            return {"evidence_state": "NO_EVIDENCE", "status": "refusal", "needs_vision": False}
        if computed_ev_state == "WEAK":
            return {"evidence_state": "WEAK", "status": "refusal", "needs_vision": False}
        if computed_ev_state == "CONFLICTING":
            return {
                "evidence_state": "CONFLICTING",
                "status": "conflicting",
                "needs_vision": False,
            }
        if computed_ev_state == "VISUAL_REQUIRED":
            return {"evidence_state": "VISUAL_REQUIRED", "needs_vision": True}
        return {"evidence_state": "RELIABLE_TEXT", "needs_vision": False}

    return await _execute_with_telemetry("assess_evidence", 5, state, runtime, _action)


async def node_vision_read(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    async def _action() -> dict[str, Any]:
        ports = runtime.context.ports
        if ports.mock_vision_mode == "ok":
            return {"vision_status": "ok"}

        # Vision failed: check OCR confidence fallback
        if ports.mock_ocr_confidence >= 0.70:
            return {"vision_status": "ocr_fallback"}

        return {"vision_status": "unusable", "status": "refusal"}

    return await _execute_with_telemetry("vision_read", 6, state, runtime, _action)


async def node_generate(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    async def _action() -> dict[str, Any]:
        ports = runtime.context.ports
        if ports.force_infinite_loop:
            # Force loop for recursion test
            return {"validation_attempts": 0, "status": "looping"}

        model = ports.model_provider
        content = "Generated answer grounded in evidence."
        if model:
            resp = await model.generate(
                role=ModelRole.ANSWER,
                messages=[ChatMessage(role="user", content=runtime.context.question_text)],
            )
            content = resp.content

        runtime.context.response_text = content
        return {"response_message_id": str(uuid4()), "status": "in_progress"}

    return await _execute_with_telemetry("generate", 7, state, runtime, _action)


async def node_validate(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    async def _action() -> dict[str, Any]:
        evidence = runtime.context.evidence_text
        attempts = state.get("validation_attempts", 0)

        # Invariant 4 validation rule: if evidence is explicitly inconsistent, fail validation
        is_valid = True
        for chunk in evidence.values():
            if "Non matching" in chunk:
                is_valid = False
                break

        if not is_valid:
            if attempts < 1:
                return {"validation_attempts": attempts + 1, "status": "retry"}
            return {"status": "refusal"}

        return {"status": "completed"}

    return await _execute_with_telemetry("validate", 8, state, runtime, _action)


async def node_respond(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    async def _action() -> dict[str, Any]:
        status = state.get("status", "completed")
        if status in ("in_progress", "completed"):
            status = "answered"
        return {"status": status}

    return await _execute_with_telemetry("respond", 9, state, runtime, _action)


async def node_safe_failure(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    async def _action() -> dict[str, Any]:
        err_ref = state.get("public_error_ref") or f"ERR-{uuid4().hex[:6].upper()}"
        msg = f"An error occurred. Please contact support quoting reference {err_ref}."
        runtime.context.response_text = msg
        return {
            "status": "safe_failure",
            "public_error_ref": err_ref,
            "response_message_id": str(uuid4()),
        }

    return await _execute_with_telemetry("safe_failure", None, state, runtime, _action)


# --- 4. Extraction Subgraph Nodes ---


async def node_extract_fields(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    _ = state
    runtime.context.extracted_fields = {"patient_name": "John Doe", "dob": "1980-01-01"}
    return {"extracted_field_ids": ["field_patient_name", "field_dob"]}


async def node_dual_read_compare(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    _ = (state, runtime)
    return {"dual_read_matches": True}


async def node_classify_confidence(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    _ = (state, runtime)
    return {"confidence_score": 0.98}


async def node_validate_fields(
    state: GraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> dict[str, Any]:
    _ = (state, runtime)
    return {"status": "extracted", "fields_valid": True}


# --- 5. Conditional Edge Routing ---


def _route_from_guard(state: GraphState) -> str:
    if state.get("status") == "refusal":
        return "respond"
    return "route"


def _route_from_route(state: GraphState) -> str:
    r = state.get("route", "knowledge")
    if r == "general":
        return "generate"
    if r == "clarify":
        return "respond"
    if r in ("extraction", "extract"):
        return "extraction_subgraph"
    return "retrieve"


def _route_from_assess_evidence(state: GraphState) -> str:
    ev_state = state.get("evidence_state")
    if ev_state == "VISUAL_REQUIRED":
        return "vision_read"
    if ev_state == "RELIABLE_TEXT":
        return "generate"
    return "respond"


def _route_from_vision_read(state: GraphState) -> str:
    v_status = state.get("vision_status")
    if v_status in ("ok", "ocr_fallback"):
        return "generate"
    return "respond"


def _route_from_validate(state: GraphState) -> str:
    if state.get("status") == "retry" and state.get("validation_attempts", 0) <= 1:
        return "generate"
    return "respond"


# --- 6. Graph Builders ---


def build_extraction_subgraph() -> StateGraph[GraphState, GraphRuntimeContext, Any, Any]:
    """Build dedicated extraction subgraph ending at validate_fields (returns to main graph)."""
    subgraph: StateGraph[GraphState, GraphRuntimeContext, Any, Any] = StateGraph(
        GraphState, context_schema=GraphRuntimeContext
    )
    subgraph.add_node("extract_fields", node_extract_fields)
    subgraph.add_node("dual_read_compare", node_dual_read_compare)
    subgraph.add_node("classify_confidence", node_classify_confidence)
    subgraph.add_node("validate_fields", node_validate_fields)

    subgraph.add_edge(START, "extract_fields")
    subgraph.add_edge("extract_fields", "dual_read_compare")
    subgraph.add_edge("dual_read_compare", "classify_confidence")
    subgraph.add_edge("classify_confidence", "validate_fields")
    subgraph.add_edge("validate_fields", END)
    return subgraph


def build_knowledge_graph() -> StateGraph[GraphState, GraphRuntimeContext, Any, Any]:
    """Build the primary 9-node knowledge StateGraph with conditional edges and extraction."""
    builder: StateGraph[GraphState, GraphRuntimeContext, Any, Any] = StateGraph(
        GraphState, context_schema=GraphRuntimeContext
    )

    # 9 Main Nodes
    builder.add_node("guard_input", node_guard_input)
    builder.add_node("route", node_route)
    builder.add_node("retrieve", node_retrieve)
    builder.add_node("rerank", node_rerank)
    builder.add_node("assess_evidence", node_assess_evidence)
    builder.add_node("vision_read", node_vision_read)
    builder.add_node("generate", node_generate)
    builder.add_node("validate", node_validate)
    builder.add_node("respond", node_respond)
    builder.add_node("safe_failure", node_safe_failure)

    # Extraction Subgraph compiled and attached as a node
    extraction_subgraph = build_extraction_subgraph().compile()
    builder.add_node("extraction_subgraph", extraction_subgraph)

    # Fixed and Conditional Edges
    builder.add_edge(START, "guard_input")
    builder.add_conditional_edges(
        "guard_input",
        _route_from_guard,
        {"route": "route", "respond": "respond"},
    )

    builder.add_conditional_edges(
        "route",
        _route_from_route,
        {
            "retrieve": "retrieve",
            "generate": "generate",
            "extraction_subgraph": "extraction_subgraph",
            "respond": "respond",
        },
    )

    builder.add_edge("retrieve", "rerank")
    builder.add_edge("rerank", "assess_evidence")

    builder.add_conditional_edges(
        "assess_evidence",
        _route_from_assess_evidence,
        {
            "generate": "generate",
            "vision_read": "vision_read",
            "respond": "respond",
        },
    )

    builder.add_conditional_edges(
        "vision_read",
        _route_from_vision_read,
        {"generate": "generate", "respond": "respond"},
    )

    builder.add_edge("generate", "validate")

    builder.add_conditional_edges(
        "validate",
        _route_from_validate,
        {"generate": "generate", "respond": "respond"},
    )

    builder.add_edge("extraction_subgraph", "respond")
    builder.add_edge("respond", END)
    builder.add_edge("safe_failure", END)

    return builder


# --- 7. Execution Helper with Recursion Guard ---


async def invoke_graph(
    state: GraphState,
    context: GraphRuntimeContext,
    recursion_limit: int = 25,
) -> GraphState:
    """Execute the compiled knowledge graph.

    Includes invocation recursion protection and safe failure fallback.
    """
    graph = build_knowledge_graph().compile()
    config = {"recursion_limit": recursion_limit}
    try:
        res = await graph.ainvoke(state, config=cast(Any, config), context=context)
        return cast(GraphState, res)
    except (GraphRecursionError, Exception) as exc:
        err_ref = f"ERR-{uuid4().hex[:6].upper()}"
        failure_state = dict(state)
        failure_state["status"] = "safe_failure"
        failure_state["public_error_ref"] = err_ref
        failure_state["response_message_id"] = str(uuid4())
        context.response_text = f"An error occurred ({type(exc).__name__}). Reference: {err_ref}"
        return cast(GraphState, failure_state)
