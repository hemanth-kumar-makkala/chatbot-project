"""Version activation service with concurrency serialization and review policy branching.

Implements:
- Concurrency serialization via DocumentRepository locking to prevent completion race.
- MECE activation policy:
  - Branch A: Previous active version exists AND new version has flagged pages -> keep previous,
    hold new version in 'needs_review', chunks stay inactive.
  - Branch B: First version of document AND new version has flagged pages -> activate version,
    status 'needs_review', unflagged chunks active, flagged chunks inactive (held out of index).
  - Branch C: All pages clean -> activate document version, status 'ready', all chunks active,
    retire previous version (status 'retired', chunks inactive).
- Single transaction guarantee flipping active_version_id and chunks.is_active.
- Zero direct database or ORM dependencies (ADR-0003 and boundary checker).
"""

from __future__ import annotations

from uuid import UUID

from app.ports.document_repository import DocumentRepository
from app.ports.errors import DocumentRetiredError


class VersionActivationService:
    """Orchestrates atomic document version activation and review policy branching."""

    def __init__(self, repository: DocumentRepository) -> None:
        self.repository = repository

    async def check_and_activate_if_complete(
        self,
        tenant_id: str,
        document_version_id: UUID,
    ) -> bool:
        """Lock version row, check if all pages are finished, and trigger activation.

        Returns True if this invocation was the final finisher and triggered activation;
        returns False if unfinished pages remain.
        """
        (
            doc,
            ver,
            total_pages,
            completed_pages,
            has_flagged,
        ) = await self.repository.lock_version_for_activation(tenant_id, document_version_id)
        if doc.is_retired:
            return False

        if ver.status != "processing":
            return False

        if total_pages == 0 or completed_pages < total_pages:
            return False

        has_prior_active = (
            doc.active_version_id is not None and doc.active_version_id != document_version_id
        )
        if has_prior_active and has_flagged:
            branch = "Branch A"
        elif not has_prior_active and has_flagged:
            branch = "Branch B"
        else:
            branch = "Branch C"

        await self.repository.execute_version_activation(tenant_id, document_version_id, branch)
        return True

    async def activate_version(
        self,
        tenant_id: str,
        document_version_id: UUID,
    ) -> str:
        """Execute activation branching under row lock.

        Returns the assigned status of the target version ('ready' or 'needs_review').
        """
        (
            doc,
            ver,
            total_pages,
            completed_pages,
            has_flagged,
        ) = await self.repository.lock_version_for_activation(tenant_id, document_version_id)
        if doc.is_retired:
            raise DocumentRetiredError(f"Cannot activate version for retired document {doc.id}")
        has_prior_active = (
            doc.active_version_id is not None and doc.active_version_id != document_version_id
        )
        if has_prior_active and has_flagged:
            branch = "Branch A"
        elif not has_prior_active and has_flagged:
            branch = "Branch B"
        else:
            branch = "Branch C"

        return await self.repository.execute_version_activation(
            tenant_id, document_version_id, branch
        )
