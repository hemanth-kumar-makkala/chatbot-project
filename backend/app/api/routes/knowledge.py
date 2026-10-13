"""Knowledge page administration endpoints (Task 2.8a).

Provides:
- POST /v1/knowledge/upload: client_admin upload with memory-only streaming,
  size cap enforcement, anti-probing replace checks, and Procrastinate split job dispatch.
- GET /v1/knowledge/documents: status list returning derived UI states
  (Queued, Reading, Scanning images, Ready, Ready (N pages need review)).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.config import ApiSettings
from app.api.deps import (
    get_api_settings,
    get_client_admin_tenant_context_for_assistant,
    validate_csrf,
)
from app.db.models import AuditEvent, Document, Page
from app.db.session import tenant_session
from app.ports.errors import (
    AuthorizationError,
    DocumentNotFoundError,
    FileValidationError,
    MalwareDetectedError,
)
from app.ports.types import ReviewPageInfo, StorageKey, TenantContext, UploadPayload
from app.wiring import (
    create_document_repository,
    create_storage_backend,
    create_upload_service,
)
from app.worker import app as worker_app
from app.worker import split_document_job

router = APIRouter(prefix="/v1/assistants/{assistant_id}/knowledge", tags=["knowledge"])


class UploadResponse(BaseModel):
    """Response returned upon successful upload acceptance.

    Invariant 10: Holds IDs and status only, never filenames or content.
    """

    document_id: str
    document_version_id: str
    status: str
    is_duplicate: bool


class VersionReviewResponse(BaseModel):
    """Response returning flagged pages and readings for administrative review inspection."""

    version_id: str
    flagged_pages: list[ReviewPageInfo]


class RetireDocumentResponse(BaseModel):
    """Response returning retired document ID and status."""

    document_id: str
    is_retired: bool
    status: str


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(validate_csrf)],
)
async def upload_document(
    request: Request,
    tenant: Annotated[TenantContext, Depends(get_client_admin_tenant_context_for_assistant)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> UploadResponse:
    """Upload document for knowledge base ingestion.

    - Memory-only streaming: reads chunks up to max_upload_bytes; never writes
      to Starlette spooled temporary disk files.
    - Anti-probing: if replacing an existing document_id, verifies tenant ownership;
      returns 404 Not Found if missing or cross-tenant.
    - Tenant isolation: opens tenant_session(tenant) directly.
    - Enqueues split job with IDs only.
    """
    # 1. Stream content strictly in memory up to cap
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > settings.max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="File size exceeds upload limit",
            )

    filename = (
        request.headers.get("X-Filename") or request.query_params.get("filename") or "document.pdf"
    )

    # 2. Check replacement target document_id with anti-probing protection
    target_doc_id: UUID | None = None
    doc_id_param = request.query_params.get("document_id")
    if doc_id_param:
        try:
            target_doc_id = UUID(doc_id_param)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            ) from None

        # Verify document belongs to caller tenant
        async with tenant_session(tenant) as check_session:
            stmt = select(Document.id).where(
                Document.tenant_id == UUID(tenant.tenant_id),
                Document.id == target_doc_id,
            )
            exists = (await check_session.execute(stmt)).scalar_one_or_none()
            if exists is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found",
                )

    # 3. Process upload inside tenant_session
    payload = UploadPayload(
        filename=filename,
        content=bytes(buffer),
        document_id=target_doc_id,
    )

    profile = getattr(settings, "profile", "mock")
    storage_dir = getattr(settings, "storage_dir", "storage")

    try:
        async with tenant_session(tenant) as session:
            upload_svc = create_upload_service(
                session=session,
                profile=profile,
                storage_dir=storage_dir,
            )
            result = await upload_svc.upload_document(tenant, payload)

            # Record compliance audit event for upload
            audit = AuditEvent(
                tenant_id=UUID(tenant.tenant_id),
                user_id=UUID(tenant.user_id),
                event_type="knowledge_compliance",
                action="document_uploaded",
                resource_id=str(result.document_version_id),
            )
            session.add(audit)
            await session.flush()

    except AuthorizationError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client admin role required",
        ) from None
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        ) from None
    except FileValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.message,
        ) from None
    except MalwareDetectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.message,
        ) from None

    # 4. Enqueue background split job with IDs only (if not duplicate)
    if not result.is_duplicate:
        try:
            await split_document_job.defer_async(
                tenant_id=tenant.tenant_id,
                document_version_id=str(result.document_version_id),
            )
        except Exception:
            async with worker_app.open_async():
                await split_document_job.defer_async(
                    tenant_id=tenant.tenant_id,
                    document_version_id=str(result.document_version_id),
                )

    return UploadResponse(
        document_id=str(result.document_id),
        document_version_id=str(result.document_version_id),
        status="queued" if not result.is_duplicate else "duplicate",
        is_duplicate=result.is_duplicate,
    )


@router.get("/documents")
async def list_documents(
    tenant: Annotated[TenantContext, Depends(get_client_admin_tenant_context_for_assistant)],
) -> dict[str, list[dict[str, Any]]]:
    """List documents and versions for tenant with derived UI states.

    Audit Rule: Zero audit events logged on status list polling.
    """
    async with tenant_session(tenant) as session:
        repo = create_document_repository(session)
        doc_infos = await repo.list_documents_with_status(tenant.tenant_id)

    return {"documents": [doc.model_dump() for doc in doc_infos]}


@router.get("/documents/{version_id}/review", response_model=VersionReviewResponse)
async def get_version_review(
    version_id: UUID,
    tenant: Annotated[TenantContext, Depends(get_client_admin_tenant_context_for_assistant)],
) -> VersionReviewResponse:
    """Retrieve flagged pages with review reasons and readings for version.

    Logs compliance audit event (action="review_inspected").
    """
    async with tenant_session(tenant) as session:
        repo = create_document_repository(session)
        try:
            pages = await repo.get_version_review_pages(tenant.tenant_id, version_id)
        except DocumentNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document version not found",
            ) from None

        audit = AuditEvent(
            tenant_id=UUID(tenant.tenant_id),
            user_id=UUID(tenant.user_id),
            event_type="knowledge_compliance",
            action="review_inspected",
            resource_id=str(version_id),
        )
        session.add(audit)
        await session.flush()

    return VersionReviewResponse(
        version_id=str(version_id),
        flagged_pages=pages,
    )


@router.get("/documents/{version_id}/pages/{page_number}/image")
async def get_page_image(
    version_id: UUID,
    page_number: int,
    tenant: Annotated[TenantContext, Depends(get_client_admin_tenant_context_for_assistant)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> Response:
    """Safely proxy page image bytes via storage port without public static path.

    Headers: Content-Type: image/png, Cache-Control: no-store,
             X-Content-Type-Options: nosniff, Content-Security-Policy: default-src 'none'.
    Logs compliance audit event (action="page_image_viewed").
    """
    tid = UUID(tenant.tenant_id)
    profile = getattr(settings, "profile", "mock")
    storage_dir = getattr(settings, "storage_dir", "storage")

    async with tenant_session(tenant) as session:
        page_stmt = select(Page).where(
            Page.tenant_id == tid,
            Page.document_version_id == version_id,
            Page.page_number == page_number,
        )
        page = (await session.execute(page_stmt)).scalars().first()
        if page is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Page not found",
            )

        storage = create_storage_backend(profile=profile, base_dir=storage_dir)
        path = page.image_key
        if path.startswith(f"{tenant.tenant_id}/"):
            path = path[len(f"{tenant.tenant_id}/") :]
        key = StorageKey(tenant_id=tid, path=path)

        try:
            image_bytes = await storage.get(key)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Page image not found in storage",
            ) from None

        audit = AuditEvent(
            tenant_id=tid,
            user_id=UUID(tenant.user_id),
            event_type="knowledge_compliance",
            action="page_image_viewed",
            resource_id=f"{version_id}:{page_number}",
        )
        session.add(audit)
        await session.flush()

    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


@router.delete(
    "/documents/{document_id}",
    response_model=RetireDocumentResponse,
    dependencies=[Depends(validate_csrf)],
)
async def retire_document(
    document_id: UUID,
    tenant: Annotated[TenantContext, Depends(get_client_admin_tenant_context_for_assistant)],
) -> RetireDocumentResponse:
    """Retire document, clearing active version and deactivating search chunks under row lock.

    Requires CSRF token on DELETE.
    Logs compliance audit event (action="document_retired").
    """
    async with tenant_session(tenant) as session:
        repo = create_document_repository(session)
        try:
            doc = await repo.retire_document(tenant.tenant_id, document_id)
        except DocumentNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            ) from None

        audit = AuditEvent(
            tenant_id=UUID(tenant.tenant_id),
            user_id=UUID(tenant.user_id),
            event_type="knowledge_compliance",
            action="document_retired",
            resource_id=str(document_id),
        )
        session.add(audit)
        await session.flush()

    return RetireDocumentResponse(
        document_id=str(doc.id),
        is_retired=doc.is_retired,
        status="retired",
    )
