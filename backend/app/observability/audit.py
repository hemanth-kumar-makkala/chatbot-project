"""Audit log store implementation (Task 1.4b).

Enforces:
- Compliance audit trail (audit_events): records who viewed, uploaded, exported,
  or deleted what, and when.
- References only: records resource IDs, actions, and actors (no PHI or content payloads).
- Fail-closed contract: if writing an audit event fails, audited action is REFUSED
  by raising AuditError.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from app.db.models import AuditEvent
from app.db.session import tenant_session
from app.ports.audit import AuditLogger as AuditLoggerPort
from app.ports.types import TenantContext

logger = logging.getLogger(__name__)


class AuditError(RuntimeError):
    """Raised when an audit event fails to persist. Forces audited actions to fail-closed."""


class AuditLogger(AuditLoggerPort):
    """Service for appending compliance audit records into audit_events."""

    async def _insert_event(self, session: Any, event: AuditEvent) -> None:
        """Staging insertion point for testable fail-closed simulation."""
        session.add(event)
        await session.commit()

    async def record(
        self,
        tenant_ctx: TenantContext,
        action: str,
        event_type: str,
        resource_id: str | None = None,
    ) -> UUID:
        """Record an immutable compliance audit event.

        Contract Invariant:
        Audit is fail-closed. If writing the audit record fails for any reason,
        an AuditError is raised to prevent the audited operation from completing.
        """
        event_id = uuid4()
        user_uuid: UUID | None = None
        try:
            user_uuid = UUID(tenant_ctx.user_id)
        except ValueError:
            user_uuid = None

        event = AuditEvent(
            id=event_id,
            tenant_id=UUID(tenant_ctx.tenant_id),
            user_id=user_uuid,
            action=action,
            event_type=event_type,
            resource_id=resource_id,
        )

        try:
            async with tenant_session(tenant_ctx) as session:
                await self._insert_event(session, event)
            return event_id
        except Exception as exc:
            logger.error(
                "Audit write failed",
                extra={
                    "error_code": "ERR_AUDIT_WRITE_FAIL",
                    "error_type": type(exc).__name__,
                    "node": "audit",
                    "status": "error",
                },
            )
            raise AuditError(f"Audit write failed: {exc}") from exc
