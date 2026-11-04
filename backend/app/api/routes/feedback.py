"""Feedback submission routes for message ratings and reasons.

Enforces:
- Tenant-scoped persistence to feedback table (Task 5.5).
- One rating per user per message (subsequent ratings update the existing record).
- CSRF token validation and staff role authorization.
- Anti-probing: accessing another tenant's message or assistant returns 404.
- Reason text never enters operational logs or audit events (Invariant 9, 10).
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import (
    get_auth_provider,
    get_current_session,
    get_tenant_context_for_assistant,
    validate_csrf,
)
from app.db.models import AuditEvent, Conversation, Feedback, Message
from app.db.session import tenant_session, unscoped_session
from app.ports.auth import AuthProvider
from app.ports.types import SessionContext, TenantContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["feedback"])

MAX_FEEDBACK_REASON_LENGTH: int = 1000


class FeedbackRequest(BaseModel):
    """Payload for submitting message rating and optional reason."""

    rating: Literal["up", "down"]
    reason: str | None = Field(default=None, max_length=MAX_FEEDBACK_REASON_LENGTH)


class FeedbackResponse(BaseModel):
    """Response schema for recorded user feedback."""

    id: str
    message_id: str
    user_id: str
    rating: str
    reason: str | None = None
    created_at: str
    updated_at: str


@router.post(
    "/assistants/{assistant_id}/messages/{message_id}/feedback",
    response_model=FeedbackResponse,
)
@router.post(
    "/messages/{message_id}/feedback",
    response_model=FeedbackResponse,
)
async def submit_feedback(
    message_id: str,
    payload: FeedbackRequest,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    _csrf: Annotated[None, Depends(validate_csrf)],
    assistant_id: str | None = None,
) -> FeedbackResponse:
    """Submit or update thumbs up/down rating with optional reason for a message."""
    try:
        msg_uuid = UUID(message_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        ) from None

    target_tenant_ctx: TenantContext | None = None

    if assistant_id is not None:
        # 1. Scoped lookup through specific assistant
        target_tenant_ctx = await get_tenant_context_for_assistant(
            assistant_id=assistant_id,
            session=session,
            auth_provider=auth_provider,
        )
        asst_uuid = UUID(assistant_id)

        async with tenant_session(target_tenant_ctx) as db_sess:
            msg_stmt = (
                select(Message.id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Message.tenant_id == UUID(target_tenant_ctx.tenant_id),
                    Message.id == msg_uuid,
                    Conversation.assistant_id == asst_uuid,
                )
            )
            msg_exists = (await db_sess.execute(msg_stmt)).scalar_one_or_none()
            if not msg_exists:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Message not found",
                )
    else:
        # 2. Direct message route: look up message across tenants and verify membership
        async with unscoped_session() as db_sess:
            raw_msg = (
                await db_sess.execute(select(Message.tenant_id).where(Message.id == msg_uuid))
            ).scalar_one_or_none()
            if not raw_msg:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Message not found",
                )
            tenant_str = str(raw_msg)

        roles = await auth_provider.roles(session.user_id, tenant_str)
        if not roles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Message not found",
            )

        target_tenant_ctx = TenantContext(
            tenant_id=tenant_str,
            user_id=session.user_id,
            role=roles[0],
            session_id=session.session_id,
        )

    # Clean and normalize optional reason string
    clean_reason: str | None = payload.reason.strip() if payload.reason is not None else None
    if clean_reason == "":
        clean_reason = None

    # Upsert feedback under tenant RLS context
    async with tenant_session(target_tenant_ctx) as db_sess:
        fb_stmt = select(Feedback).where(
            Feedback.tenant_id == UUID(target_tenant_ctx.tenant_id),
            Feedback.message_id == msg_uuid,
            Feedback.user_id == UUID(session.user_id),
        )
        existing_fb = (await db_sess.execute(fb_stmt)).scalar_one_or_none()

        action = "feedback_updated" if existing_fb else "feedback_created"

        if existing_fb:
            existing_fb.rating = payload.rating
            existing_fb.reason = clean_reason
            existing_fb.updated_at = func.now()
            fb_record = existing_fb
        else:
            fb_record = Feedback(
                id=uuid4(),
                tenant_id=UUID(target_tenant_ctx.tenant_id),
                message_id=msg_uuid,
                user_id=UUID(session.user_id),
                rating=payload.rating,
                reason=clean_reason,
            )
            db_sess.add(fb_record)

        # Audit event records metadata and IDs ONLY (Invariant 9, 10: zero reason text)
        audit_event = AuditEvent(
            id=uuid4(),
            tenant_id=UUID(target_tenant_ctx.tenant_id),
            user_id=UUID(session.user_id),
            event_type="feedback",
            action=action,
            resource_id=str(fb_record.id),
        )
        db_sess.add(audit_event)
        await db_sess.flush()
        await db_sess.refresh(fb_record)

        # Operational log records metadata only (zero reason text)
        logger.info(
            "Recorded user feedback",
            extra={
                "tenant_id": target_tenant_ctx.tenant_id,
                "message_id": str(msg_uuid),
                "rating": payload.rating,
                "action": action,
            },
        )

        return FeedbackResponse(
            id=str(fb_record.id),
            message_id=str(fb_record.message_id),
            user_id=str(fb_record.user_id),
            rating=fb_record.rating,
            reason=fb_record.reason,
            created_at=fb_record.created_at.isoformat(),
            updated_at=fb_record.updated_at.isoformat(),
        )
