"""Document upload service (Task 2.2).

Orchestrates file validation, role-based authorization, deduplication lookup,
atomic storage persistence, and document/version metadata recording.
Zero direct database or ORM dependencies (enforced by ADR-0003 and boundary checker).
"""

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from app.core.ingestion.validation import validate_file
from app.ports.audit import AuditLogger
from app.ports.document_repository import DocumentExtension, DocumentRepository
from app.ports.errors import (
    AuthorizationError,
    FileValidationError,
    MalwareDetectedError,
)
from app.ports.malware_scanner import MalwareScanner
from app.ports.storage import StorageBackend
from app.ports.types import (
    StorageKey,
    TenantContext,
    UploadPayload,
    UploadResult,
    UserRole,
)


class UploadService:
    """Orchestrates tenant-scoped document upload, validation, deduplication, and storage."""

    def __init__(
        self,
        document_repo: DocumentRepository,
        storage: StorageBackend,
        malware_scanner: MalwareScanner,
        audit_logger: AuditLogger,
        after_duplicate_lookup_hook: Callable[[], Awaitable[None]] | None = None,
        before_lock_hook: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.document_repo = document_repo
        self.storage = storage
        self.malware_scanner = malware_scanner
        self.audit_logger = audit_logger
        self.after_duplicate_lookup_hook = after_duplicate_lookup_hook
        self.before_lock_hook = before_lock_hook

    async def upload_document(
        self,
        tenant: TenantContext,
        payload: UploadPayload,
    ) -> UploadResult:
        """Process document upload within authenticated tenant scope.

        Raises:
            AuthorizationError: If user role is not client_admin.
            FileValidationError: If content sniffing, encoding, or format validation fails.
            MalwareDetectedError: If payload fails malware scanning.
            DocumentNotFoundError: If payload.document_id is supplied but not found in tenant.
        """
        # 1. Role Authorization (strictly client_admin only)
        if tenant.role != UserRole.CLIENT_ADMIN:
            await self.audit_logger.record(
                tenant_ctx=tenant,
                action="upload_rejected",
                event_type="AUTHORIZATION_ERROR",
            )
            raise AuthorizationError("Only client_admin can upload knowledge documents")

        # 2. File Validation & Sniffing
        try:
            validated = await validate_file(
                payload.filename,
                payload.content,
                malware_scanner=self.malware_scanner,
            )
        except (FileValidationError, MalwareDetectedError) as exc:
            await self.audit_logger.record(
                tenant_ctx=tenant,
                action="upload_rejected",
                event_type=exc.error_code,
            )
            raise

        # 3. Deduplication Lookup within Tenant Scope (serialized via advisory lock)
        if self.before_lock_hook is not None:
            await self.before_lock_hook()

        await self.document_repo.acquire_upload_lock(tenant.tenant_id, validated.sha256)

        existing_version = await self.document_repo.find_version_by_sha(
            tenant.tenant_id, validated.sha256, active_only=True
        )

        if self.after_duplicate_lookup_hook is not None:
            await self.after_duplicate_lookup_hook()

        if existing_version is not None:
            # Normal duplicate: DO NOT write to storage at all
            existing_doc = await self.document_repo.get_document(
                tenant.tenant_id, existing_version.document_id
            )
            title = existing_doc.title if existing_doc else ""
            await self.audit_logger.record(
                tenant_ctx=tenant,
                action="document_upload_duplicate",
                event_type="INGESTION",
                resource_id=str(existing_version.id),
            )
            return UploadResult(
                document_id=existing_version.document_id,
                document_version_id=existing_version.id,
                sha256=validated.sha256,
                title=title,
                is_duplicate=True,
                existing_document_id=existing_version.document_id,
            )

        # 4. Storage Write before DB Insert (prevent orphaned DB records)
        version_id = uuid4()
        storage_key = StorageKey(
            tenant_id=UUID(str(tenant.tenant_id)),
            path=f"documents/{version_id}/original.{validated.extension}",
        )
        await self.storage.put(
            storage_key,
            validated.content,
            content_type=validated.mime_type,
        )

        # 5. Database Persistence
        try:
            file_ext: DocumentExtension
            if validated.extension == "pdf":
                file_ext = "pdf"
            elif validated.extension == "png":
                file_ext = "png"
            elif validated.extension == "jpeg":
                file_ext = "jpeg"
            elif validated.extension == "tiff":
                file_ext = "tiff"
            elif validated.extension == "xlsx":
                file_ext = "xlsx"
            elif validated.extension == "csv":
                file_ext = "csv"
            elif validated.extension == "txt":
                file_ext = "txt"
            else:
                raise FileValidationError(f"Unsupported file extension: {validated.extension}")

            doc, ver = await self.document_repo.create_document_with_version(
                tenant_id=tenant.tenant_id,
                title=payload.filename,
                sha256=validated.sha256,
                uploaded_by=tenant.user_id,
                file_extension=file_ext,
                status="received",
                document_id=payload.document_id,
                version_id=version_id,
            )

            # 6. Audit Logging (Fail-Closed: failure aborts upload and deletes stored file)
            await self.audit_logger.record(
                tenant_ctx=tenant,
                action="document_uploaded",
                event_type="INGESTION",
                resource_id=str(ver.id),
            )

            return UploadResult(
                document_id=doc.id,
                document_version_id=ver.id,
                sha256=validated.sha256,
                title=doc.title,
                is_duplicate=False,
            )

        except Exception:
            # Failed insert, audit write, or not found: clean up storage file and re-raise
            await self.storage.delete(storage_key)
            raise
