"""Port interface for compliance audit logging.

Enforces:
- Compliance audit trail (audit_events): records who viewed, uploaded, exported,
  or deleted what, and when.
- References only: records resource IDs, actions, and actors (no PHI or content payloads).
- Fail-closed contract: if writing an audit event fails, the audited action must fail.
"""

from abc import ABC, abstractmethod
from uuid import UUID

from app.ports.types import TenantContext, WorkerContext


class AuditLogger(ABC):
    """Abstract port for compliance audit event logging (audit_events).

    Contract Invariant:
    Audit is fail-closed. If writing the audit record fails for any reason,
    an exception is raised to prevent the audited operation from completing.
    """

    @abstractmethod
    async def record(
        self,
        tenant_ctx: TenantContext,
        action: str,
        event_type: str,
        resource_id: str | None = None,
    ) -> UUID:
        """Record an immutable compliance audit event."""
        ...

    @abstractmethod
    async def record_worker(
        self,
        worker_ctx: WorkerContext,
        action: str,
        event_type: str,
        resource_id: str | None = None,
    ) -> UUID:
        """Record an immutable compliance audit event on behalf of system worker."""
        ...
