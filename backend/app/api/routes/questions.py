"""Question and answer dispatch routes."""

import contextlib
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
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
from app.core.safety_lock import (
    CloudModelSafetyLockError,
    is_cloud_model,
    verify_cloud_model_safety_lock,
)
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
from app.observability.logger import scrub_exception_for_log
from app.ports.auth import AuthProvider
from app.ports.errors import ConfigurationError, UnresolvableEvidenceError
from app.ports.model_provider import ModelCapabilities
from app.ports.storage import StorageKey
from app.ports.types import SessionContext
from app.wiring import (
    build_app_graph,
    create_mock_ports,
    create_model_provider,
    create_production_ports,
    create_reranker,
    create_retriever,
    create_storage_backend,
    load_and_validate_profile_models,
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


async def _hash_active_stored_documents(
    settings: ApiSettings,
    tenant_uuid: UUID,
    rows: Sequence[Any],
    model_name: str,
) -> list[str]:
    """Hash original bytes from tenant storage; refuse cloud use if any cannot be verified."""
    storage = create_storage_backend(profile=settings.profile, base_dir=settings.storage_dir)
    hashes: list[str] = []
    for title, version_id, extension in rows:
        if not extension:
            raise CloudModelSafetyLockError(str(title), model_name)
        key = StorageKey(
            tenant_id=tenant_uuid,
            path=f"documents/{version_id}/original.{extension}",
        )
        try:
            file_bytes = await storage.get(key)
        except Exception as exc:
            raise CloudModelSafetyLockError(str(title), model_name) from exc
        hashes.append(hashlib.sha256(file_bytes).hexdigest())
    return hashes


async def _verify_active_documents_for_cloud_models(
    settings: ApiSettings,
    tenant_uuid: UUID,
    rows: Sequence[Any],
) -> list[str]:
    """Hash active files once and validate every selected cloud role model."""
    ports = get_engine_ports(settings)
    caps_map: Mapping[tuple[str, str], ModelCapabilities] = ports.capabilities_map
    explicit_selectors = [
        m
        for m in (settings.chat_model, settings.vision_model, ports.chat_model, ports.vision_model)
        if m
    ]
    if not caps_map:
        if settings.profile != "mock" or explicit_selectors:
            raise ConfigurationError(
                message="Model capabilities map is not declared or missing in GraphPorts",
                error_code="MODEL_CAPABILITIES_NOT_DECLARED",
            )
        return []

    role_selectors: list[str] = []
    if settings.profile != "mock":
        profile_models = load_and_validate_profile_models(settings.profile)
        for role_name in ("router", "answer", "vision", "extract", "judge"):
            role_cfg = profile_models.get(role_name)
            if isinstance(role_cfg, dict) and role_cfg.get("provider") and role_cfg.get("model"):
                role_selectors.append(f"{role_cfg['provider']}:{role_cfg['model']}")

    caps_selectors = [f"{prov}:{mod}" for prov, mod in caps_map]
    models_to_check = list(dict.fromkeys([*explicit_selectors, *role_selectors, *caps_selectors]))
    if not models_to_check:
        return []

    cloud_models: list[str] = []
    for m in models_to_check:
        if is_cloud_model(m, capabilities_map=caps_map):
            cloud_models.append(m)
    if not cloud_models:
        return []
    hashes = await _hash_active_stored_documents(settings, tenant_uuid, rows, cloud_models[0])
    titles = [str(row[0]) for row in rows]
    for model_name in cloud_models:
        verify_cloud_model_safety_lock(
            model_name=model_name,
            active_document_titles=titles,
            active_document_hashes=hashes,
            capabilities_map=caps_map,
        )
    return hashes


def get_engine_ports(settings: ApiSettings) -> GraphPorts:
    """Retrieve or construct profile-scoped GraphPorts instance."""
    if settings.profile == "mock" and settings.profile in _ports_cache:
        return _ports_cache[settings.profile]
    cache_key = f"{settings.profile}:{settings.chat_model}:{settings.vision_model}"
    if cache_key in _ports_cache:
        return _ports_cache[cache_key]

    profile = settings.profile
    if profile == "mock":
        profile_models = load_and_validate_profile_models("mock")
        ports = create_mock_ports(capabilities_map=profile_models["_capabilities_map"])
    else:
        profile_models = load_and_validate_profile_models(profile)
        caps_map = profile_models["_capabilities_map"]
        resolved_chat_model = (
            settings.chat_model
            or f"{profile_models['answer']['provider']}:{profile_models['answer']['model']}"
        )
        resolved_vision_model = (
            settings.vision_model
            or f"{profile_models['vision']['provider']}:{profile_models['vision']['model']}"
        )
        model_provider = create_model_provider(
            profile,
            chat_model=settings.chat_model or None,
            vision_model=settings.vision_model or None,
            gemini_base_url=settings.gemini_base_url,
            gemini_api_key=(
                settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
            ),
        )
        retriever = create_retriever(profile)
        reranker = None
        if profile in ("desktop-gpu", "home-gpu"):
            reranker = create_reranker(profile)
        ports = create_production_ports(
            model_provider=model_provider,
            retriever=retriever,
            reranker=reranker,
            chat_model=resolved_chat_model,
            vision_model=resolved_vision_model,
            capabilities_map=caps_map,
        )

    _ports_cache[cache_key] = ports
    return ports


class ConversationTurn(BaseModel):
    role: str
    content: str


class QuestionRequest(BaseModel):
    assistant_id: str
    question_text: str
    conversation_id: UUID | None = None
    history: list[ConversationTurn] | None = None
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
    page_number: int | None = None
    version_id: str | None = None
    evidence_type: str = "text"
    sheet_name: str | None = None
    row_number: int | None = None


class QuestionResponse(BaseModel):
    request_id: str
    tenant_id: str
    conversation_id: str
    message_id: str
    status: str
    route: str | None = None
    answer: str
    answer_mode: str | None = None
    evidence_state: str | None = None
    citations: list[str] = []
    evidence_type: str = "text"
    sources: list[SourceCitation] = []
    conflict_sources: list[SourceCitation] = []
    public_error_ref: str | None = None
    rewritten_question: str | None = None


def _should_include_sources(status_val: str, route_val: str | None) -> bool:
    """Return True only when responses may display document source citations."""
    return status_val not in ("refusal", "clarify") and route_val != "general"


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

    chunk_rows: list[Any] = []
    table_rows: list[Any] = []
    found_chunk_ids: set[UUID] = set()

    if chunk_uuids:
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
        chunk_rows = list((await db_session.execute(chunk_stmt)).all())
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
            table_rows = list((await db_session.execute(table_stmt)).all())
            for trow in table_rows:
                sources_list.append(
                    SourceCitation(
                        document_title=trow.document_title,
                        version="1",
                        page_number=None,
                        version_id=str(trow.document_version_id),
                        evidence_type="table",
                        sheet_name=trow.sheet_name,
                        row_number=trow.row_index,
                    )
                )

    # Fail closed: never synthesize citations for unresolvable IDs
    resolved_str_ids = {str(crow.id) for crow in chunk_rows} | {str(trow.id) for trow in table_rows}
    unresolved_ids = [str(eid) for eid in evidence_ids if str(eid) not in resolved_str_ids]
    if unresolved_ids:
        raise UnresolvableEvidenceError(
            f"Evidence IDs {unresolved_ids} cannot be resolved to active chunks "
            f"for tenant {tenant_uuid}",
            error_code="UNRESOLVABLE_EVIDENCE_ID",
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

        # 1. Resolve or create conversation and load scoped conversation history
        history_turns: list[dict[str, str]] = []
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

            # Load history turns (last 4 turns = 8 messages) scoped strictly to tenant and session
            prev_msg_stmt = (
                select(Message.role, Message.content)
                .where(
                    Message.tenant_id == tenant_uuid,
                    Message.conversation_id == conv_uuid,
                )
                .order_by(Message.created_at.desc())
                .limit(8)
            )
            prev_msgs = (await db_session.execute(prev_msg_stmt)).all()
            history_turns = [{"role": r.role, "content": r.content} for r in reversed(prev_msgs)]
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

        if not history_turns and payload.history:
            history_turns = [{"role": t.role, "content": t.content} for t in payload.history[-8:]]

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
        active_docs_stmt = (
            select(Document.title, DocumentVersion.id, DocumentVersion.file_extension)
            .join(DocumentVersion, Document.active_version_id == DocumentVersion.id)
            .where(
                Document.tenant_id == tenant_uuid,
                Document.is_retired.is_(False),
                Document.active_version_id.is_not(None),
                DocumentVersion.tenant_id == tenant_uuid,
            )
        )
        active_doc_rows = (await db_session.execute(active_docs_stmt)).all()
        active_doc_titles = [row[0] for row in active_doc_rows]
        active_doc_hashes: list[str] = []

        # Safety lock validates every selected cloud answer and vision model.
        try:
            active_doc_hashes = await _verify_active_documents_for_cloud_models(
                settings, tenant_uuid, active_doc_rows
            )
        except CloudModelSafetyLockError as lock_err:
            raise HTTPException(status_code=403, detail=str(lock_err)) from lock_err
        except ConfigurationError as cfg_err:
            raise HTTPException(status_code=500, detail=str(cfg_err)) from cfg_err

        # 4. Load visual page metadata inside the pre-graph DB transaction
        page_images: dict[str, bytes] = {}
        page_ocr_confidences: dict[str, float] = {}
        visual_stmt = (
            select(Chunk.id, Page.image_key, Page.ocr_confidence)
            .join(Page, (Page.id == Chunk.page_id) & (Page.tenant_id == Chunk.tenant_id))
            .where(
                Chunk.tenant_id == tenant_uuid,
                Chunk.is_active.is_(True),
                Chunk.evidence_type == "visual",
            )
        )
        visual_rows = [
            (str(vrow.id), vrow.image_key, vrow.ocr_confidence)
            for vrow in (await db_session.execute(visual_stmt)).all()
        ]

    # Release DB connection before invoking model/GPU pipeline (matching ask_question_stream)
    ports = get_engine_ports(settings=settings)
    if visual_rows:
        storage = create_storage_backend(profile=settings.profile, base_dir=settings.storage_dir)
        for cid_str, image_key, ocr_conf in visual_rows:
            if ocr_conf is not None:
                page_ocr_confidences[cid_str] = float(ocr_conf)
            if image_key:
                rel_path = str(image_key)
                prefix = f"{tenant_ctx.tenant_id}/"
                if rel_path.startswith(prefix):
                    rel_path = rel_path[len(prefix) :]
                with contextlib.suppress(Exception):
                    page_images[cid_str] = await storage.get(
                        StorageKey(tenant_id=tenant_uuid, path=rel_path)
                    )

    runtime_ctx = GraphRuntimeContext(
        ports=ports,
        tenant=tenant_ctx,
        question_text=payload.question_text,
        conversation_history=history_turns,
        active_document_count=doc_count,
        active_document_titles=active_doc_titles,
        active_document_hashes=active_doc_hashes,
        chat_model=ports.chat_model or settings.chat_model,
        page_images=page_images,
        page_ocr_confidences=page_ocr_confidences,
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
        scrubbed_exc = scrub_exception_for_log(exc)
        logger.error(
            "Graph invocation error: %s",
            scrubbed_exc.scrubbed_message,
            extra={
                "node": "ask_question",
                "status": "error",
                "error_code": "ERR_GRAPH_INVOCATION",
                "error_type": scrubbed_exc.error_type,
            },
        )
        err_ref = f"ERR-{uuid4().hex[:6].upper()}"
        result = {
            "request_id": request_id,
            "status": "safe_failure",
            "public_error_ref": err_ref,
        }
        runtime_ctx.response_text = (
            "I encountered an issue processing your request. Please try again."
        )

    # 5. Save assistant answer message row and audit event in a short post-graph DB transaction
    if runtime_ctx.response_text:
        answer_text = runtime_ctx.response_text
    else:
        answer_text = "I am unable to answer this question based on validated clinic documents."

    async with tenant_session(tenant_ctx) as db_session:
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

        route_val: str | None = result.get("route")
        ev_type = "visual" if ("needs_vision" in result and result["needs_vision"]) else "text"
        evidence_ids: list[str] = []
        if "evidence_ids" in result and _should_include_sources(status_val, route_val):
            evidence_ids = result["evidence_ids"]

        # 7. Resolve real source chips from database (Rule E.4)
        sources_list: list[SourceCitation] = []
        if evidence_ids:
            try:
                sources_list = await _resolve_sources(db_session, tenant_uuid, evidence_ids)
            except UnresolvableEvidenceError:
                logger.error(
                    "Evidence ID could not be resolved to tenant chunk",
                    extra={
                        "request_id": request_id,
                        "tenant_id": tenant_ctx.tenant_id,
                        "node": "resolve_sources",
                        "status": "safe_failure",
                        "error_code": "UNRESOLVABLE_EVIDENCE_ID",
                    },
                )
                err_ref = f"ERR-{uuid4().hex[:6].upper()}"
                status_val = "safe_failure"
                answer_text = (
                    "An error occurred while evaluating evidence. "
                    f"Please quote reference {err_ref}."
                )
                result["public_error_ref"] = err_ref
                evidence_ids = []
                sources_list = []

        conflict_sources_list: list[SourceCitation] = []
        if status_val == "conflicting":
            conflict_sources_list = list(sources_list)

        public_ref: str | None = None
        if "public_error_ref" in result:
            public_ref = result["public_error_ref"]

        return QuestionResponse(
            request_id=request_id,
            tenant_id=tenant_ctx.tenant_id,
            conversation_id=str(conv_uuid),
            message_id=str(asst_msg.id),
            status=status_val,
            route=route_val,
            answer=answer_text,
            answer_mode=result.get("answer_mode"),
            evidence_state=result.get("evidence_state"),
            citations=evidence_ids,
            evidence_type=ev_type,
            sources=sources_list,
            conflict_sources=conflict_sources_list,
            public_error_ref=public_ref,
            rewritten_question=result.get("rewritten_question") or runtime_ctx.rewritten_question,
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

        history_turns: list[dict[str, str]] = []
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

            prev_msg_stmt = (
                select(Message.role, Message.content)
                .where(
                    Message.tenant_id == tenant_uuid,
                    Message.conversation_id == conv_uuid,
                )
                .order_by(Message.created_at.desc())
                .limit(8)
            )
            prev_msgs = (await db_session.execute(prev_msg_stmt)).all()
            history_turns = [{"role": r.role, "content": r.content} for r in reversed(prev_msgs)]
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

        if not history_turns and payload.history:
            history_turns = [{"role": t.role, "content": t.content} for t in payload.history[-8:]]

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
        active_docs_stmt = (
            select(Document.title, DocumentVersion.id, DocumentVersion.file_extension)
            .join(DocumentVersion, Document.active_version_id == DocumentVersion.id)
            .where(
                Document.tenant_id == tenant_uuid,
                Document.is_retired.is_(False),
                Document.active_version_id.is_not(None),
                DocumentVersion.tenant_id == tenant_uuid,
            )
        )
        active_doc_rows = (await db_session.execute(active_docs_stmt)).all()
        active_doc_titles = [row[0] for row in active_doc_rows]
        active_doc_hashes: list[str] = []

        # Safety lock validates every selected cloud answer and vision model.
        try:
            active_doc_hashes = await _verify_active_documents_for_cloud_models(
                settings, tenant_uuid, active_doc_rows
            )
        except CloudModelSafetyLockError as lock_err:
            raise HTTPException(status_code=403, detail=str(lock_err)) from lock_err
        except ConfigurationError as cfg_err:
            raise HTTPException(status_code=500, detail=str(cfg_err)) from cfg_err

        visual_stmt = (
            select(Chunk.id, Page.image_key, Page.ocr_confidence)
            .join(Page, (Page.id == Chunk.page_id) & (Page.tenant_id == Chunk.tenant_id))
            .where(
                Chunk.tenant_id == tenant_uuid,
                Chunk.is_active.is_(True),
                Chunk.evidence_type == "visual",
            )
        )
        visual_rows = [
            (str(vrow.id), vrow.image_key, vrow.ocr_confidence)
            for vrow in (await db_session.execute(visual_stmt)).all()
        ]

    async def event_generator() -> AsyncIterator[str]:
        settings = get_api_settings(request)
        ports = get_engine_ports(settings=settings)
        page_images: dict[str, bytes] = {}
        page_ocr_confidences: dict[str, float] = {}
        if visual_rows:
            storage = create_storage_backend(
                profile=settings.profile, base_dir=settings.storage_dir
            )
            for cid_str, image_key, ocr_conf in visual_rows:
                if ocr_conf is not None:
                    page_ocr_confidences[cid_str] = float(ocr_conf)
                if image_key:
                    rel_path = str(image_key)
                    prefix = f"{tenant_ctx.tenant_id}/"
                    if rel_path.startswith(prefix):
                        rel_path = rel_path[len(prefix) :]
                    with contextlib.suppress(Exception):
                        page_images[cid_str] = await storage.get(
                            StorageKey(tenant_id=tenant_uuid, path=rel_path)
                        )

        runtime_ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text=payload.question_text,
            conversation_history=history_turns,
            active_document_count=doc_count,
            active_document_titles=active_doc_titles,
            active_document_hashes=active_doc_hashes,
            chat_model=ports.chat_model or settings.chat_model,
            page_images=page_images,
            page_ocr_confidences=page_ocr_confidences,
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
            scrubbed_exc = scrub_exception_for_log(exc)
            logger.error(
                "Streaming graph invocation error: %s",
                scrubbed_exc.scrubbed_message,
                extra={
                    "node": "ask_question_stream",
                    "status": "error",
                    "error_code": "ERR_STREAM_GRAPH_INVOCATION",
                    "error_type": scrubbed_exc.error_type,
                },
            )
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

        route_val: str | None = result.get("route")
        evidence_ids = (
            result.get("evidence_ids", []) if _should_include_sources(status_val, route_val) else []
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

            sources_list: list[SourceCitation] = []
            try:
                sources_list = await _resolve_sources(post_session, tenant_uuid, evidence_ids)
            except UnresolvableEvidenceError:
                logger.error(
                    "Evidence ID could not be resolved to tenant chunk",
                    extra={
                        "request_id": request_id,
                        "tenant_id": tenant_ctx.tenant_id,
                        "node": "resolve_sources",
                        "status": "safe_failure",
                        "error_code": "UNRESOLVABLE_EVIDENCE_ID",
                    },
                )
                err_ref = f"ERR-{uuid4().hex[:6].upper()}"
                status_val = "safe_failure"
                answer_text = (
                    "An error occurred while evaluating evidence. "
                    f"Please quote reference {err_ref}."
                )
                result["public_error_ref"] = err_ref
                evidence_ids = []
                sources_list = []

            conflict_sources_list: list[SourceCitation] = []
            if status_val == "conflicting":
                conflict_sources_list = list(sources_list)

            ev_type = "visual" if ("needs_vision" in result and result["needs_vision"]) else "text"
            public_ref = result.get("public_error_ref")

            answer_payload = {
                "request_id": request_id,
                "tenant_id": tenant_ctx.tenant_id,
                "conversation_id": str(conv_uuid),
                "message_id": str(asst_msg.id),
                "status": status_val,
                "route": route_val,
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
