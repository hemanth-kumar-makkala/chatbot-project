"""API routes for model provider statuses and admin connection testing (Stage 1)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text

from app.api.config import ApiSettings
from app.api.deps import (
    get_api_settings,
    get_auth_provider,
    get_current_session,
    require_platform_admin,
    validate_csrf,
)
from app.db.models import Tenant, User
from app.db.session import unscoped_session
from app.ports.auth import AuthProvider
from app.ports.errors import ConfigurationError
from app.ports.model_catalog import (
    ProvidersStatusResponse,
    ProviderTestConnectionResponse,
)
from app.ports.types import SessionContext, UserRole
from app.wiring import get_model_provider_statuses, test_model_provider_connection

router = APIRouter(tags=["models"])


async def _ensure_client_or_platform_admin(
    session: SessionContext,
    auth_provider: AuthProvider,
    assistant_id: str | None = None,
) -> None:
    """Enforce client_admin or platform_admin authorization (deny staff with 403)."""
    if UserRole.PLATFORM_ADMIN in session.roles or UserRole.CLIENT_ADMIN in session.roles:
        return

    try:
        uid = UUID(session.user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client admin or platform admin privileges required",
        ) from None

    async with unscoped_session() as db_session:
        stmt_user = select(User.is_platform_admin).where(User.id == uid)
        is_padmin = (await db_session.execute(stmt_user)).scalar_one_or_none()
        if is_padmin:
            return

        if assistant_id:
            try:
                asst_uuid = UUID(assistant_id)
            except (ValueError, TypeError):
                asst_uuid = None
            if asst_uuid is not None:
                res_t = await db_session.execute(
                    text("SELECT lookup_assistant_tenant(:asst_id)"),
                    {"asst_id": asst_uuid},
                )
                t_uuid = res_t.scalar_one_or_none()
                if t_uuid is not None:
                    roles = await auth_provider.roles(session.user_id, str(t_uuid))
                    if UserRole.CLIENT_ADMIN in roles:
                        return

        if session.tenant_id:
            roles = await auth_provider.roles(session.user_id, session.tenant_id)
            if UserRole.CLIENT_ADMIN in roles:
                return

        # Built-in session cookies carry tenant_id="": inspect global tenants via RLS role check
        tenant_ids = (await db_session.execute(select(Tenant.id))).scalars().all()
        for t_id in tenant_ids:
            roles = await auth_provider.roles(session.user_id, str(t_id))
            if UserRole.CLIENT_ADMIN in roles:
                return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Client admin or platform admin privileges required",
    )


async def _ensure_platform_admin(session: SessionContext) -> None:
    """Enforce platform_admin authorization (rejects staff and client_admin with 403)."""
    if UserRole.PLATFORM_ADMIN in session.roles:
        return
    try:
        UUID(session.user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin privileges required",
        ) from None
    await require_platform_admin(session)


@router.get(
    "/v1/models/providers",
    response_model=ProvidersStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def list_model_providers(
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    assistant_id: Annotated[str | None, Query()] = None,
) -> ProvidersStatusResponse:
    """List curated providers, readiness, missing env setting names, and available models."""
    await _ensure_client_or_platform_admin(
        session=session,
        auth_provider=auth_provider,
        assistant_id=assistant_id,
    )
    try:
        return await get_model_provider_statuses(settings=settings)
    except ConfigurationError as cfg_err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": cfg_err.error_code or "MODEL_CATALOG_INVALID",
                "message": "Model catalog configuration is invalid.",
            },
        ) from cfg_err


@router.post(
    "/v1/models/providers/{provider}/test-connection",
    response_model=ProviderTestConnectionResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(validate_csrf)],
)
async def test_model_provider(
    provider: str,
    session: Annotated[SessionContext, Depends(get_current_session)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> ProviderTestConnectionResponse:
    """Execute one tiny probe request for a provider (platform_admin + CSRF required)."""
    await _ensure_platform_admin(session)
    try:
        return await test_model_provider_connection(provider_id=provider, settings=settings)
    except ConfigurationError as cfg_err:
        if cfg_err.error_code == "PROVIDER_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PROVIDER_NOT_FOUND", "message": "Unknown model provider."},
            ) from cfg_err
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": cfg_err.error_code or "MODEL_CATALOG_INVALID",
                "message": "Model catalog configuration is invalid.",
            },
        ) from cfg_err
