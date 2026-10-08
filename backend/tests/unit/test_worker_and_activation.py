"""Unit tests for worker configuration, wiring guards, and job contracts (Task 2.7)."""

from __future__ import annotations

import inspect
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.ports.chunker import Chunker
from app.ports.embedding_provider import EmbeddingProvider
from app.ports.errors import ConfigurationError
from app.ports.types import WorkerContext
from app.wiring import create_chunker, create_embedding_provider
from app.worker import (
    create_procrastinate_app,
    get_procrastinate_connector,
    process_page_job,
)


class TestWorkerWiringGuards:
    """Verifies profile guards for chunker and embedding provider (Task 2.7)."""

    def test_chunker_wiring_guard_profile_matrix(self) -> None:
        """MockChunker returned ONLY for 'mock' profile; other profiles raise ConfigurationError."""
        # 1. mock profile returns Chunker port implementation
        chunker = create_chunker("mock")
        assert isinstance(chunker, Chunker)

        # 2. All other profiles raise ConfigurationError until Task 3.1
        disallowed_profiles = [
            "office-cpu",
            "desktop-gpu",
            "home-gpu",
            "cloud",
            "dev-free-api",
            "unknown-profile",
        ]
        for prof in disallowed_profiles:
            with pytest.raises(ConfigurationError) as exc_info:
                create_chunker(prof)
            assert f"Chunker is not yet implemented for profile '{prof}'" in str(exc_info.value)

    def test_embedding_wiring_guard_profile_matrix(self) -> None:
        """MockEmbeddingProvider returned ONLY for 'mock'; others raise ConfigurationError."""
        # 1. mock profile returns EmbeddingProvider port implementation
        embedder = create_embedding_provider("mock")
        assert isinstance(embedder, EmbeddingProvider)

        # 2. All other profiles raise ConfigurationError until Task 3.1
        disallowed_profiles = [
            "office-cpu",
            "desktop-gpu",
            "home-gpu",
            "cloud",
            "dev-free-api",
            "unknown-profile",
        ]
        for prof in disallowed_profiles:
            with pytest.raises(ConfigurationError) as exc_info:
                create_embedding_provider(prof)
            assert f"EmbeddingProvider is not yet implemented for profile '{prof}'" in str(
                exc_info.value
            )


class TestWorkerConfigurationAndSecurity:
    """Verifies worker isolation, tracing guards, and job argument safety."""

    def test_tracing_guard_enforced_at_worker_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Worker startup forces LangSmith / LangChain tracing variables to 'false'."""
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

        # Creating worker app enforces tracing disabled
        create_procrastinate_app()

        assert os.environ.get("LANGSMITH_TRACING") == "false"
        assert os.environ.get("LANGCHAIN_TRACING_V2") == "false"

    def test_job_args_strictly_ids_only(self) -> None:
        """Job payload arguments carry strictly IDs; zero content, filenames, or titles."""
        sig = inspect.signature(process_page_job)
        param_names = list(sig.parameters.keys())

        # Expected ID-only parameters
        expected_params = ["tenant_id", "document_version_id", "page_number"]
        assert param_names == expected_params, f"Unexpected job parameters: {param_names}"

        for name in param_names:
            assert name not in (
                "filename",
                "title",
                "content",
                "text",
                "body",
                "raw_bytes",
            ), f"Sensitive content parameter '{name}' detected in worker job definition!"

    def test_procrastinate_schema_isolation_search_path(self) -> None:
        """Procrastinate connector kwargs set search_path to isolated schema."""
        connector = get_procrastinate_connector(search_path="procrastinate")
        pool_args = connector._pool_args
        kwargs = pool_args.get("kwargs", {})

        options = kwargs.get("options", "")
        assert "-c search_path=procrastinate" in options, (
            f"Connector options missing search_path isolation: {options}"
        )

    @pytest.mark.asyncio
    async def test_worker_context_accepted_by_worker_tenant_session(self) -> None:
        """worker_tenant_session accepts WorkerContext and configures app.current_tenant_id."""
        from app.db.session import worker_tenant_session

        tenant_id = str(uuid4())
        job_id = str(uuid4())
        worker_ctx = WorkerContext(tenant_id=tenant_id, job_id=job_id)

        # WorkerContext has no user_id or role (ADR-0007 compliance)
        assert not hasattr(worker_ctx, "role")
        assert not hasattr(worker_ctx, "user_id")

        async with worker_tenant_session(worker_ctx) as session:
            res = await session.execute(
                text("SELECT current_setting('app.current_tenant_id', true)")
            )
            current_tenant = res.scalar()
            assert current_tenant == tenant_id

    @pytest.mark.asyncio
    async def test_audit_event_from_worker_uses_system_worker_actor(self) -> None:
        """Audit events from worker explicitly set user_id=None (actor is system worker)."""
        from uuid import UUID

        from app.db.models import AuditEvent
        from app.db.session import worker_tenant_session
        from app.observability.audit import AuditLogger

        tenant_id = str(uuid4())
        job_id = str(uuid4())
        worker_ctx = WorkerContext(tenant_id=tenant_id, job_id=job_id)

        # Seed tenant first
        from app.db.session import unscoped_session

        async with unscoped_session() as session:
            await session.execute(
                text("INSERT INTO tenants (id, name) VALUES (:tid, 'Audit Worker Tenant')"),
                {"tid": UUID(tenant_id)},
            )

        logger = AuditLogger()
        event_id = await logger.record_worker(
            worker_ctx=worker_ctx,
            action="ingest_page",
            event_type="page_processed",
            resource_id="doc-ver-123",
        )

        async with worker_tenant_session(worker_ctx) as session:
            from sqlalchemy import select

            row = (
                await session.execute(select(AuditEvent).where(AuditEvent.id == event_id))
            ).scalar_one()
            assert row.user_id is None, "Worker audit events must have user_id=None (no fake user)"
            assert row.action == "ingest_page"
            assert row.event_type == "page_processed"
            assert row.tenant_id == UUID(tenant_id)

    def test_is_retryable_error_classifies_deadlock_and_serialization_failures(self) -> None:
        """DeadlockDetected (40P01) and SerializationFailure (40001) are retryable."""
        from app.core.ingestion.pipeline import is_retryable_error

        # 1. Direct driver exception with sqlstate / pgcode
        class MockDriverDeadlock(Exception):
            sqlstate = "40P01"
            pgcode = "40P01"

        class MockDriverSerialization(Exception):
            sqlstate = "40001"
            pgcode = "40001"

        assert is_retryable_error(MockDriverDeadlock("deadlock detected")) is True
        assert is_retryable_error(MockDriverSerialization("serialization failure")) is True

        # 2. Wrapped DBAPIError
        class MockDBAPIError(Exception):
            def __init__(self, orig: Exception) -> None:
                super().__init__(str(orig))
                self.orig = orig

        wrapped_deadlock = MockDBAPIError(MockDriverDeadlock("deadlock"))
        wrapped_serialization = MockDBAPIError(MockDriverSerialization("serialization"))
        assert is_retryable_error(wrapped_deadlock) is True
        assert is_retryable_error(wrapped_serialization) is True

        # 3. Named exception classes
        class DeadlockDetected(Exception):
            pass

        class SerializationFailure(Exception):
            pass

        assert is_retryable_error(DeadlockDetected("deadlock")) is True
        assert is_retryable_error(SerializationFailure("could not serialize")) is True

        # 4. Permanent errors must NOT be retryable
        assert is_retryable_error(ValueError("invalid format")) is False
        assert is_retryable_error(KeyError("missing key")) is False
        assert is_retryable_error(TypeError("wrong type")) is False
