"""Health check and diagnostic endpoints."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from app.api.config import ApiSettings
from app.api.deps import get_api_settings, require_platform_admin
from app.db.session import unscoped_session
from app.ports.types import SessionContext

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Public health check endpoint returning ok/not-ok status only.

    Security rule: Profile, database, and telemetry details are strictly excluded.
    """
    return {"status": "ok"}


@router.get("/v1/admin/health")
async def admin_health_check(
    session: Annotated[SessionContext, Depends(require_platform_admin)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> dict[str, Any]:
    """Admin-only diagnostic endpoint disclosing database and profile status."""
    db_ok = False
    try:
        async with unscoped_session() as db_session:
            result = await db_session.execute(text("SELECT 1"))
            db_ok = result.scalar() == 1
    except Exception as e:
        logger.error("Admin health database failure: error_type=%s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database diagnostic failure (error_code: DB_DIAGNOSTIC_FAILURE)",
        ) from None

    return {
        "status": "ok" if db_ok else "degraded",
        "profile": getattr(settings, "profile", "mock"),
        "database": "connected" if db_ok else "disconnected",
        "admin_user_id": session.user_id,
    }
