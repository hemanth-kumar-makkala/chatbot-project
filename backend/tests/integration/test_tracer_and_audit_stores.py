"""Integration tests for Tracer (run_steps) and Audit Log (audit_events) (Task 1.4b).

Verifies:
- Tracer writes run_steps in its own transaction, separate from the request's transaction,
  so traces of failed and rolled-back requests are kept.
- Tracer write timeout: write timeout does not delay request beyond timeout.
- Tracer DB outage: tracer DB failure logs error code and returns without raising (fail-open).
- Audit is fail-closed: if writing an audit_events row fails, audited action is refused
  with a safe error.
- Audit success: valid action records append-only row in audit_events table.
"""

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from app.adapters.observability.tracer import PostgresTracer
from app.db.models import Assistant, AuditEvent, RunStep, Tenant
from app.db.session import tenant_session, unscoped_session
from app.observability.audit import AuditError, AuditLogger
from app.ports.types import (
    StepEndPayload,
    StepErrorPayload,
    StepStartPayload,
    TenantContext,
    UserRole,
)


class TestPostgresTracerIntegration:
    """Verifies PostgresTracer transaction isolation and fail-open contract."""

    async def test_tracer_writes_in_own_transaction_survives_request_rollback(self) -> None:
        tenant_id = uuid4()
        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id="user-trace-1",
            role=UserRole.STAFF,
            session_id="sess-trace-1",
        )

        # Create tenant
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Trace Rollback Tenant"))
            await session.commit()

        tracer = PostgresTracer(write_timeout_seconds=2.0)
        request_id = f"req_{uuid4()}"

        # Start an application request transaction
        async with tenant_session(ctx) as app_session:
            # 1. Tracer records step start (in its own independent transaction)
            step_id = await tracer.step_start(
                StepStartPayload(
                    request_id=request_id,
                    tenant_id=str(tenant_id),
                    step_name="retrieve",
                    node_number=3,
                )
            )

            # 2. Application performs work and stages a record
            app_session.add(
                Assistant(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    name="Will Be Rolled Back",
                    prompt_id="p1",
                    prompt_version="1.0",
                )
            )
            # Application intentionally rolls back / raises
            await app_session.rollback()

        # Record step completion / error after rollback
        await tracer.step_end(
            step_id,
            StepEndPayload(
                request_id=request_id,
                step_name="retrieve",
                duration_ms=45.2,
                item_count=5,
            ),
        )

        # In a new session, query run_steps and assistants
        async with tenant_session(ctx) as verify_session:
            # Assistant was rolled back -> 0 rows
            assistants = (await verify_session.execute(sa.select(Assistant))).scalars().all()
            assert len(assistants) == 0

            # Trace was in its own transaction -> row must exist!
            stmt = sa.select(RunStep).where(RunStep.request_id == request_id)
            steps = (await verify_session.execute(stmt)).scalars().all()
            assert len(steps) == 1
            step = steps[0]
            assert str(step.id) == step_id
            assert step.step_name == "retrieve"
            assert step.status == "completed"
            assert step.duration_ms == 45.2
            assert step.item_count == 5

    async def test_tracer_timeout_does_not_block_beyond_timeout(self) -> None:
        """Tracer timeout aborts slow write and returns without raising."""
        tracer = PostgresTracer(write_timeout_seconds=0.05)
        request_id = f"req_{uuid4()}"
        tenant_id = str(uuid4())

        async def slow_execute(*_args: object, **_kwargs: object) -> None:
            await asyncio.sleep(0.5)

        with patch.object(tracer, "_execute_in_session", side_effect=slow_execute):
            start_time = asyncio.get_running_loop().time()
            # Must not raise and must complete near the 0.05s timeout
            step_id = await tracer.step_start(
                StepStartPayload(
                    request_id=request_id,
                    tenant_id=tenant_id,
                    step_name="route",
                    node_number=2,
                )
            )
            elapsed = asyncio.get_running_loop().time() - start_time
            assert elapsed < 0.25, f"Tracer took too long: {elapsed}s"
            assert isinstance(step_id, str)

    async def test_tracer_db_outage_is_fail_open(self) -> None:
        """Tracer handles DB error gracefully and returns without raising."""
        tracer = PostgresTracer(write_timeout_seconds=1.0)
        request_id = f"req_{uuid4()}"
        tenant_id = str(uuid4())

        with patch.object(
            tracer,
            "_execute_in_session",
            side_effect=OperationalError("DB connection lost", None, Exception()),
        ):
            # step_start fails open
            step_id = await tracer.step_start(
                StepStartPayload(
                    request_id=request_id,
                    tenant_id=tenant_id,
                    step_name="generate",
                    node_number=7,
                )
            )
            assert isinstance(step_id, str)

            # step_end fails open
            await tracer.step_end(
                step_id,
                StepEndPayload(
                    request_id=request_id,
                    step_name="generate",
                    duration_ms=250.0,
                ),
            )

            # step_error fails open
            await tracer.step_error(
                step_id,
                StepErrorPayload(
                    request_id=request_id,
                    step_name="generate",
                    duration_ms=10.0,
                    error_code="ERR_MODEL_TIMEOUT",
                    error_type="ProviderTimeout",
                ),
            )


class TestAuditLoggerIntegration:
    """Verifies AuditLogger append-only persistence and fail-closed contract."""

    async def test_audit_event_logged_successfully(self) -> None:
        tenant_id = uuid4()
        user_id = uuid4()
        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            role=UserRole.CLIENT_ADMIN,
            session_id="audit-sess-1",
        )

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Audit Tenant"))
            await session.commit()

        audit_logger = AuditLogger()
        event_id = await audit_logger.record(
            tenant_ctx=ctx,
            action="EXPORT_KNOWLEDGE",
            event_type="COMPLIANCE_EXPORT",
            resource_id="doc_version_123",
        )
        assert event_id is not None

        # Verify row in audit_events
        async with tenant_session(ctx) as session:
            event = (
                await session.execute(sa.select(AuditEvent).where(AuditEvent.id == event_id))
            ).scalar_one_or_none()
            assert event is not None
            assert event.tenant_id == tenant_id
            assert event.user_id == user_id
            assert event.action == "EXPORT_KNOWLEDGE"
            assert event.event_type == "COMPLIANCE_EXPORT"
            assert event.resource_id == "doc_version_123"

    async def test_audit_failure_is_fail_closed(self) -> None:
        """If audit logging fails, it must raise AuditError to refuse the audited action."""
        tenant_id = uuid4()
        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="s1",
        )

        audit_logger = AuditLogger()

        with patch.object(
            audit_logger,
            "_insert_event",
            side_effect=OperationalError("DB connection lost", None, Exception()),
        ):
            with pytest.raises(AuditError) as exc_info:
                await audit_logger.record(
                    tenant_ctx=ctx,
                    action="DELETE_DOCUMENT",
                    event_type="DOCUMENT_MANAGEMENT",
                    resource_id="doc_456",
                )
            assert "audit write failed" in str(exc_info.value).lower()
