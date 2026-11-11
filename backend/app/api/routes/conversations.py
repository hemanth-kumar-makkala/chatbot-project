"""Conversation and message history management routes under tenant RLS (Task 3.6, Task 4.6)."""

import io
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.config import ApiSettings
from app.api.deps import (
    get_api_settings,
    get_auth_provider,
    get_current_session,
    get_tenant_context_for_assistant,
    validate_csrf,
)
from app.core.extraction.field_state import ExtractedField
from app.core.extraction.subgraph import build_extraction_subgraph
from app.core.graph import GraphPorts, GraphRuntimeContext, GraphState
from app.core.ingestion.validation import validate_file
from app.db.models import Conversation, ExtractionResult, Message, SessionDocument, Tenant
from app.db.session import tenant_session, unscoped_session
from app.ports.auth import AuthProvider
from app.ports.errors import AuthorizationError, FileValidationError, MalwareDetectedError
from app.ports.types import SessionContext, TenantContext
from app.wiring import (
    create_document_parser,
    create_image_processor,
    create_malware_scanner,
    create_model_provider,
    load_and_validate_profile_models,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["conversations"])


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


class ConversationResponse(BaseModel):
    id: str
    assistant_id: str
    title: str
    created_at: str
    updated_at: str
    last_message_preview: str
    message_count: int
    messages: list[MessageResponse] = Field(default_factory=list)


class DeleteConversationsResponse(BaseModel):
    ok: bool
    deleted_count: int


class DeleteSingleConversationResponse(BaseModel):
    ok: bool
    id: str


@router.get(
    "/assistants/{assistant_id}/conversations",
    response_model=list[ConversationResponse],
)
async def list_assistant_conversations(
    assistant_id: str,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
) -> list[ConversationResponse]:
    """List conversations for an assistant scoped to the user's authenticated tenant."""
    tenant_ctx = await get_tenant_context_for_assistant(
        assistant_id=assistant_id,
        session=session,
        auth_provider=auth_provider,
    )
    tenant_uuid = UUID(tenant_ctx.tenant_id)
    asst_uuid = UUID(assistant_id)

    async with tenant_session(tenant_ctx) as db_session:
        stmt = (
            select(Conversation)
            .where(
                Conversation.tenant_id == tenant_uuid,
                Conversation.assistant_id == asst_uuid,
            )
            .order_by(Conversation.created_at.desc())
        )
        conversations = (await db_session.execute(stmt)).scalars().all()

        results: list[ConversationResponse] = []
        for conv in conversations:
            msg_stmt = (
                select(Message)
                .where(
                    Message.tenant_id == tenant_uuid,
                    Message.conversation_id == conv.id,
                )
                .order_by(Message.created_at.asc())
            )
            msgs = (await db_session.execute(msg_stmt)).scalars().all()
            msg_responses = [
                MessageResponse(
                    id=str(m.id),
                    role=m.role,
                    content=m.content,
                    created_at=m.created_at.isoformat() if m.created_at else "",
                )
                for m in msgs
            ]
            last_preview = msgs[-1].content[:100] if msgs else ""
            created_str = conv.created_at.isoformat() if conv.created_at else ""

            results.append(
                ConversationResponse(
                    id=str(conv.id),
                    assistant_id=str(conv.assistant_id),
                    title=conv.title,
                    created_at=created_str,
                    updated_at=created_str,
                    last_message_preview=last_preview,
                    message_count=len(msgs),
                    messages=msg_responses,
                )
            )
        return results


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
async def get_conversation_messages(
    conversation_id: str,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
) -> list[MessageResponse]:
    """Retrieve full transcript messages for a conversation under tenant RLS."""
    try:
        conv_uuid = UUID(conversation_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND") from None

    async with unscoped_session() as db_session:
        tenant_ids = (await db_session.execute(select(Tenant.id))).scalars().all()

    found_tenant_ctx: TenantContext | None = None
    for tid in tenant_ids:
        roles = await auth_provider.roles(user_id=session.user_id, tenant_id=str(tid))
        if not roles:
            continue
        candidate_ctx = TenantContext(
            tenant_id=str(tid),
            user_id=session.user_id,
            role=roles[0],
            session_id=session.session_id,
        )
        async with tenant_session(candidate_ctx) as t_sess:
            c_exists = (
                await t_sess.execute(select(Conversation.id).where(Conversation.id == conv_uuid))
            ).scalar_one_or_none()
            if c_exists:
                found_tenant_ctx = candidate_ctx
                break

    if not found_tenant_ctx:
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")

    async with tenant_session(found_tenant_ctx) as db_session:
        msg_stmt = (
            select(Message)
            .where(
                Message.tenant_id == UUID(found_tenant_ctx.tenant_id),
                Message.conversation_id == conv_uuid,
            )
            .order_by(Message.created_at.asc())
        )
        msgs = (await db_session.execute(msg_stmt)).scalars().all()
        return [
            MessageResponse(
                id=str(m.id),
                role=m.role,
                content=m.content,
                created_at=m.created_at.isoformat() if m.created_at else "",
            )
            for m in msgs
        ]


@router.delete(
    "/assistants/{assistant_id}/conversations",
    response_model=DeleteConversationsResponse,
)
async def delete_assistant_conversations(
    assistant_id: str,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    _csrf: Annotated[None, Depends(validate_csrf)],
) -> DeleteConversationsResponse:
    """Delete all conversations for an assistant under tenant RLS."""
    tenant_ctx = await get_tenant_context_for_assistant(
        assistant_id=assistant_id,
        session=session,
        auth_provider=auth_provider,
    )
    tenant_uuid = UUID(tenant_ctx.tenant_id)
    asst_uuid = UUID(assistant_id)

    async with tenant_session(tenant_ctx) as db_session:
        del_stmt = (
            delete(Conversation)
            .where(
                Conversation.tenant_id == tenant_uuid,
                Conversation.assistant_id == asst_uuid,
            )
            .execution_options(synchronize_session=False)
        )
        res = await db_session.execute(del_stmt)
        await db_session.flush()
        count = int(getattr(res, "rowcount", 0) or 0)
        return DeleteConversationsResponse(ok=True, deleted_count=count)


@router.delete(
    "/conversations/{conversation_id}",
    response_model=DeleteSingleConversationResponse,
)
async def delete_conversation(
    conversation_id: str,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    _csrf: Annotated[None, Depends(validate_csrf)],
) -> DeleteSingleConversationResponse:
    """Delete a single conversation under tenant RLS."""
    try:
        conv_uuid = UUID(conversation_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND") from None

    async with unscoped_session() as db_session:
        tenant_ids = (await db_session.execute(select(Tenant.id))).scalars().all()

    found_tenant_ctx: TenantContext | None = None
    for tid in tenant_ids:
        roles = await auth_provider.roles(user_id=session.user_id, tenant_id=str(tid))
        if not roles:
            continue
        candidate_ctx = TenantContext(
            tenant_id=str(tid),
            user_id=session.user_id,
            role=roles[0],
            session_id=session.session_id,
        )
        async with tenant_session(candidate_ctx) as t_sess:
            c_exists = (
                await t_sess.execute(select(Conversation.id).where(Conversation.id == conv_uuid))
            ).scalar_one_or_none()
            if c_exists:
                found_tenant_ctx = candidate_ctx
                break

    if not found_tenant_ctx:
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")

    async with tenant_session(found_tenant_ctx) as db_session:
        del_stmt = (
            delete(Conversation)
            .where(
                Conversation.tenant_id == UUID(found_tenant_ctx.tenant_id),
                Conversation.id == conv_uuid,
            )
            .execution_options(synchronize_session=False)
        )
        res = await db_session.execute(del_stmt)
        await db_session.flush()
        del_count = int(getattr(res, "rowcount", 0) or 0)
        if del_count == 0:
            raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")
        return DeleteSingleConversationResponse(ok=True, id=conversation_id)


class SessionDocumentUploadResponse(BaseModel):
    session_document_id: str
    conversation_id: str
    file_name: str
    page_count: int
    status: str
    extracted_fields: list[dict[str, Any]]
    page_image_url: str


async def extract_fields_from_document_file(
    file_path: Path,
    file_name: str,
    page_number: int = 1,
    profile: str = "mock",
    chat_model: str | None = None,
    vision_model: str | None = None,
    tenant_ctx: TenantContext | None = None,
    *,
    nvidia_base_url: str | None = None,
    nvidia_api_key: str | None = None,
    inception_base_url: str | None = None,
    inception_api_key: str | None = None,
) -> list[ExtractedField]:
    """Run the real extraction subgraph using configured OCR and vision providers.

    On mock profile, mock providers supply deterministic output through the same subgraph,
    never through filename matching.
    Invariant 6: UNREADABLE fields receive state=UNREADABLE, value=None.
    """
    if tenant_ctx is None or not tenant_ctx.tenant_id:
        raise AuthorizationError(
            "Tenant context is required for document extraction",
            error_code="TENANT_CONTEXT_REQUIRED",
        )

    raw_bytes = file_path.read_bytes()

    profile_models = load_and_validate_profile_models(profile)
    caps_map = profile_models["_capabilities_map"]
    answer_cfg = profile_models["answer"]
    extract_cfg = profile_models["extract"]
    resolved_chat_model = chat_model or f"{answer_cfg['provider']}:{answer_cfg['model']}"
    resolved_vision_model = vision_model or f"{extract_cfg['provider']}:{extract_cfg['model']}"

    doc_parser = create_document_parser(profile)
    model_provider = create_model_provider(
        profile,
        chat_model=chat_model or None,
        vision_model=vision_model or None,
        nvidia_base_url=nvidia_base_url,
        nvidia_api_key=nvidia_api_key,
        inception_base_url=inception_base_url,
        inception_api_key=inception_api_key,
    )
    image_processor = create_image_processor(profile)

    ports = GraphPorts(
        capabilities_map=caps_map,
        model_provider=model_provider,
        document_parser=doc_parser,
        image_processor=image_processor,
        chat_model=resolved_chat_model,
        vision_model=resolved_vision_model,
        is_mock_profile=(profile == "mock"),
    )

    runtime_ctx = GraphRuntimeContext(
        ports=ports,
        tenant=tenant_ctx,
        evidence_text={
            "document_title": file_name,
            "page_number": str(page_number),
        },
        raw_document_bytes=raw_bytes,
        chat_model=resolved_chat_model,
    )

    state: GraphState = {
        "request_id": str(uuid4()),
        "tenant_id": runtime_ctx.tenant.tenant_id,
        "route": "extraction",
        "status": "in_progress",
    }

    subgraph = build_extraction_subgraph().compile()
    await subgraph.ainvoke(state, context=runtime_ctx)

    fields: list[ExtractedField] = runtime_ctx.extracted_fields.get("fields", [])
    return fields


async def _extract_upload_content_and_filename(request: Request) -> tuple[bytes, str]:
    """Extract uploaded file content and filename from multipart or raw body."""
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="EMPTY_FILE")

    if "multipart/form-data" in content_type:
        m_boundary = re.search(r"boundary=([^;]+)", content_type)
        if m_boundary:
            boundary = m_boundary.group(1).strip("\"'").encode()
            parts = body.split(b"--" + boundary)
            for part in parts:
                if b'name="file"' in part:
                    header_part, sep, file_data = part.partition(b"\r\n\r\n")
                    if sep:
                        if file_data.endswith(b"\r\n"):
                            file_data = file_data[:-2]
                        m_fn = re.search(
                            r'filename="([^"]+)"', header_part.decode("utf-8", errors="ignore")
                        )
                        fn = m_fn.group(1) if m_fn else "uploaded_fax.pdf"
                        return file_data, fn
        m_fn = re.search(r'filename="([^"]+)"', body[:500].decode("utf-8", errors="ignore"))
        fn = m_fn.group(1) if m_fn else "uploaded_fax.pdf"
        header_end = body.find(b"\r\n\r\n")
        file_data = body[header_end + 4 :] if header_end != -1 else body
        if file_data.endswith(b"\r\n"):
            file_data = file_data[:-2]
        return file_data, fn

    filename = (
        request.headers.get("X-Filename")
        or request.query_params.get("filename")
        or "uploaded_fax.pdf"
    )
    return body, filename


_NEW_CONVERSATION_SENTINELS = {
    "new",
    "00000000-0000-0000-0000-000000000000",
    "00000000-0000-0000-0000-000000000001",
}


@router.post(
    "/assistants/{assistant_id}/conversations/{conversation_id}/documents",
    response_model=SessionDocumentUploadResponse,
)
@router.post(
    "/conversations/{conversation_id}/documents",
    response_model=SessionDocumentUploadResponse,
)
async def upload_session_document(
    conversation_id: str,
    request: Request,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    assistant_id: str | None = None,
    _csrf: Annotated[None, Depends(validate_csrf)] = None,
) -> SessionDocumentUploadResponse:
    """Upload a session fax document, execute extraction subgraph, and return extracted fields."""
    normalized_conv_id = (conversation_id or "").strip().lower()
    is_new_sentinel = normalized_conv_id in _NEW_CONVERSATION_SENTINELS and assistant_id is not None

    if normalized_conv_id == "new" and is_new_sentinel:
        conv_uuid = uuid4()
    else:
        try:
            conv_uuid = UUID(conversation_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND") from None

    # Derive tenant context
    found_tenant_ctx: TenantContext | None = None
    if assistant_id:
        found_tenant_ctx = await get_tenant_context_for_assistant(
            assistant_id=assistant_id,
            session=session,
            auth_provider=auth_provider,
        )
    else:
        async with unscoped_session() as db_session:
            tenant_ids = (await db_session.execute(select(Tenant.id))).scalars().all()

        for tid in tenant_ids:
            roles = await auth_provider.roles(user_id=session.user_id, tenant_id=str(tid))
            if not roles:
                continue
            cand_ctx = TenantContext(
                tenant_id=str(tid),
                user_id=session.user_id,
                role=roles[0],
                session_id=session.session_id,
            )
            async with tenant_session(cand_ctx) as t_sess:
                c_exists = (
                    await t_sess.execute(
                        select(Conversation.id).where(Conversation.id == conv_uuid)
                    )
                ).scalar_one_or_none()
                if c_exists:
                    found_tenant_ctx = cand_ctx
                    break

    if not found_tenant_ctx:
        raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")

    # Verify conversation exists under tenant (or auto-create when attaching in a brand-new chat)
    async with tenant_session(found_tenant_ctx) as db_session:
        conv_stmt = select(Conversation).where(
            Conversation.tenant_id == UUID(found_tenant_ctx.tenant_id),
            Conversation.id == conv_uuid,
        )
        conv = (await db_session.execute(conv_stmt)).scalar_one_or_none()
        if not conv:
            if is_new_sentinel and assistant_id:
                conv_uuid = uuid4()
                conv = Conversation(
                    id=conv_uuid,
                    tenant_id=UUID(found_tenant_ctx.tenant_id),
                    user_id=UUID(found_tenant_ctx.user_id),
                    assistant_id=UUID(assistant_id),
                    title="Session Document Upload",
                )
                db_session.add(conv)
                await db_session.flush()
            else:
                raise HTTPException(status_code=404, detail="CONVERSATION_NOT_FOUND")

    content, filename = await _extract_upload_content_and_filename(request)
    if not content:
        raise HTTPException(status_code=400, detail="EMPTY_FILE")

    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="FILE_TOO_LARGE")

    profile = getattr(settings, "profile", "mock")
    scanner = create_malware_scanner(profile)
    try:
        validated = await validate_file(
            filename=filename,
            content=content,
            malware_scanner=scanner,
        )
    except FileValidationError as exc:
        if "size" in str(exc).lower():
            raise HTTPException(status_code=413, detail="FILE_TOO_LARGE") from exc
        raise HTTPException(status_code=400, detail="INVALID_FILE_TYPE") from exc
    except MalwareDetectedError as exc:
        raise HTTPException(status_code=400, detail="MALWARE_DETECTED") from exc

    if validated.extension not in ("pdf", "png", "jpeg", "jpg", "tiff", "tif"):
        raise HTTPException(status_code=400, detail="INVALID_FILE_TYPE")

    ext = validated.extension
    file_sha256 = validated.sha256
    doc_id = uuid4()

    # Save to disk under session storage
    storage_root = Path(settings.storage_dir if hasattr(settings, "storage_dir") else "storage")
    session_dir = storage_root / "sessions" / found_tenant_ctx.tenant_id / str(conv_uuid)
    session_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{doc_id}_{re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)}"
    saved_file_path = session_dir / safe_name
    saved_file_path.write_bytes(content)

    # Determine page count
    page_count = 1
    if ext == "pdf":
        try:
            import pypdfium2 as pdfium  # type: ignore[import-untyped]

            pdf_doc = pdfium.PdfDocument(str(saved_file_path))
            page_count = len(pdf_doc)
        except Exception:
            page_count = 1

    retention_hours = settings.session_upload_retention_hours or 24
    retention_until = datetime.now(UTC) + timedelta(hours=retention_hours)

    # Persist SessionDocument (Invariant 7: 0 chunks, isolated from knowledge index)
    async with tenant_session(found_tenant_ctx) as db_session:
        session_doc = SessionDocument(
            id=doc_id,
            tenant_id=UUID(found_tenant_ctx.tenant_id),
            conversation_id=conv_uuid,
            file_name=filename,
            file_path=str(saved_file_path),
            file_size_bytes=len(content),
            file_sha256=file_sha256,
            page_count=page_count,
            status="uploaded",
            retention_until=retention_until,
        )
        db_session.add(session_doc)
        await db_session.flush()

    # Execute extraction subgraph
    chat_model_name = settings.chat_model or None
    vision_model_name = settings.vision_model or None
    fields = await extract_fields_from_document_file(
        file_path=saved_file_path,
        file_name=filename,
        page_number=1,
        profile=profile,
        chat_model=chat_model_name,
        vision_model=vision_model_name,
        tenant_ctx=found_tenant_ctx,
        nvidia_base_url=settings.nvidia_base_url,
        nvidia_api_key=(
            settings.nvidia_api_key.get_secret_value() if settings.nvidia_api_key else None
        ),
        inception_base_url=settings.inception_base_url,
        inception_api_key=(
            settings.inception_api_key.get_secret_value() if settings.inception_api_key else None
        ),
    )

    # Persist ExtractionResult
    async with tenant_session(found_tenant_ctx) as db_session:
        ext_result = ExtractionResult(
            tenant_id=UUID(found_tenant_ctx.tenant_id),
            session_document_id=doc_id,
            schema_name="fax_intake",
            fields_data={"fields": [f.to_dict() for f in fields]},
        )
        db_session.add(ext_result)
        await db_session.flush()

    asst_param = assistant_id or str(conv.assistant_id)
    page_img_url = (
        f"/v1/assistants/{asst_param}/conversations/{conv_uuid}/documents/{doc_id}/pages/1/image"
    )

    return SessionDocumentUploadResponse(
        session_document_id=str(doc_id),
        conversation_id=str(conv_uuid),
        file_name=filename,
        page_count=page_count,
        status="extracted",
        extracted_fields=[f.to_dict() for f in fields],
        page_image_url=page_img_url,
    )


@router.get(
    "/assistants/{assistant_id}/conversations/{conversation_id}/documents/{document_id}/pages/{page_number}/image",
)
@router.get(
    "/conversations/{conversation_id}/documents/{document_id}/pages/{page_number}/image",
)
async def get_session_document_page_image(
    conversation_id: str,
    document_id: str,
    page_number: int,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    assistant_id: str | None = None,
) -> Response:
    """Stream PNG image for a session document page under tenant isolation."""
    try:
        conv_uuid = UUID(conversation_id)
        doc_uuid = UUID(document_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="DOCUMENT_NOT_FOUND") from None

    found_tenant_ctx: TenantContext | None = None
    if assistant_id:
        try:
            found_tenant_ctx = await get_tenant_context_for_assistant(
                assistant_id=assistant_id,
                session=session,
                auth_provider=auth_provider,
            )
        except HTTPException:
            found_tenant_ctx = None

    if not found_tenant_ctx:
        async with unscoped_session() as db_session:
            tenant_ids = (await db_session.execute(select(Tenant.id))).scalars().all()

        for tid in tenant_ids:
            roles = await auth_provider.roles(user_id=session.user_id, tenant_id=str(tid))
            if not roles:
                continue
            cand_ctx = TenantContext(
                tenant_id=str(tid),
                user_id=session.user_id,
                role=roles[0],
                session_id=session.session_id,
            )
            async with tenant_session(cand_ctx) as t_sess:
                s_exists = (
                    await t_sess.execute(
                        select(SessionDocument.id).where(
                            SessionDocument.id == doc_uuid,
                            SessionDocument.conversation_id == conv_uuid,
                        )
                    )
                ).scalar_one_or_none()
                if s_exists:
                    found_tenant_ctx = cand_ctx
                    break

    if not found_tenant_ctx:
        raise HTTPException(status_code=404, detail="DOCUMENT_NOT_FOUND")

    async with tenant_session(found_tenant_ctx) as db_session:
        doc_stmt = select(SessionDocument).where(
            SessionDocument.tenant_id == UUID(found_tenant_ctx.tenant_id),
            SessionDocument.conversation_id == conv_uuid,
            SessionDocument.id == doc_uuid,
        )
        session_doc = (await db_session.execute(doc_stmt)).scalar_one_or_none()
        if not session_doc:
            raise HTTPException(status_code=404, detail="DOCUMENT_NOT_FOUND")

    file_path = Path(session_doc.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")

    if file_path.suffix.lower() == ".pdf":
        try:
            import pypdfium2 as pdfium

            pdf_doc = pdfium.PdfDocument(str(file_path))
            if page_number < 1 or page_number > len(pdf_doc):
                raise HTTPException(status_code=404, detail="PAGE_NOT_FOUND")
            page = pdf_doc.get_page(page_number - 1)
            pil_img = page.render(scale=2).to_pil()
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail="IMAGE_RENDER_FAILED") from exc
    else:
        media_type = "image/png" if file_path.suffix.lower() == ".png" else "image/jpeg"
        return Response(content=file_path.read_bytes(), media_type=media_type)
