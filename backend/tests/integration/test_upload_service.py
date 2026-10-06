"""Integration tests for Document Upload Service and Deduplication (Task 2.2).

Tests tenant isolation, duplicate detection, concurrent upload races,
role authorization, storage ordering, zero-PHI audit logging against PostgreSQL,
and document existence verification.
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy import text as sa_text

from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.adapters.malware.mock import MockMalwareScanner
from app.adapters.storage.in_memory import InMemoryStorageBackend
from app.core.ingestion.upload import UploadService
from app.db.models import AuditEvent, Membership, Tenant, User
from app.db.session import tenant_session, unscoped_session
from app.observability.audit import AuditLogger as PostgresAuditLogger
from app.ports.audit import AuditLogger
from app.ports.errors import (
    AuthorizationError,
    DocumentNotFoundError,
    FileValidationError,
)
from app.ports.types import TenantContext, UploadPayload, UserRole


class MockAuditLogger(AuditLogger):
    """Mock audit logger recording compliance event metadata."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record(
        self,
        tenant_ctx: TenantContext,
        action: str,
        event_type: str,
        resource_id: str | None = None,
    ) -> UUID:
        event_id = uuid4()
        self.events.append(
            {
                "event_id": event_id,
                "tenant_id": tenant_ctx.tenant_id,
                "user_id": tenant_ctx.user_id,
                "action": action,
                "event_type": event_type,
                "resource_id": resource_id,
            }
        )
        return event_id


@pytest_asyncio.fixture
async def upload_test_env(test_user_password_hash: str) -> tuple[TenantContext, TenantContext]:
    """Seed test tenants with client_admin memberships in PostgreSQL."""
    t_a_id = uuid4()
    t_b_id = uuid4()
    u_a_id = uuid4()
    u_b_id = uuid4()

    async with unscoped_session() as session:
        session.add_all(
            [
                Tenant(id=t_a_id, name=f"Upload-Tenant-A-{t_a_id}"),
                Tenant(id=t_b_id, name=f"Upload-Tenant-B-{t_b_id}"),
                User(
                    id=u_a_id,
                    email=f"admin-a-{u_a_id}@example.com",
                    hashed_password=test_user_password_hash,
                    is_platform_admin=False,
                ),
                User(
                    id=u_b_id,
                    email=f"admin-b-{u_b_id}@example.com",
                    hashed_password=test_user_password_hash,
                    is_platform_admin=False,
                ),
            ]
        )
        await session.commit()

    ctx_a = TenantContext(
        tenant_id=str(t_a_id),
        user_id=str(u_a_id),
        role=UserRole.CLIENT_ADMIN,
        session_id=str(uuid4()),
    )
    ctx_b = TenantContext(
        tenant_id=str(t_b_id),
        user_id=str(u_b_id),
        role=UserRole.CLIENT_ADMIN,
        session_id=str(uuid4()),
    )

    async with tenant_session(ctx_a) as session_a:
        session_a.add(Membership(id=uuid4(), tenant_id=t_a_id, user_id=u_a_id, role="client_admin"))

    async with tenant_session(ctx_b) as session_b:
        session_b.add(Membership(id=uuid4(), tenant_id=t_b_id, user_id=u_b_id, role="client_admin"))

    return ctx_a, ctx_b


@pytest.mark.asyncio
async def test_upload_first_version_creates_document_and_version(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, _ = upload_test_env

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        audit_logger = MockAuditLogger()
        service = UploadService(repo, storage, scanner, audit_logger)

        pdf_content = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
        payload = UploadPayload(filename="policy.pdf", content=pdf_content)

        res = await service.upload_document(ctx_a, payload)

        assert not res.is_duplicate
        assert res.sha256 is not None
        assert res.document_id is not None
        assert res.document_version_id is not None

        # Verify version status starts as 'received' (NOT 'ready')
        ver = await repo.find_version_by_sha(ctx_a.tenant_id, res.sha256)
        assert ver is not None
        assert ver.status == "received"


@pytest.mark.asyncio
async def test_upload_duplicate_same_tenant_returns_existing_version(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, _ = upload_test_env

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        audit_logger = MockAuditLogger()
        service = UploadService(repo, storage, scanner, audit_logger)

        pdf_content = b"%PDF-1.4\nDuplicate content body\n%%EOF"
        payload1 = UploadPayload(filename="first.pdf", content=pdf_content)
        res1 = await service.upload_document(ctx_a, payload1)

        payload2 = UploadPayload(filename="second_upload.pdf", content=pdf_content)
        res2 = await service.upload_document(ctx_a, payload2)

        assert res2.is_duplicate
        assert res2.document_version_id == res1.document_version_id
        assert res2.document_id == res1.document_id

        # Assert duplicate upload writes an audit event with version ID and no filename
        assert len(audit_logger.events) == 2
        dup_event = audit_logger.events[1]
        assert dup_event.get("action") == "document_upload_duplicate"
        assert dup_event.get("resource_id") == str(res1.document_version_id)
        assert dup_event.get("event_type") == "INGESTION"
        dup_str = str(dup_event)
        assert "second_upload" not in dup_str
        assert "first.pdf" not in dup_str


@pytest.mark.asyncio
async def test_upload_duplicate_across_different_document_id(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, _ = upload_test_env

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        audit_logger = MockAuditLogger()
        service = UploadService(repo, storage, scanner, audit_logger)

        content = b"%PDF-1.4\nFile under Doc X\n%%EOF"
        res_x = await service.upload_document(
            ctx_a, UploadPayload(filename="doc_x.pdf", content=content)
        )

        # Create distinct document Y
        doc_y = await repo.create_document(ctx_a.tenant_id, title="Document Y")

        # Upload duplicate content targeting document Y
        res_y = await service.upload_document(
            ctx_a,
            UploadPayload(
                filename="duplicate_as_y.pdf",
                content=content,
                document_id=doc_y.id,
            ),
        )

        # Returns existing version and reports it belongs to document X
        assert res_y.is_duplicate
        assert res_y.document_version_id == res_x.document_version_id
        assert res_y.document_id == res_x.document_id
        assert res_y.existing_document_id == res_x.document_id


@pytest.mark.asyncio
async def test_upload_concurrent_duplicate_race_with_barrier(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, _ = upload_test_env

    barrier = asyncio.Barrier(2)

    async def race_hook() -> None:
        await barrier.wait()

    content = b"%PDF-1.4\nConcurrent race payload content\n%%EOF"
    storage = InMemoryStorageBackend()
    scanner = MockMalwareScanner()

    async def worker() -> Any:
        async with tenant_session(ctx_a) as sess:
            repo = PostgresDocumentRepository(sess)
            audit_logger = MockAuditLogger()
            svc = UploadService(
                repo,
                storage,
                scanner,
                audit_logger,
                after_duplicate_lookup_hook=race_hook,
            )
            return await svc.upload_document(
                ctx_a,
                UploadPayload(filename="race.pdf", content=content),
            )

    # Execute concurrent uploads holding both after duplicate lookup
    res1, res2 = await asyncio.gather(worker(), worker())

    # Exactly one version ID created, both calls return it
    assert res1.document_version_id == res2.document_version_id
    assert res1.sha256 == res2.sha256
    assert res1.document_id == res2.document_id

    # One winner, one duplicate
    assert (res1.is_duplicate and not res2.is_duplicate) or (
        res2.is_duplicate and not res1.is_duplicate
    )

    # Invariant: brand-new upload race rolls back the loser's document row
    # Exactly one document and one version exist in the database afterwards
    async with tenant_session(ctx_a) as sess:
        doc_count_res = await sess.execute(
            sa_text("SELECT COUNT(*) FROM documents WHERE tenant_id = :t_id"),
            {"t_id": UUID(ctx_a.tenant_id)},
        )
        doc_count = doc_count_res.scalar()

        ver_count_res = await sess.execute(
            sa_text("SELECT COUNT(*) FROM document_versions WHERE tenant_id = :t_id"),
            {"t_id": UUID(ctx_a.tenant_id)},
        )
        ver_count = ver_count_res.scalar()

    assert doc_count == 1
    assert ver_count == 1


@pytest.mark.asyncio
async def test_upload_storage_ordering_and_orphan_cleanup_on_failure(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, _ = upload_test_env

    async with tenant_session(ctx_a) as session:

        class VersionInsertFailure(Exception):
            pass

        class FailingRepo(PostgresDocumentRepository):
            async def create_document_with_version(self, *args: Any, **kwargs: Any) -> Any:
                del args, kwargs
                raise VersionInsertFailure("Database connection died during version insert")

        repo = FailingRepo(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        audit_logger = MockAuditLogger()
        service = UploadService(repo, storage, scanner, audit_logger)

        content = b"%PDF-1.4\nTest storage orphan cleanup\n%%EOF"
        with pytest.raises(VersionInsertFailure):
            await service.upload_document(
                ctx_a,
                UploadPayload(filename="orphan_test.pdf", content=content),
            )

        # Assert storage backend has 0 orphaned files left
        assert len(storage.files) == 0


@pytest.mark.asyncio
async def test_upload_cross_tenant_isolation_invariant_15(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, ctx_b = upload_test_env

    storage = InMemoryStorageBackend()
    scanner = MockMalwareScanner()
    identical_content = b"%PDF-1.4\nCross-tenant identical bytes\n%%EOF"

    # Tenant A uploads
    async with tenant_session(ctx_a) as session_a:
        repo_a = PostgresDocumentRepository(session_a)
        service_a = UploadService(repo_a, storage, scanner, MockAuditLogger())
        res_a = await service_a.upload_document(
            ctx_a,
            UploadPayload(filename="a.pdf", content=identical_content),
        )

    # Tenant B uploads the same file
    async with tenant_session(ctx_b) as session_b:
        repo_b = PostgresDocumentRepository(session_b)
        service_b = UploadService(repo_b, storage, scanner, MockAuditLogger())
        res_b = await service_b.upload_document(
            ctx_b,
            UploadPayload(filename="b.pdf", content=identical_content),
        )

    # Both succeed, neither is a duplicate of the other
    assert not res_a.is_duplicate
    assert not res_b.is_duplicate
    assert res_a.document_id != res_b.document_id
    assert res_a.document_version_id != res_b.document_version_id

    # Tenant B cannot see Tenant A's version
    async with tenant_session(ctx_b) as session_b:
        repo_b = PostgresDocumentRepository(session_b)
        ver_b_lookup = await repo_b.find_version_by_sha(ctx_b.tenant_id, res_a.sha256)
        assert ver_b_lookup is not None
        assert ver_b_lookup.id == res_b.document_version_id
        assert ver_b_lookup.id != res_a.document_version_id


@pytest.mark.asyncio
async def test_upload_role_denial_raises_authorization_error(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, _ = upload_test_env

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        audit_logger = MockAuditLogger()
        service = UploadService(repo, storage, scanner, audit_logger)
        content = b"%PDF-1.4\nStaff upload\n%%EOF"

        # Staff role denied
        staff_ctx = TenantContext(
            tenant_id=ctx_a.tenant_id,
            user_id=ctx_a.user_id,
            role=UserRole.STAFF,
            session_id=str(uuid4()),
        )
        with pytest.raises(AuthorizationError):
            await service.upload_document(
                staff_ctx,
                UploadPayload(filename="staff.pdf", content=content),
            )

        # Platform admin role denied (uploads belong strictly to client_admin)
        platform_ctx = TenantContext(
            tenant_id=ctx_a.tenant_id,
            user_id=ctx_a.user_id,
            role=UserRole.PLATFORM_ADMIN,
            session_id=str(uuid4()),
        )
        with pytest.raises(AuthorizationError):
            await service.upload_document(
                platform_ctx,
                UploadPayload(filename="plat.pdf", content=content),
            )


@pytest.mark.asyncio
async def test_upload_rejected_audited_with_zero_phi(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, _ = upload_test_env

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        audit_logger = MockAuditLogger()
        service = UploadService(repo, storage, scanner, audit_logger)

        bad_content = b"INVALID_GARBAGE_PAYLOAD"
        with pytest.raises(FileValidationError):
            await service.upload_document(
                ctx_a,
                UploadPayload(
                    filename="secret_patient_report.pdf",
                    content=bad_content,
                ),
            )

        # Audit logger should record rejection with code only
        assert len(audit_logger.events) == 1
        event = audit_logger.events[0]
        assert event.get("event_type") == "FILE_VALIDATION_ERROR"
        assert event.get("action") == "upload_rejected"
        # Verify ZERO PHI
        event_str = str(event)
        assert "secret_patient_report" not in event_str
        assert "INVALID_GARBAGE_PAYLOAD" not in event_str


@pytest.mark.asyncio
async def test_upload_new_version_to_existing_document_leaves_active_version_null(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, _ = upload_test_env

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        audit_logger = MockAuditLogger()
        service = UploadService(repo, storage, scanner, audit_logger)

        content_v1 = b"%PDF-1.4\nInitial document version 1\n%%EOF"
        res_v1 = await service.upload_document(
            ctx_a, UploadPayload(filename="handbook.pdf", content=content_v1)
        )
        assert res_v1.document_id is not None

        content_v2 = b"%PDF-1.4\nUpdated document version 2\n%%EOF"
        res_v2 = await service.upload_document(
            ctx_a,
            UploadPayload(
                filename="handbook_revised.pdf",
                content=content_v2,
                document_id=res_v1.document_id,
            ),
        )

        assert res_v2.document_id == res_v1.document_id
        assert res_v2.document_version_id != res_v1.document_version_id
        assert not res_v2.is_duplicate

        # active_version_id must stay None (set only at pipeline ready state)
        doc = await repo.get_document(ctx_a.tenant_id, res_v1.document_id)
        assert doc is not None
        assert doc.active_version_id is None


@pytest.mark.asyncio
async def test_tenant_a_cannot_read_or_match_tenant_b_versions_through_repository(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, ctx_b = upload_test_env
    storage = InMemoryStorageBackend()
    scanner = MockMalwareScanner()
    content_b = b"%PDF-1.4\nConfidential tenant B records\n%%EOF"

    # Tenant B uploads version
    async with tenant_session(ctx_b) as session_b:
        repo_b = PostgresDocumentRepository(session_b)
        service_b = UploadService(repo_b, storage, scanner, MockAuditLogger())
        res_b = await service_b.upload_document(
            ctx_b, UploadPayload(filename="tenant_b.pdf", content=content_b)
        )

    # Tenant A attempts to find or match Tenant B's version
    async with tenant_session(ctx_a) as session_a:
        repo_a = PostgresDocumentRepository(session_a)

        # Lookup by tenant A ID returns None
        lookup_a = await repo_a.find_version_by_sha(ctx_a.tenant_id, res_b.sha256)
        assert lookup_a is None

        # Cross-tenant query specifying tenant B ID returns None (RLS block)
        lookup_b_cross = await repo_a.find_version_by_sha(ctx_b.tenant_id, res_b.sha256)
        assert lookup_b_cross is None


@pytest.mark.asyncio
async def test_normal_duplicate_upload_never_calls_storage_put(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, _ = upload_test_env

    class SpyStorage(InMemoryStorageBackend):
        def __init__(self) -> None:
            super().__init__()
            self.put_call_count = 0

        async def put(self, *args: Any, **kwargs: Any) -> str:
            self.put_call_count += 1
            return await super().put(*args, **kwargs)

    spy_storage = SpyStorage()
    scanner = MockMalwareScanner()
    audit_logger = MockAuditLogger()

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        service = UploadService(repo, spy_storage, scanner, audit_logger)

        content = b"%PDF-1.4\nDuplicate avoidance test content\n%%EOF"
        payload = UploadPayload(filename="doc.pdf", content=content)

        # Initial upload writes to storage once
        res1 = await service.upload_document(ctx_a, payload)
        assert not res1.is_duplicate
        assert spy_storage.put_call_count == 1

        # Duplicate upload finds version early and must NOT write to storage
        res2 = await service.upload_document(ctx_a, payload)
        assert res2.is_duplicate
        assert spy_storage.put_call_count == 1


@pytest.mark.asyncio
async def test_upload_audit_failure_is_fail_closed_and_cleans_storage(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    ctx_a, _ = upload_test_env

    class FailingAuditLogger(AuditLogger):
        async def record(
            self,
            tenant_ctx: TenantContext,
            action: str,
            event_type: str,
            resource_id: str | None = None,
        ) -> UUID:
            del tenant_ctx, action, event_type, resource_id
            raise RuntimeError("Audit log store unavailable")

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        audit_logger = FailingAuditLogger()
        service = UploadService(repo, storage, scanner, audit_logger)

        content = b"%PDF-1.4\nAudit fail closed payload\n%%EOF"
        with pytest.raises(RuntimeError, match="Audit log store unavailable"):
            await service.upload_document(
                ctx_a,
                UploadPayload(filename="audit_fail.pdf", content=content),
            )

        # Storage file must be deleted (fail-closed, zero orphans)
        assert len(storage.files) == 0


@pytest.mark.asyncio
async def test_upload_success_writes_row_to_real_postgres_audit_store_with_zero_filename(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    """Audit test using the real Phase 1 Postgres audit store."""
    ctx_a, _ = upload_test_env

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        real_audit_logger = PostgresAuditLogger()
        service = UploadService(repo, storage, scanner, real_audit_logger)

        sensitive_filename = "Confidential_Patient_Diagnoses_2026.pdf"
        pdf_content = b"%PDF-1.4\nReal Postgres audit test content\n%%EOF"
        payload = UploadPayload(filename=sensitive_filename, content=pdf_content)

        res = await service.upload_document(ctx_a, payload)

        assert not res.is_duplicate
        assert res.document_version_id is not None

        # Verify audit_events row persisted in PostgreSQL
        audit_stmt = select(AuditEvent).where(
            AuditEvent.tenant_id == UUID(ctx_a.tenant_id),
            AuditEvent.action == "document_uploaded",
            AuditEvent.resource_id == str(res.document_version_id),
        )
        audit_res = await session.execute(audit_stmt)
        audit_event = audit_res.scalar_one_or_none()

        assert audit_event is not None
        assert audit_event.event_type == "INGESTION"
        assert audit_event.action == "document_uploaded"
        assert str(audit_event.user_id) == ctx_a.user_id

        # Invariant: filename and raw content must never appear in audit_events table
        row_str = f"{audit_event.action} {audit_event.event_type} {audit_event.resource_id}"
        assert sensitive_filename not in row_str
        assert "Patient_Diagnoses" not in row_str
        assert "Real Postgres audit test content" not in row_str


@pytest.mark.asyncio
async def test_upload_with_random_unknown_document_id_raises_document_not_found(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    """Document ID not found test: random unknown id raises DocumentNotFoundError."""
    ctx_a, _ = upload_test_env

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        audit_logger = MockAuditLogger()
        service = UploadService(repo, storage, scanner, audit_logger)

        unknown_doc_id = uuid4()
        content = b"%PDF-1.4\nUnknown doc id test\n%%EOF"
        payload = UploadPayload(
            filename="unknown.pdf",
            content=content,
            document_id=unknown_doc_id,
        )

        with pytest.raises(DocumentNotFoundError):
            await service.upload_document(ctx_a, payload)

        # No storage file left behind
        assert len(storage.files) == 0

        # No document or version rows created
        doc = await repo.get_document(ctx_a.tenant_id, unknown_doc_id)
        assert doc is None
        doc_count_res = await session.execute(
            sa_text("SELECT COUNT(*) FROM documents WHERE tenant_id = :t_id"),
            {"t_id": UUID(ctx_a.tenant_id)},
        )
        assert doc_count_res.scalar() == 0


@pytest.mark.asyncio
async def test_upload_tenant_a_targeting_tenant_b_document_id_raises_document_not_found(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    """Tenant B's real document id used by Tenant A raises DocumentNotFoundError."""
    ctx_a, ctx_b = upload_test_env
    storage = InMemoryStorageBackend()
    scanner = MockMalwareScanner()

    # Tenant B creates a valid document
    async with tenant_session(ctx_b) as session_b:
        repo_b = PostgresDocumentRepository(session_b)
        service_b = UploadService(repo_b, storage, scanner, MockAuditLogger())
        content_b = b"%PDF-1.4\nTenant B document\n%%EOF"
        res_b = await service_b.upload_document(
            ctx_b, UploadPayload(filename="b.pdf", content=content_b)
        )
        target_doc_b_id = res_b.document_id

    # Tenant A attempts to upload targeting Tenant B's real document ID
    async with tenant_session(ctx_a) as session_a:
        repo_a = PostgresDocumentRepository(session_a)
        service_a = UploadService(repo_a, storage, scanner, MockAuditLogger())
        content_a = b"%PDF-1.4\nTenant A spoofing B\n%%EOF"
        payload_a = UploadPayload(
            filename="spoof.pdf",
            content=content_a,
            document_id=target_doc_b_id,
        )

        with pytest.raises(DocumentNotFoundError):
            await service_a.upload_document(ctx_a, payload_a)

        # No storage file left behind for tenant A's upload
        # Only tenant B's original file remains in storage
        assert len(storage.files) == 1
        stored_key = next(iter(storage.files.keys()))
        assert stored_key.startswith(ctx_b.tenant_id)

        # No documents or versions created under Tenant A
        doc_count_a = await session_a.execute(
            sa_text("SELECT COUNT(*) FROM documents WHERE tenant_id = :t_id"),
            {"t_id": UUID(ctx_a.tenant_id)},
        )
        assert doc_count_a.scalar() == 0


@pytest.mark.asyncio
async def test_upload_duplicate_writes_audit_event_to_postgres_without_filename(
    upload_test_env: tuple[TenantContext, TenantContext],
) -> None:
    """Duplicate upload writes document_upload_duplicate row to real Postgres audit store."""
    ctx_a, _ = upload_test_env

    async with tenant_session(ctx_a) as session:
        repo = PostgresDocumentRepository(session)
        storage = InMemoryStorageBackend()
        scanner = MockMalwareScanner()
        real_audit_logger = PostgresAuditLogger()
        service = UploadService(repo, storage, scanner, real_audit_logger)

        content = b"%PDF-1.4\nDuplicate real audit test content\n%%EOF"
        first_filename = "patient_file_alpha.pdf"
        second_filename = "patient_file_duplicate.pdf"

        res1 = await service.upload_document(
            ctx_a, UploadPayload(filename=first_filename, content=content)
        )
        res2 = await service.upload_document(
            ctx_a, UploadPayload(filename=second_filename, content=content)
        )

        assert res2.is_duplicate
        assert res2.document_version_id == res1.document_version_id

        # Verify duplicate audit row in PostgreSQL
        audit_stmt = select(AuditEvent).where(
            AuditEvent.tenant_id == UUID(ctx_a.tenant_id),
            AuditEvent.action == "document_upload_duplicate",
            AuditEvent.resource_id == str(res1.document_version_id),
        )
        audit_res = await session.execute(audit_stmt)
        dup_event = audit_res.scalar_one_or_none()

        assert dup_event is not None
        assert dup_event.event_type == "INGESTION"
        assert dup_event.action == "document_upload_duplicate"
        assert str(dup_event.user_id) == ctx_a.user_id

        # Invariant: zero filename or content leakage
        row_str = f"{dup_event.action} {dup_event.event_type} {dup_event.resource_id}"
        assert first_filename not in row_str
        assert second_filename not in row_str
        assert "Duplicate real audit test content" not in row_str
