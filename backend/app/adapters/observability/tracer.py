"""PostgreSQL implementation of the Tracer port (Task 1.4b).

Enforces:
- Own transaction: traces are written in an independent transaction so request rollbacks
  do not discard telemetry rows.
- Fail-open contract: if DB write times out or fails, logs an error code and returns
  without raising exceptions to the caller.
- Strict telemetry schema (Invariant 10): records only IDs, metrics, token counts, and
  typed error codes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from app.db.models import RunStep
from app.db.session import tenant_session
from app.ports.tracer import Tracer
from app.ports.types import (
    StepEndPayload,
    StepErrorPayload,
    StepStartPayload,
    TenantContext,
    UserRole,
)

logger = logging.getLogger(__name__)


class PostgresTracer(Tracer):
    """PostgreSQL-backed execution tracer storing step records in run_steps."""

    def __init__(self, write_timeout_seconds: float = 1.0) -> None:
        self.write_timeout_seconds = write_timeout_seconds
        # In-memory mapping of step_id to tenant_id so step_end/step_error know the tenant partition
        self._step_tenants: dict[str, str] = {}

    def _build_tenant_ctx(self, tenant_id: str) -> TenantContext:
        return TenantContext(
            tenant_id=tenant_id,
            user_id="tracer_system",
            role=UserRole.STAFF,
            session_id="trace_session",
        )

    async def _execute_in_session(self, ctx: TenantContext, action_coro_fn: Any) -> None:
        """Execute action within a dedicated tenant transaction."""
        async with tenant_session(ctx) as session:
            await action_coro_fn(session)
            await session.commit()

    async def _persist_step(self, tenant_id: str, action_coro_fn: Any) -> None:
        """Execute telemetry persistence in its own transaction with timeout and fail-open."""
        ctx = self._build_tenant_ctx(tenant_id)

        try:
            await asyncio.wait_for(
                self._execute_in_session(ctx, action_coro_fn),
                timeout=self.write_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "Tracer write timed out",
                extra={
                    "error_code": "ERR_TRACER_TIMEOUT",
                    "error_type": "TimeoutError",
                    "node": "tracer",
                    "status": "warning",
                },
            )
        except Exception as exc:
            logger.warning(
                "Tracer write failed",
                extra={
                    "error_code": "ERR_TRACER_DB_FAIL",
                    "error_type": type(exc).__name__,
                    "node": "tracer",
                    "status": "warning",
                },
            )

    async def step_start(self, payload: StepStartPayload) -> str:
        step_id = str(uuid4())
        self._step_tenants[step_id] = payload.tenant_id

        async def _insert(session: Any) -> None:
            step = RunStep(
                id=UUID(step_id),
                tenant_id=UUID(payload.tenant_id),
                request_id=payload.request_id,
                step_name=payload.step_name,
                node_number=payload.node_number,
                status="running",
                duration_ms=0.0,
            )
            session.add(step)

        await self._persist_step(payload.tenant_id, _insert)
        return step_id

    async def step_end(self, step_id: str, payload: StepEndPayload) -> None:
        tenant_id = self._step_tenants.get(step_id)
        if not tenant_id:
            logger.warning(
                "Tracer step_end missing tenant context",
                extra={
                    "error_code": "ERR_TRACER_NO_TENANT",
                    "error_type": "KeyError",
                    "node": "tracer",
                    "status": "warning",
                },
            )
            return

        async def _update(session: Any) -> None:
            stmt = (
                sa.update(RunStep)
                .where(RunStep.id == UUID(step_id))
                .values(
                    status="completed",
                    duration_ms=payload.duration_ms,
                    input_tokens=payload.input_tokens,
                    output_tokens=payload.output_tokens,
                    item_count=payload.item_count,
                )
            )
            await session.execute(stmt)

        await self._persist_step(tenant_id, _update)

    async def step_error(self, step_id: str, payload: StepErrorPayload) -> None:
        tenant_id = self._step_tenants.get(step_id)
        if not tenant_id:
            logger.warning(
                "Tracer step_error missing tenant context",
                extra={
                    "error_code": "ERR_TRACER_NO_TENANT",
                    "error_type": "KeyError",
                    "node": "tracer",
                    "status": "warning",
                },
            )
            return

        async def _update(session: Any) -> None:
            stmt = (
                sa.update(RunStep)
                .where(RunStep.id == UUID(step_id))
                .values(
                    status="error",
                    duration_ms=payload.duration_ms,
                    error_code=payload.error_code,
                    error_type=payload.error_type,
                )
            )
            await session.execute(stmt)

        await self._persist_step(tenant_id, _update)
