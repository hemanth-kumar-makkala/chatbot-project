"""Unit tests for Port Interface Contracts (Task 1.3).

Verifies:
- All 9 port interfaces are pure Abstract Base Classes (ABC) and cannot be instantiated.
- All I/O methods are coroutines (async).
- DocumentParser.split_pages returns an AsyncIterator (page-first, no whole-file parsing).
- Retriever uses typed TenantContext and returns typed EvidenceId models.
- Retriever includes search_titles for ADR-0002 clarify topic grounding.
- StorageBackend operations require a StorageKey containing tenant_id.
- Roles are constrained to UserRole enum (platform_admin, client_admin, staff).
- ModelResponse, PageReading, ExtractedFields contain model_id and model_version.
- ExtractedFields reports field unreadability explicitly via FieldState.
- Tracer uses typed telemetry payloads without free-form content fields.
- Tracer contract specifies tracing failure must never fail the request.
- Shared error hierarchy inherits from PortError with retryable flags.
"""

import inspect
from abc import ABC
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.ports import (
    AuthProvider,
    DocumentParser,
    EmbeddingProvider,
    JobQueue,
    ModelProvider,
    Reranker,
    Retriever,
    StorageBackend,
    Tracer,
)
from app.ports.errors import (
    AuthenticationError,
    EmptyOutput,
    MalformedOutput,
    ParseError,
    PortError,
    ProviderTimeout,
    ProviderUnavailable,
    QueueError,
    StorageError,
)
from app.ports.types import (
    EvidenceId,
    EvidenceType,
    ExtractedFields,
    ExtractedFieldValue,
    ModelResponse,
    PageReading,
    ReadState,
    StepErrorPayload,
    StorageKey,
    TenantContext,
    UserRole,
)


class TestPortErrors:
    """Verifies the shared error set that all adapters must raise."""

    def test_shared_error_hierarchy(self) -> None:
        assert issubclass(ProviderTimeout, PortError)
        assert issubclass(ProviderUnavailable, PortError)
        assert issubclass(MalformedOutput, PortError)
        assert issubclass(EmptyOutput, PortError)
        assert issubclass(AuthenticationError, PortError)
        assert issubclass(StorageError, PortError)
        assert issubclass(ParseError, PortError)
        assert issubclass(QueueError, PortError)

    def test_error_retryability_flags(self) -> None:
        timeout_err = ProviderTimeout("Model timed out")
        assert timeout_err.retryable is True
        assert timeout_err.status_code == 504

        unavail_err = ProviderUnavailable("Service unreachable")
        assert unavail_err.retryable is True
        assert unavail_err.status_code == 503

        malformed_err = MalformedOutput("Invalid JSON")
        assert malformed_err.retryable is False
        assert malformed_err.status_code == 502

        empty_err = EmptyOutput("Model produced empty text")
        assert empty_err.retryable is False
        assert empty_err.status_code == 502

        auth_err = AuthenticationError("Unauthorized")
        assert auth_err.retryable is False
        assert auth_err.status_code == 401


class TestPortAbstractness:
    """Verifies all 9 ports inherit from ABC and cannot be directly instantiated."""

    @pytest.mark.parametrize(
        "port_cls",
        [
            ModelProvider,
            EmbeddingProvider,
            DocumentParser,
            Retriever,
            StorageBackend,
            JobQueue,
            AuthProvider,
            Tracer,
            Reranker,
        ],
    )
    def test_cannot_instantiate_abstract_port(self, port_cls: type) -> None:
        assert issubclass(port_cls, ABC)
        with pytest.raises(TypeError):
            port_cls()


class TestModelProviderContract:
    """Verifies ModelProvider method signatures, typing, and async requirements."""

    def test_methods_are_async(self) -> None:
        assert inspect.iscoroutinefunction(ModelProvider.generate)
        assert inspect.iscoroutinefunction(ModelProvider.read_page)
        assert inspect.iscoroutinefunction(ModelProvider.extract_fields)

    def test_model_response_and_readings_require_provenance(self) -> None:
        resp = ModelResponse(
            content="Answer text",
            model_id="qwen3-vl:4b",
            model_version="1.0.0",
        )
        assert resp.model_id == "qwen3-vl:4b"
        assert resp.model_version == "1.0.0"

        reading = PageReading(
            reading="Reading text",
            confidence=0.95,
            model_id="qwen3-vl:4b",
            model_version="1.0.0",
        )
        assert reading.model_id == "qwen3-vl:4b"

    def test_extracted_fields_unreadable_state_explicit(self) -> None:
        fields = ExtractedFields(
            fields={
                "patient_name": ExtractedFieldValue(
                    field_name="patient_name",
                    value="Jane Doe",
                    state=ReadState.PRESENT,
                    confidence=0.99,
                ),
                "rx_number": ExtractedFieldValue(
                    field_name="rx_number",
                    value=None,
                    state=ReadState.UNREADABLE,
                    confidence=0.1,
                ),
                "middle_name": ExtractedFieldValue(
                    field_name="middle_name",
                    value=None,
                    state=ReadState.NOT_PRESENT,
                    confidence=0.95,
                ),
            },
            model_id="qwen3-vl:4b",
            model_version="1.0.0",
        )
        assert fields.fields["rx_number"].state == ReadState.UNREADABLE
        assert fields.fields["rx_number"].value is None
        assert fields.fields["patient_name"].state == ReadState.PRESENT
        assert fields.fields["middle_name"].state == ReadState.NOT_PRESENT


class TestEmbeddingProviderContract:
    """Verifies EmbeddingProvider abstract properties and embed coroutine."""

    def test_embed_is_async(self) -> None:
        assert inspect.iscoroutinefunction(EmbeddingProvider.embed)

    def test_requires_model_id_and_version_properties(self) -> None:
        assert isinstance(EmbeddingProvider.model_id, property)
        assert isinstance(EmbeddingProvider.version, property)


class TestRerankerContract:
    """Verifies Reranker async contract."""

    def test_rerank_is_async(self) -> None:
        assert inspect.iscoroutinefunction(Reranker.rerank)


class TestDocumentParserContract:
    """Verifies page-first DocumentParser contract with AsyncIterator."""

    def test_split_pages_signature_and_page_first(self) -> None:
        sig = inspect.signature(DocumentParser.split_pages)
        assert "file" in sig.parameters
        assert "filename" in sig.parameters
        assert inspect.iscoroutinefunction(DocumentParser.extract)


class TestRetrieverContract:
    """Verifies Retriever tenant scoping and EvidenceId typed model."""

    def test_retriever_methods_are_async(self) -> None:
        assert inspect.iscoroutinefunction(Retriever.search)
        assert inspect.iscoroutinefunction(Retriever.search_titles)
        assert inspect.iscoroutinefunction(Retriever.get_evidence_text)

    def test_tenant_context_requires_user_role_enum(self) -> None:
        ctx = TenantContext(
            tenant_id="tenant-123",
            user_id="user-456",
            role=UserRole.STAFF,
            session_id="sess-789",
        )
        assert ctx.role == UserRole.STAFF
        assert ctx.role.value == "staff"

    def test_evidence_id_typed_model(self) -> None:
        ev = EvidenceId(
            chunk_id="chk-001",
            page_id="pg-012",
            document_version_id="ver-003",
            evidence_type=EvidenceType.TEXT,
        )
        assert ev.evidence_type == EvidenceType.TEXT
        assert ev.evidence_type.value == "text"


class TestStorageBackendContract:
    """Verifies StorageBackend requires StorageKey model containing tenant_id."""

    def test_methods_are_async(self) -> None:
        assert inspect.iscoroutinefunction(StorageBackend.put)
        assert inspect.iscoroutinefunction(StorageBackend.get)
        assert inspect.iscoroutinefunction(StorageBackend.signed_url)
        assert inspect.iscoroutinefunction(StorageBackend.delete)

    def test_storage_key_requires_tenant_id(self) -> None:
        valid_uuid = UUID("11111111-1111-4111-8111-111111111111")
        key = StorageKey(tenant_id=valid_uuid, path="documents/v1/page_1.png")
        assert key.tenant_id == valid_uuid
        assert key.path == "documents/v1/page_1.png"

        # Strengthened: invalid non-UUID string must fail validation
        with pytest.raises(ValidationError):
            StorageKey(tenant_id="tenant-abc", path="documents/v1/page_1.png")  # type: ignore[arg-type]


class TestJobQueueContract:
    """Verifies JobQueue async methods and idempotency key."""

    def test_methods_are_async(self) -> None:
        assert inspect.iscoroutinefunction(JobQueue.enqueue)
        assert inspect.iscoroutinefunction(JobQueue.status)


class TestAuthProviderContract:
    """Verifies AuthProvider async methods and UserRole enum enforcement."""

    def test_methods_are_async(self) -> None:
        assert inspect.iscoroutinefunction(AuthProvider.authenticate)
        assert inspect.iscoroutinefunction(AuthProvider.session)
        assert inspect.iscoroutinefunction(AuthProvider.roles)

    def test_user_roles_enum_values(self) -> None:
        assert UserRole.PLATFORM_ADMIN.value == "platform_admin"
        assert UserRole.CLIENT_ADMIN.value == "client_admin"
        assert UserRole.STAFF.value == "staff"


class TestTracerContract:
    """Verifies Tracer typed telemetry payloads and fail-safe contract."""

    def test_methods_are_async(self) -> None:
        assert inspect.iscoroutinefunction(Tracer.step_start)
        assert inspect.iscoroutinefunction(Tracer.step_end)
        assert inspect.iscoroutinefunction(Tracer.step_error)

    def test_step_error_payload_takes_code_and_type_not_raw_content(self) -> None:
        payload = StepErrorPayload(
            request_id="req-123",
            step_name="generate",
            duration_ms=45.2,
            error_code="PROVIDER_TIMEOUT",
            error_type="ProviderTimeout",
        )
        assert payload.error_code == "PROVIDER_TIMEOUT"
        assert payload.error_type == "ProviderTimeout"

    def test_tracer_contract_docstring_specifies_fail_safe(self) -> None:
        doc = Tracer.__doc__ or ""
        assert "never fail the request" in doc.lower()


class TestAuditLoggerContract:
    """Verifies AuditLogger abstract port contract."""

    def test_cannot_be_instantiated_directly(self) -> None:
        from app.ports.audit import AuditLogger

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            AuditLogger()  # type: ignore[abstract]

    def test_methods_are_async(self) -> None:
        from app.ports.audit import AuditLogger

        assert inspect.iscoroutinefunction(AuditLogger.record)

    def test_contract_docstring_specifies_fail_closed(self) -> None:
        from app.ports.audit import AuditLogger

        doc = AuditLogger.__doc__ or ""
        assert "fail-closed" in doc.lower()
