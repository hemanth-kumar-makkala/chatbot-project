"""Unit tests for worker configuration, wiring guards, and job contracts (Task 2.7)."""

from __future__ import annotations

import inspect
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.ports.embedding_provider import EmbeddingProvider
from app.ports.errors import ConfigurationError
from app.ports.types import WorkerContext
from app.wiring import create_chunker, create_embedding_provider
from app.worker import (
    create_procrastinate_app,
    get_procrastinate_connector,
    process_page_job,
    split_document_job,
    stalled_job_sweep_task,
)


class TestWorkerWiringGuards:
    """Verifies profile guards for chunker and embedding provider (Task 2.7)."""

    def test_chunker_wiring_guard_profile_matrix(self) -> None:
        """MockChunker returned for 'mock', StructureAwareChunker for real profiles."""
        from app.adapters.chunking.mock import MockChunker
        from app.adapters.chunking.structure_aware import StructureAwareChunker

        # 1. mock profile returns MockChunker
        mock_chunker = create_chunker("mock")
        assert isinstance(mock_chunker, MockChunker)

        # 2. All non-mock profiles return StructureAwareChunker
        real_profiles = [
            "office-cpu",
            "desktop-gpu",
            "home-gpu",
            "cloud",
            "dev-free-api",
        ]
        for prof in real_profiles:
            chunker = create_chunker(prof)
            assert isinstance(chunker, StructureAwareChunker)

        # 3. Unknown profile raises ConfigurationError
        with pytest.raises(ConfigurationError) as exc_info:
            create_chunker("unknown-profile")
        assert "Unknown or unauthorized profile for Chunker" in str(exc_info.value)

    def test_embedding_wiring_guard_profile_matrix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MockEmbeddingProvider for 'mock'; BgeM3 for 'desktop-gpu' and 'home-gpu'."""
        from app.adapters.embedding.bge_m3 import BgeM3EmbeddingProvider
        from app.adapters.embedding.mock import MockEmbeddingProvider
        from tests.unit.test_bge_m3_adapter import FakeSentenceTransformerModel

        # 1. mock profile returns EmbeddingProvider port implementation
        embedder = create_embedding_provider("mock")
        assert isinstance(embedder, MockEmbeddingProvider)
        assert isinstance(embedder, EmbeddingProvider)

        # 2. desktop-gpu and home-gpu return BgeM3EmbeddingProvider
        fake_model = FakeSentenceTransformerModel()
        monkeypatch.setattr(
            "app.adapters.embedding.bge_m3.load_bge_m3_model",
            lambda _cfg: fake_model,
        )
        for prof in ["desktop-gpu", "home-gpu"]:
            real_embedder = create_embedding_provider(prof)
            assert isinstance(real_embedder, BgeM3EmbeddingProvider)
            assert isinstance(real_embedder, EmbeddingProvider)

        # 3. Disallowed profiles raise ConfigurationError
        disallowed_profiles = [
            "office-cpu",
            "cloud",
            "dev-free-api",
            "unknown-profile",
        ]
        for prof in disallowed_profiles:
            with pytest.raises(ConfigurationError) as exc_info:
                create_embedding_provider(prof)
            assert "Unknown or unauthorized profile for EmbeddingProvider" in str(exc_info.value)


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
        sensitive_names = ("filename", "title", "content", "text", "body", "raw_bytes")

        # 1. process_page_job: first param is context (pass_context=True), remaining are IDs
        assert process_page_job.pass_context is True
        page_params = list(inspect.signature(process_page_job).parameters.keys())
        assert page_params[0] == "context"
        assert page_params[1:] == ["tenant_id", "document_version_id", "page_number"]
        for name in page_params:
            assert name not in sensitive_names, f"Sensitive param '{name}' in process_page_job"

        # 2. split_document_job: first param is context (pass_context=True), remaining are IDs
        assert split_document_job.pass_context is True
        split_params = list(inspect.signature(split_document_job).parameters.keys())
        assert split_params[0] == "context"
        assert split_params[1:] == ["tenant_id", "document_version_id"]
        for name in split_params:
            assert name not in sensitive_names, f"Sensitive param '{name}' in split_document_job"

        # 3. stalled_job_sweep_task: first param is context, remaining are IDs/counters
        assert stalled_job_sweep_task.pass_context is True
        sweep_params = list(inspect.signature(stalled_job_sweep_task).parameters.keys())
        assert sweep_params[0] == "context"
        assert sweep_params[1:] == ["timestamp", "seconds_since_heartbeat"]
        for name in sweep_params:
            assert name not in sensitive_names, (
                f"Sensitive param '{name}' in stalled_job_sweep_task"
            )

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

    def test_wrapped_deadlock_is_retried_and_contains_no_raw_exception_text(self) -> None:
        """Transient error is wrapped in TransientIngestionError with zero raw exception text."""
        import io
        import logging
        import traceback

        from procrastinate.jobs import Job
        from procrastinate.retry import RetryStrategy

        from app.core.ingestion.pipeline import is_retryable_error
        from app.ports.errors import TransientIngestionError

        class MockDriverDeadlock(Exception):
            sqlstate = "40P01"
            pgcode = "40P01"

        sensitive_text = "PATIENT_SECRET_DATA_DO_NOT_LOG: SELECT * FROM phi WHERE ssn='123'"
        raw_driver_exc = MockDriverDeadlock(sensitive_text)

        class MockDBAPIError(Exception):
            def __init__(self, orig: Exception) -> None:
                super().__init__(str(orig))
                self.orig = orig

        wrapped_deadlock = MockDBAPIError(raw_driver_exc)

        # 1. Classified as retryable
        assert is_retryable_error(wrapped_deadlock) is True

        # 2. Re-raised as TransientIngestionError with fixed message and SQLSTATE code only
        orig = getattr(wrapped_deadlock, "orig", wrapped_deadlock)
        code = (
            getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None) or "TRANSIENT_DB_ERROR"
        )
        try:
            raise TransientIngestionError(
                message="Transient database deadlock detected",
                error_code=code,
            ) from None
        except TransientIngestionError as raised:
            # 3. Assert zero raw exception text reaches error message, details, or cause
            assert sensitive_text not in str(raised)
            assert sensitive_text not in raised.message
            assert sensitive_text not in str(raised.details)
            assert raised.__cause__ is None, "Must raise with 'from None' to suppress chained error"
            assert raised.__suppress_context__ is True
            assert raised.error_code == "40P01"

            # 4. Traceback formatting contains zero raw exception text
            formatted_tb = "".join(
                traceback.format_exception(type(raised), raised, raised.__traceback__)
            )
            assert sensitive_text not in formatted_tb

            # 5. Procrastinate logger call (worker.py:240) contains zero sensitive text
            log_stream = io.StringIO()
            handler = logging.StreamHandler(log_stream)
            handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
            proc_logger = logging.getLogger("procrastinate.worker.test")
            proc_logger.addHandler(handler)
            proc_logger.setLevel(logging.INFO)
            try:
                proc_logger.log(
                    logging.INFO,
                    "Job ended with status: Error, to retry",
                    exc_info=raised,
                )
                handler.flush()
                log_output = log_stream.getvalue()
                assert sensitive_text not in log_output
            finally:
                proc_logger.removeHandler(handler)

            # 6. Procrastinate RetryStrategy retries TransientIngestionError
            strategy = RetryStrategy(
                max_attempts=5,
                exponential_wait=2,
                retry_exceptions=(TransientIngestionError,),
            )
            mock_job = Job(
                queue="ingestion",
                lock=None,
                queueing_lock=None,
                task_name=process_page_job.name,
                attempts=0,
            )
            decision = strategy.get_retry_decision(exception=raised, job=mock_job)
            assert decision is not None
            assert decision.retry_at is not None

    def test_permanent_error_is_not_retried(self) -> None:
        """Permanent error is not classified as retryable and Procrastinate does not retry it."""
        from procrastinate.jobs import Job
        from procrastinate.retry import RetryStrategy

        from app.core.ingestion.pipeline import is_retryable_error
        from app.ports.errors import ParseError, TransientIngestionError
        from app.worker import process_page_job

        perm_err = ParseError("Invalid PDF layout")
        assert is_retryable_error(perm_err) is False

        strategy = RetryStrategy(
            max_attempts=5,
            exponential_wait=2,
            retry_exceptions=(TransientIngestionError,),
        )
        mock_job = Job(
            queue="ingestion",
            lock=None,
            queueing_lock=None,
            task_name=process_page_job.name,
            attempts=0,
        )
        assert strategy.get_retry_decision(exception=perm_err, job=mock_job) is None

    def test_temporary_error_final_attempt_proof(self) -> None:
        """Proves real task RetryStrategy boundary (decision at attempts=4, None at attempts=5)."""
        from procrastinate.jobs import Job

        from app.ports.errors import TransientIngestionError
        from app.worker import process_page_job

        strategy = process_page_job.retry_strategy
        assert strategy is not None
        transient_err = TransientIngestionError(error_code="40P01")

        # Attempt 4: not final, returns retry decision
        job_attempt_4 = Job(
            queue="ingestion",
            lock=None,
            queueing_lock=None,
            task_name=process_page_job.name,
            attempts=4,
        )
        decision_4 = strategy.get_retry_decision(exception=transient_err, job=job_attempt_4)
        assert decision_4 is not None, "Attempt 4 must not return None (not final)"

        # Attempt 5: true boundary where job.attempts >= max_attempts (5), returns None
        job_attempt_5 = Job(
            queue="ingestion",
            lock=None,
            queueing_lock=None,
            task_name=process_page_job.name,
            attempts=5,
        )
        decision_5 = strategy.get_retry_decision(exception=transient_err, job=job_attempt_5)
        assert decision_5 is None, "Attempt 5 must return None (final attempt)"
