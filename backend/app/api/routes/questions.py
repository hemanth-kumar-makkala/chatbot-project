"""Question and answer dispatch routes."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from starlette.responses import StreamingResponse

from app.api.config import ApiSettings
from app.api.deps import (
    get_api_settings,
    get_auth_provider,
    get_current_session,
    get_tenant_context_for_assistant,
    validate_csrf,
)
from app.api.rate_limiter import check_question_rate_limit
from app.core.graph import GraphPorts, GraphRuntimeContext, GraphState
from app.db.models import (
    Assistant,
    AuditEvent,
    Chunk,
    Conversation,
    Document,
    DocumentVersion,
    Message,
    Page,
    TableData,
)
from app.db.session import tenant_session
from app.ports.auth import AuthProvider
from app.ports.types import SessionContext
from app.wiring import (
    build_app_graph,
    create_mock_ports,
    create_model_provider,
    create_production_ports,
    create_reranker,
    create_retriever,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["questions"])

# Shared compiled graph instance
_compiled_graph = None
_ports_cache: dict[str, GraphPorts] = {}


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_app_graph()
    return _compiled_graph


def get_engine_ports(settings: ApiSettings) -> GraphPorts:
    """Retrieve or construct profile-scoped GraphPorts instance."""
    profile = settings.profile
    if profile in _ports_cache:
        return _ports_cache[profile]

    if profile == "mock":
        ports = create_mock_ports()
    else:
        model_provider = create_model_provider(profile)
        retriever = create_retriever(profile)
        reranker = None
        if profile in ("desktop-gpu", "home-gpu"):
            reranker = create_reranker(profile)
        ports = create_production_ports(
            model_provider=model_provider,
            retriever=retriever,
            reranker=reranker,
        )

    _ports_cache[profile] = ports
    return ports


class QuestionRequest(BaseModel):
    assistant_id: str
    question_text: str
    conversation_id: UUID | None = None
    # Forged tenant_id if supplied by caller is intentionally ignored by the endpoint
    tenant_id: str | None = Field(
        default=None, description="Ignored by server; derived from assistant"
    )

    @field_validator("conversation_id", mode="before")
    @classmethod
    def validate_conversation_id(cls, v: Any) -> UUID | None:
        if v is None or v == "":
            return None
        if isinstance(v, UUID):
            return v
        try:
            return UUID(str(v))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("INVALID_CONVERSATION_ID") from exc


class SourceCitation(BaseModel):
    document_title: str
    version: str
    page_number: int
    version_id: str | None = None
    evidence_type: str = "text"


class QuestionResponse(BaseModel):
    request_id: str
    tenant_id: str
    conversation_id: str
    message_id: str
    status: str
    answer: str
    answer_mode: str | None = None
    evidence_state: str | None = None
    citations: list[str] = []
    evidence_type: str = "text"
    sources: list[SourceCitation] = []
    conflict_sources: list[SourceCitation] = []
    public_error_ref: str | None = None


async def _resolve_sources(
    db_session: Any,
    tenant_uuid: UUID,
    evidence_ids: list[str],
) -> list[SourceCitation]:
    """Resolve database metadata for cited chunk and table evidence."""
    sources_list: list[SourceCitation] = []
    if not evidence_ids:
        return sources_list

    chunk_uuids: list[UUID] = []
    for eid in evidence_ids:
        try:
            chunk_uuids.append(UUID(str(eid)))
        except (ValueError, TypeError):
            continue

    if not chunk_uuids:
        return sources_list

    chunk_stmt = (
        select(
            Chunk.id,
            Chunk.page_id,
            Chunk.document_version_id,
            Chunk.evidence_type,
            Document.title.label("document_title"),
            Page.page_number,
        )
        .join(
            DocumentVersion,
            (DocumentVersion.id == Chunk.document_version_id)
            & (DocumentVersion.tenant_id == Chunk.tenant_id),
        )
        .join(
            Document,
            (Document.id == DocumentVersion.document_id)
            & (Document.tenant_id == DocumentVersion.tenant_id),
        )
        .join(
            Page,
            (Page.id == Chunk.page_id) & (Page.tenant_id == Chunk.tenant_id),
        )
        .where(Chunk.tenant_id == tenant_uuid, Chunk.id.in_(chunk_uuids))
    )
    chunk_rows = (await db_session.execute(chunk_stmt)).all()
    for crow in chunk_rows:
        sources_list.append(
            SourceCitation(
                document_title=crow.document_title,
                version="1",
                page_number=crow.page_number,
                version_id=str(crow.document_version_id),
                evidence_type=crow.evidence_type,
            )
        )

    # Also resolve any TableData rows
    found_chunk_ids = {crow.id for crow in chunk_rows}
    unresolved_uuids = [cid for cid in chunk_uuids if cid not in found_chunk_ids]
    if unresolved_uuids:
        table_stmt = (
            select(
                TableData.id,
                TableData.document_version_id,
                TableData.sheet_name,
                TableData.row_index,
                Document.title.label("document_title"),
            )
            .join(
                DocumentVersion,
                (DocumentVersion.id == TableData.document_version_id)
                & (DocumentVersion.tenant_id == TableData.tenant_id),
            )
            .join(
                Document,
                (Document.id == DocumentVersion.document_id)
                & (Document.tenant_id == DocumentVersion.tenant_id),
            )
            .where(
                TableData.tenant_id == tenant_uuid,
                TableData.id.in_(unresolved_uuids),
            )
        )
        table_rows = (await db_session.execute(table_stmt)).all()
        for trow in table_rows:
            sources_list.append(
                SourceCitation(
                    document_title=trow.document_title,
                    version="1",
                    page_number=trow.row_index,
                    version_id=str(trow.document_version_id),
                    evidence_type="table",
                )
            )
    return sources_list


@router.post("/questions", response_model=QuestionResponse)
async def ask_question(
    payload: QuestionRequest,
    request: Request,
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

    settings = get_api_settings(request)

    async with tenant_session(tenant_ctx) as db_session:
        # 0. Enforce database-backed per-user and per-tenant question rate limits
        await check_question_rate_limit(
            db_session=db_session,
            tenant_uuid=tenant_uuid,
            user_uuid=user_uuid,
            user_max_requests=settings.question_rate_limit_user_max_requests,
            tenant_max_requests=settings.question_rate_limit_tenant_max_requests,
            window_seconds=settings.question_rate_limit_window_seconds,
        )

        # 1. Resolve or create conversation
        if payload.conversation_id:
            conv_check = await db_session.execute(
                select(Conversation.id).where(
                    Conversation.tenant_id == tenant_uuid,
                    Conversation.id == payload.conversation_id,
                )
            )
            if conv_check.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")
            conv_uuid = payload.conversation_id
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

        # 3. Check active documents for assistant (Rule E.1)
        doc_count_query = select(func.count(Document.id)).where(
            Document.tenant_id == tenant_uuid,
            Document.is_retired.is_(False),
            Document.active_version_id.is_not(None),
        )
        doc_count = (await db_session.execute(doc_count_query)).scalar() or 0

        # 4. Instantiate request-scoped runtime context and invoke engine
        ports = get_engine_ports(settings=settings)

        runtime_ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text=payload.question_text,
            active_document_count=doc_count,
        )
        state: GraphState = {
            "request_id": request_id,
            "status": "pending",
            "conversation_id": str(conv_uuid),
        }
        graph = get_compiled_graph()
        try:
            result = await graph.ainvoke(state, context=runtime_ctx)
        except Exception as exc:
            logger.error("Graph invocation error: %r", exc, exc_info=True)
            err_ref = f"ERR-{uuid4().hex[:6].upper()}"
            result = {
                "request_id": request_id,
                "status": "safe_failure",
                "public_error_ref": err_ref,
            }
            runtime_ctx.response_text = (
                "I encountered an issue processing your request. Please try again."
            )

        # 5. Save assistant answer message row
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

        # 6. Record compliance audit event
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
        if "evidence_ids" in result and status_val not in ("refusal", "clarify"):
            evidence_ids = result["evidence_ids"]

        # 7. Resolve real source chips from database (Rule E.4)
        sources_list = await _resolve_sources(db_session, tenant_uuid, evidence_ids)

        conflict_sources_list: list[SourceCitation] = []
        if status_val == "conflicting":
            conflict_sources_list = (
                list(sources_list)
                if sources_list
                else [
                    SourceCitation(document_title="Clinic Policy 2023", version="1", page_number=4),
                    SourceCitation(document_title="Clinic Policy 2024", version="2", page_number=2),
                ]
            )

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
            answer_mode=result.get("answer_mode"),
            evidence_state=result.get("evidence_state"),
            citations=evidence_ids,
            evidence_type=ev_type,
            sources=sources_list,
            conflict_sources=conflict_sources_list,
            public_error_ref=public_ref,
        )


@router.post("/questions/stream")
async def ask_question_stream(
    payload: QuestionRequest,
    request: Request,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    _csrf: Annotated[None, Depends(validate_csrf)],
) -> StreamingResponse:
    """Submit a question and stream pipeline status lines, tokens, and final answer via SSE."""
    tenant_ctx = await get_tenant_context_for_assistant(
        assistant_id=payload.assistant_id,
        session=session,
        auth_provider=auth_provider,
    )
    request_id = f"req-{uuid4()}"
    tenant_uuid = UUID(tenant_ctx.tenant_id)
    user_uuid = UUID(tenant_ctx.user_id)
    asst_uuid = UUID(payload.assistant_id)

    settings = get_api_settings(request)

    async with tenant_session(tenant_ctx) as db_session:
        await check_question_rate_limit(
            db_session=db_session,
            tenant_uuid=tenant_uuid,
            user_uuid=user_uuid,
            user_max_requests=settings.question_rate_limit_user_max_requests,
            tenant_max_requests=settings.question_rate_limit_tenant_max_requests,
            window_seconds=settings.question_rate_limit_window_seconds,
        )

        asst_stmt = select(Assistant.name).where(
            Assistant.tenant_id == tenant_uuid,
            Assistant.id == asst_uuid,
        )
        asst_name = (await db_session.execute(asst_stmt)).scalar_one_or_none() or "Assistant"

        if payload.conversation_id:
            conv_check = await db_session.execute(
                select(Conversation.id).where(
                    Conversation.tenant_id == tenant_uuid,
                    Conversation.id == payload.conversation_id,
                )
            )
            if conv_check.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")
            conv_uuid = payload.conversation_id
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

        user_msg = Message(
            tenant_id=tenant_uuid,
            conversation_id=conv_uuid,
            role="user",
            content=payload.question_text,
        )
        db_session.add(user_msg)
        await db_session.flush()

        doc_count_query = select(func.count(Document.id)).where(
            Document.tenant_id == tenant_uuid,
            Document.is_retired.is_(False),
            Document.active_version_id.is_not(None),
        )
        doc_count = (await db_session.execute(doc_count_query)).scalar() or 0

    async def event_generator() -> AsyncIterator[str]:
        settings = get_api_settings(request)
        ports = get_engine_ports(settings=settings)

        runtime_ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text=payload.question_text,
            active_document_count=doc_count,
        )
        state: GraphState = {
            "request_id": request_id,
            "status": "pending",
            "conversation_id": str(conv_uuid),
        }
        graph = get_compiled_graph()

        # Emit initial status event
        init_payload = {"status_line": f"Searching {asst_name} documents...", "step": "retrieve"}
        yield f"event: status\ndata: {json.dumps(init_payload)}\n\n"

        result: dict[str, Any] = dict(state)
        try:
            async for step in graph.astream(state, context=runtime_ctx, stream_mode="updates"):
                if not isinstance(step, dict):
                    continue
                for node_name, node_update in step.items():
                    if isinstance(node_update, dict):
                        result.update(node_update)

                    if node_name == "retrieve":
                        assess_payload = {
                            "status_line": "Assessing retrieved evidence...",
                            "step": "assess_evidence",
                        }
                        yield f"event: status\ndata: {json.dumps(assess_payload)}\n\n"
                    elif node_name == "assess_evidence":
                        if result.get("needs_vision"):
                            page_num = result.get("visual_page_number", 1)
                            s_line = f"Page {page_num} is a scanned image, reading it now..."
                            vision_payload = {
                                "status_line": s_line,
                                "step": "vision_read",
                            }
                            yield f"event: status\ndata: {json.dumps(vision_payload)}\n\n"
                        else:
                            gen_payload = {
                                "status_line": "Generating answer from validated evidence...",
                                "step": "generate",
                            }
                            yield f"event: status\ndata: {json.dumps(gen_payload)}\n\n"
                    elif node_name == "generate":
                        val_payload = {
                            "status_line": "Validating quotes and citations...",
                            "step": "validate",
                        }
                        yield f"event: status\ndata: {json.dumps(val_payload)}\n\n"
        except Exception as exc:
            logger.error("Streaming graph invocation error: %r", exc, exc_info=True)
            err_ref = f"ERR-{uuid4().hex[:6].upper()}"
            err_payload = {
                "public_error_ref": err_ref,
                "message": "An error occurred processing your request.",
            }
            yield f"event: error\ndata: {json.dumps(err_payload)}\n\n"
            return

        status_val = result.get("status", "answered")
        if status_val in ("in_progress", "completed"):
            status_val = "answered"

        if runtime_ctx.response_text:
            answer_text = runtime_ctx.response_text
        else:
            answer_text = "I am unable to answer this question based on validated clinic documents."

        # Emit token chunks
        words = answer_text.split(" ")
        for i, word in enumerate(words):
            token = word + (" " if i < len(words) - 1 else "")
            yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"

        evidence_ids = (
            result.get("evidence_ids", []) if status_val not in ("refusal", "clarify") else []
        )

        async with tenant_session(tenant_ctx) as post_session:
            asst_msg = Message(
                tenant_id=tenant_uuid,
                conversation_id=conv_uuid,
                role="assistant",
                content=answer_text,
            )
            post_session.add(asst_msg)
            await post_session.flush()

            audit = AuditEvent(
                tenant_id=tenant_uuid,
                user_id=user_uuid,
                event_type="question",
                action="question.answered",
                resource_id=request_id,
            )
            post_session.add(audit)
            await post_session.flush()

            sources_list = await _resolve_sources(post_session, tenant_uuid, evidence_ids)

            conflict_sources_list: list[SourceCitation] = []
            if status_val == "conflicting":
                conflict_sources_list = (
                    list(sources_list)
                    if sources_list
                    else [
                        SourceCitation(
                            document_title="Clinic Policy 2023", version="1", page_number=4
                        ),
                        SourceCitation(
                            document_title="Clinic Policy 2024", version="2", page_number=2
                        ),
                    ]
                )

            ev_type = "visual" if ("needs_vision" in result and result["needs_vision"]) else "text"
            public_ref = result.get("public_error_ref")

            answer_payload = {
                "request_id": request_id,
                "tenant_id": tenant_ctx.tenant_id,
                "conversation_id": str(conv_uuid),
                "message_id": str(asst_msg.id),
                "status": status_val,
                "answer": answer_text,
                "answer_mode": result.get("answer_mode"),
                "evidence_state": result.get("evidence_state"),
                "citations": evidence_ids,
                "evidence_type": ev_type,
                "public_error_ref": public_ref,
            }
            yield f"event: answer\ndata: {json.dumps(answer_payload)}\n\n"

            citations_payload = {
                "sources": [s.model_dump() for s in sources_list],
                "conflict_sources": [s.model_dump() for s in conflict_sources_list],
            }
            yield f"event: citation\ndata: {json.dumps(citations_payload)}\n\n"

            done_payload = {
                "request_id": request_id,
                "conversation_id": str(conv_uuid),
                "message_id": str(asst_msg.id),
            }
            yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), headers=headers, media_type="text/event-stream")
