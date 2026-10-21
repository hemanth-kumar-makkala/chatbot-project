"""Conversation and message history management routes under tenant RLS (Task 3.6)."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import (
    get_auth_provider,
    get_current_session,
    get_tenant_context_for_assistant,
    validate_csrf,
)
from app.db.models import Conversation, Message, Tenant
from app.db.session import tenant_session, unscoped_session
from app.ports.auth import AuthProvider
from app.ports.types import SessionContext, TenantContext

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
