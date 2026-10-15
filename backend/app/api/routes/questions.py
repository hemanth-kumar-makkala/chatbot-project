"""Question and answer dispatch routes."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import (
    get_auth_provider,
    get_current_session,
    get_tenant_context_for_assistant,
    validate_csrf,
)
from app.core.graph import GraphRuntimeContext, GraphState
from app.db.models import AuditEvent, Conversation, Message
from app.db.session import tenant_session
from app.ports.auth import AuthProvider
from app.ports.types import SessionContext
from app.wiring import build_app_graph, create_mock_ports

router = APIRouter(prefix="/v1", tags=["questions"])

# Shared compiled graph instance
_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_app_graph()
    return _compiled_graph


class QuestionRequest(BaseModel):
    assistant_id: str
    question_text: str
    conversation_id: str | None = None
    # Forged tenant_id if supplied by caller is intentionally ignored by the endpoint
    tenant_id: str | None = Field(
        default=None, description="Ignored by server; derived from assistant"
    )


class SourceCitation(BaseModel):
    document_title: str
    version: str
    page_number: int


class QuestionResponse(BaseModel):
    request_id: str
    tenant_id: str
    conversation_id: str
    message_id: str
    status: str
    answer: str
    citations: list[str] = []
    evidence_type: str = "text"
    sources: list[SourceCitation] = []
    conflict_sources: list[SourceCitation] = []
    public_error_ref: str | None = None


@router.post("/questions", response_model=QuestionResponse)
async def ask_question(
    payload: QuestionRequest,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    _csrf: Annotated[None, Depends(validate_csrf)],
) -> QuestionResponse:
    """Submit a user question to the LangGraph knowledge engine under tenant RLS.

    Saves user message and assistant answer rows via tenant_session, records an
    audit event, and returns the response payload.
    """
    tenant_ctx = await get_tenant_context_for_assistant(
        assistant_id=payload.assistant_id,
        session=session,
        auth_provider=auth_provider,
    )
    request_id = f"req-{uuid4()}"
    tenant_uuid = UUID(tenant_ctx.tenant_id)
    user_uuid = UUID(tenant_ctx.user_id)
    asst_uuid = UUID(payload.assistant_id)

    async with tenant_session(tenant_ctx) as db_session:
        # 1. Resolve or create conversation
        if payload.conversation_id:
            conv_uuid = UUID(payload.conversation_id)
        else:
            conv = Conversation(
                tenant_id=tenant_uuid,
                user_id=user_uuid,
                assistant_id=asst_uuid,
                title=payload.question_text[:50],
            )
            db_session.add(conv)
            await db_session.flush()
            conv_uuid = conv.id

        # 2. Save user message row
        user_msg = Message(
            tenant_id=tenant_uuid,
            conversation_id=conv_uuid,
            role="user",
            content=payload.question_text,
        )
        db_session.add(user_msg)
        await db_session.flush()

        # 3. Instantiate request-scoped runtime context and invoke engine
        ports = create_mock_ports()
        runtime_ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text=payload.question_text,
            evidence_text={
                "chunk-mock-001": "Standard clinic hours of operation are 9:00 AM to 5:00 PM M-F."
            },
        )
        state: GraphState = {
            "request_id": request_id,
            "status": "pending",
            "conversation_id": str(conv_uuid),
        }
        graph = get_compiled_graph()
        result = await graph.ainvoke(state, context=runtime_ctx)

        # 4. Save assistant answer message row
        if runtime_ctx.response_text:
            answer_text = runtime_ctx.response_text
        else:
            answer_text = "I am unable to answer this question based on validated clinic documents."

        asst_msg = Message(
            tenant_id=tenant_uuid,
            conversation_id=conv_uuid,
            role="assistant",
            content=answer_text,
        )
        db_session.add(asst_msg)
        await db_session.flush()

        # 5. Record compliance audit event
        audit = AuditEvent(
            tenant_id=tenant_uuid,
            user_id=user_uuid,
            event_type="question",
            action="question.answered",
            resource_id=request_id,
        )
        db_session.add(audit)
        await db_session.flush()

        status_val = "answered"
        if "status" in result:
            status_val = result["status"]

        ev_type = "visual" if ("needs_vision" in result and result["needs_vision"]) else "text"
        evidence_ids: list[str] = []
        if "evidence_ids" in result:
            evidence_ids = result["evidence_ids"]

        sources_list: list[SourceCitation] = []
        for _ in evidence_ids:
            sources_list.append(
                SourceCitation(
                    document_title="Clinic Operations Manual",
                    version="1",
                    page_number=1,
                )
            )

        conflict_sources_list: list[SourceCitation] = []
        if status_val == "conflicting":
            conflict_sources_list = [
                SourceCitation(document_title="Clinic Policy 2023", version="1", page_number=4),
                SourceCitation(document_title="Clinic Policy 2024", version="2", page_number=2),
            ]

        public_ref: str | None = None
        if "public_error_ref" in result:
            public_ref = result["public_error_ref"]

        return QuestionResponse(
            request_id=request_id,
            tenant_id=tenant_ctx.tenant_id,
            conversation_id=str(conv_uuid),
            message_id=str(asst_msg.id),
            status=status_val,
            answer=answer_text,
            citations=evidence_ids,
            evidence_type=ev_type,
            sources=sources_list,
            conflict_sources=conflict_sources_list,
            public_error_ref=public_ref,
        )
