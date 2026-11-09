"""API routes for assistant discovery, CRUD, and configuration (Stage 3.3)."""

from __future__ import annotations

import json
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.config import ApiSettings
from app.api.deps import (
    get_api_settings,
    get_auth_provider,
    get_client_admin_tenant_context_for_assistant,
    get_current_session,
    get_tenant_context_for_assistant,
    validate_csrf,
)
from app.db.models import Assistant, Document, Membership
from app.db.session import tenant_session, unscoped_session
from app.ports.auth import AuthProvider
from app.ports.storage import StorageBackend
from app.ports.types import SessionContext, StorageKey, TenantContext, UserRole
from app.wiring import create_storage_backend

router = APIRouter(tags=["assistants"])

DEFAULT_INSTRUCTIONS = (
    "You are a clinical staff knowledge assistant. Respond concisely and professionally "
    "using bullet points when helpful. Use plain medical terminology suitable for "
    "clinical coordinators."
)
DEFAULT_REFUSAL_MESSAGE = "I can only answer questions about this client's documents."


class AssistantSummary(BaseModel):
    id: str
    tenant_id: str
    name: str
    prompt_id: str
    prompt_version: str
    created_at: str
    document_count: int = 0


class AssistantCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    prompt_id: str = "generator"
    prompt_version: str = "1.0.0"


class AssistantUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    prompt_id: str | None = None
    prompt_version: str | None = None


class AssistantInstructionsResponse(BaseModel):
    instructions: str


class AssistantInstructionsRequest(BaseModel):
    instructions: str = Field(..., min_length=1, max_length=4000)


class AssistantGuardrailsResponse(BaseModel):
    refusal_message: str
    blocked_topics: list[str] = []


class AssistantGuardrailsRequest(BaseModel):
    refusal_message: str | None = Field(None, min_length=1, max_length=1000)
    blocked_topics: list[str] = []


def _get_storage(settings: ApiSettings) -> StorageBackend:
    return create_storage_backend(settings.profile, settings.storage_dir)


@router.get(
    "/v1/assistants",
    response_model=list[AssistantSummary],
    status_code=status.HTTP_200_OK,
)
async def list_assistants(
    session: Annotated[SessionContext, Depends(get_current_session)],
) -> list[AssistantSummary]:
    """Retrieve all assistants accessible to the caller's tenant."""
    async with unscoped_session() as db:
        tenant_uuid: UUID | None = None
        if session.tenant_id:
            try:
                tenant_uuid = UUID(session.tenant_id)
            except (ValueError, TypeError):
                tenant_uuid = None

        if tenant_uuid is None:
            uid = UUID(session.user_id)
            stmt_m = select(Membership.tenant_id).where(Membership.user_id == uid).limit(1)
            res_m = await db.execute(stmt_m)
            tenant_uuid = res_m.scalar_one_or_none()

        if tenant_uuid is None:
            return []

        doc_count_subq = (
            select(func.count(Document.id))
            .where(
                Document.tenant_id == tenant_uuid,
                Document.is_retired.is_(False),
            )
            .scalar_subquery()
        )

        stmt = (
            select(Assistant, doc_count_subq.label("doc_count"))
            .where(Assistant.tenant_id == tenant_uuid)
            .order_by(Assistant.created_at.asc())
        )
        result = await db.execute(stmt)
        rows = result.all()

    assistants: list[AssistantSummary] = []
    for row in rows:
        asst = cast(Assistant, row[0])
        count = cast(int | None, row[1])
        created_str = (
            asst.created_at.isoformat()
            if hasattr(asst.created_at, "isoformat")
            else str(asst.created_at)
        )
        assistants.append(
            AssistantSummary(
                id=str(asst.id),
                tenant_id=str(asst.tenant_id),
                name=asst.name,
                prompt_id=asst.prompt_id,
                prompt_version=asst.prompt_version,
                created_at=created_str,
                document_count=count or 0,
            )
        )
    return assistants


@router.post(
    "/v1/assistants",
    response_model=AssistantSummary,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_csrf)],
)
async def create_assistant(
    req: AssistantCreateRequest,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
) -> AssistantSummary:
    """Create a new assistant under the client admin's tenant."""
    tenant_uuid: UUID | None = None
    if session.tenant_id:
        try:
            tenant_uuid = UUID(session.tenant_id)
        except (ValueError, TypeError):
            tenant_uuid = None

    if tenant_uuid is None:
        async with unscoped_session() as db:
            uid = UUID(session.user_id)
            stmt_m = select(Membership.tenant_id).where(Membership.user_id == uid).limit(1)
            res_m = await db.execute(stmt_m)
            tenant_uuid = res_m.scalar_one_or_none()

    if tenant_uuid is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active tenant found for user",
        )

    roles = await auth_provider.roles(session.user_id, str(tenant_uuid))
    if UserRole.CLIENT_ADMIN not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client admin privileges required",
        )

    asst_id = uuid4()
    ctx = TenantContext(
        tenant_id=str(tenant_uuid),
        user_id=session.user_id,
        role=UserRole.CLIENT_ADMIN,
        session_id=session.session_id,
    )
    async with tenant_session(ctx) as db:
        asst = Assistant(
            id=asst_id,
            tenant_id=tenant_uuid,
            name=req.name.strip(),
            prompt_id=req.prompt_id,
            prompt_version=req.prompt_version,
        )
        db.add(asst)
        await db.commit()

    return AssistantSummary(
        id=str(asst_id),
        tenant_id=str(tenant_uuid),
        name=req.name.strip(),
        prompt_id=req.prompt_id,
        prompt_version=req.prompt_version,
        created_at="now",
        document_count=0,
    )


@router.get(
    "/v1/assistants/{assistant_id}",
    response_model=AssistantSummary,
    status_code=status.HTTP_200_OK,
)
async def get_assistant(
    assistant_id: str,
    ctx: Annotated[TenantContext, Depends(get_tenant_context_for_assistant)],
) -> AssistantSummary:
    """Retrieve assistant metadata under tenant authorization."""
    asst_uuid = UUID(assistant_id)
    tenant_uuid = UUID(ctx.tenant_id)

    async with tenant_session(ctx) as db:
        doc_count_subq = (
            select(func.count(Document.id))
            .where(
                Document.tenant_id == tenant_uuid,
                Document.is_retired.is_(False),
            )
            .scalar_subquery()
        )
        stmt = (
            select(Assistant, doc_count_subq.label("doc_count"))
            .where(Assistant.id == asst_uuid, Assistant.tenant_id == tenant_uuid)
            .limit(1)
        )
        res = await db.execute(stmt)
        row = res.first()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant not found",
            )
        asst = cast(Assistant, row[0])
        count = cast(int | None, row[1])
        created_str = (
            asst.created_at.isoformat()
            if hasattr(asst.created_at, "isoformat")
            else str(asst.created_at)
        )
        return AssistantSummary(
            id=str(asst.id),
            tenant_id=str(asst.tenant_id),
            name=asst.name,
            prompt_id=asst.prompt_id,
            prompt_version=asst.prompt_version,
            created_at=created_str,
            document_count=count or 0,
        )


@router.patch(
    "/v1/assistants/{assistant_id}",
    response_model=AssistantSummary,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(validate_csrf)],
)
async def update_assistant(
    assistant_id: str,
    req: AssistantUpdateRequest,
    ctx: Annotated[TenantContext, Depends(get_client_admin_tenant_context_for_assistant)],
) -> AssistantSummary:
    """Update assistant details (client admin only)."""
    asst_uuid = UUID(assistant_id)
    tenant_uuid = UUID(ctx.tenant_id)

    async with tenant_session(ctx) as db:
        stmt = select(Assistant).where(
            Assistant.id == asst_uuid,
            Assistant.tenant_id == tenant_uuid,
        )
        res = await db.execute(stmt)
        asst = res.scalar_one_or_none()
        if not asst:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant not found",
            )

        if req.name is not None and req.name.strip():
            asst.name = req.name.strip()
        if req.prompt_id is not None:
            asst.prompt_id = req.prompt_id
        if req.prompt_version is not None:
            asst.prompt_version = req.prompt_version

        await db.commit()

        created_str = (
            asst.created_at.isoformat()
            if hasattr(asst.created_at, "isoformat")
            else str(asst.created_at)
        )
        return AssistantSummary(
            id=str(asst.id),
            tenant_id=str(asst.tenant_id),
            name=asst.name,
            prompt_id=asst.prompt_id,
            prompt_version=asst.prompt_version,
            created_at=created_str,
            document_count=0,
        )


@router.delete(
    "/v1/assistants/{assistant_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(validate_csrf)],
)
async def delete_assistant(
    assistant_id: str,
    ctx: Annotated[TenantContext, Depends(get_client_admin_tenant_context_for_assistant)],
) -> dict[str, str]:
    """Delete an assistant (client admin only)."""
    asst_uuid = UUID(assistant_id)
    tenant_uuid = UUID(ctx.tenant_id)

    async with tenant_session(ctx) as db:
        stmt = select(Assistant).where(
            Assistant.id == asst_uuid,
            Assistant.tenant_id == tenant_uuid,
        )
        res = await db.execute(stmt)
        asst = res.scalar_one_or_none()
        if not asst:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant not found",
            )
        await db.delete(asst)
        await db.commit()

    return {"status": "deleted", "assistant_id": assistant_id}


@router.get(
    "/v1/assistants/{assistant_id}/instructions",
    response_model=AssistantInstructionsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_assistant_instructions(
    assistant_id: str,
    ctx: Annotated[TenantContext, Depends(get_tenant_context_for_assistant)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> AssistantInstructionsResponse:
    """Retrieve assistant custom instructions."""
    storage = _get_storage(settings)
    key = StorageKey(
        tenant_id=UUID(ctx.tenant_id),
        path=f"assistants/{assistant_id}/instructions.json",
    )
    try:
        raw_data = await storage.get(key)
        if raw_data:
            parsed = json.loads(raw_data.decode("utf-8"))
            return AssistantInstructionsResponse(
                instructions=parsed.get("instructions", DEFAULT_INSTRUCTIONS)
            )
    except Exception:
        pass
    return AssistantInstructionsResponse(instructions=DEFAULT_INSTRUCTIONS)


@router.patch(
    "/v1/assistants/{assistant_id}/instructions",
    response_model=AssistantInstructionsResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(validate_csrf)],
)
async def update_assistant_instructions(
    assistant_id: str,
    req: AssistantInstructionsRequest,
    ctx: Annotated[TenantContext, Depends(get_client_admin_tenant_context_for_assistant)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> AssistantInstructionsResponse:
    """Save assistant custom instructions (client admin only)."""
    storage = _get_storage(settings)
    key = StorageKey(
        tenant_id=UUID(ctx.tenant_id),
        path=f"assistants/{assistant_id}/instructions.json",
    )
    payload = json.dumps({"instructions": req.instructions}).encode("utf-8")
    await storage.put(key, payload)
    return AssistantInstructionsResponse(instructions=req.instructions)


@router.get(
    "/v1/assistants/{assistant_id}/guardrails",
    response_model=AssistantGuardrailsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_assistant_guardrails(
    assistant_id: str,
    ctx: Annotated[TenantContext, Depends(get_tenant_context_for_assistant)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> AssistantGuardrailsResponse:
    """Retrieve assistant guardrails configuration."""
    storage = _get_storage(settings)
    key = StorageKey(
        tenant_id=UUID(ctx.tenant_id),
        path=f"assistants/{assistant_id}/guardrails.json",
    )
    try:
        raw_data = await storage.get(key)
        if raw_data:
            parsed = json.loads(raw_data.decode("utf-8"))
            return AssistantGuardrailsResponse(
                refusal_message=parsed.get("refusal_message", DEFAULT_REFUSAL_MESSAGE),
                blocked_topics=parsed.get("blocked_topics", []),
            )
    except Exception:
        pass
    return AssistantGuardrailsResponse(
        refusal_message=DEFAULT_REFUSAL_MESSAGE,
        blocked_topics=[],
    )


@router.patch(
    "/v1/assistants/{assistant_id}/guardrails",
    response_model=AssistantGuardrailsResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(validate_csrf)],
)
async def update_assistant_guardrails(
    assistant_id: str,
    req: AssistantGuardrailsRequest,
    ctx: Annotated[TenantContext, Depends(get_client_admin_tenant_context_for_assistant)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> AssistantGuardrailsResponse:
    """Save assistant guardrails configuration (client admin only)."""
    storage = _get_storage(settings)
    key = StorageKey(
        tenant_id=UUID(ctx.tenant_id),
        path=f"assistants/{assistant_id}/guardrails.json",
    )
    refusal = (
        req.refusal_message.strip()
        if req.refusal_message is not None and req.refusal_message.strip()
        else DEFAULT_REFUSAL_MESSAGE
    )
    topics = req.blocked_topics or []
    payload = json.dumps(
        {
            "refusal_message": refusal,
            "blocked_topics": topics,
        }
    ).encode("utf-8")
    await storage.put(key, payload)
    return AssistantGuardrailsResponse(
        refusal_message=refusal,
        blocked_topics=topics,
    )
