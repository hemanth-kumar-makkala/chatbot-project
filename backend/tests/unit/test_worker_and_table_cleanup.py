"""Unit tests for Part B cleanups (worker profile, types, table extensions, missing version)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.ingestion.table_service import TableIngestionService
from app.ports.document_splitter import DocumentSplitter
from app.ports.errors import (
    ConfigurationError,
    DocumentVersionNotFoundError,
    FileValidationError,
)
from app.wiring import create_document_splitter
from app.worker import _get_worker_profile, run_split_document_job


def test_missing_app_profile_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing or empty APP_PROFILE raises ConfigurationError with fixed message."""
    monkeypatch.delenv("APP_PROFILE", raising=False)
    with pytest.raises(ConfigurationError) as exc_info:
        _get_worker_profile()
    assert str(exc_info.value) == "APP_PROFILE environment variable is not set"

    monkeypatch.setenv("APP_PROFILE", "   ")
    with pytest.raises(ConfigurationError) as exc_info_empty:
        _get_worker_profile()
    assert str(exc_info_empty.value) == "APP_PROFILE environment variable is not set"


def test_valid_app_profile_returns_stripped_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid APP_PROFILE returns stripped profile string."""
    monkeypatch.setenv("APP_PROFILE", "mock")
    assert _get_worker_profile() == "mock"


def test_create_document_splitter_returns_concrete_type() -> None:
    """create_document_splitter returns a DocumentSplitter instance."""
    splitter = create_document_splitter()
    assert isinstance(splitter, DocumentSplitter)


@pytest.mark.asyncio
async def test_ingest_table_file_unsupported_extension_raises_typed_error() -> None:
    """ingest_table_file raises FileValidationError on any extension other than csv or xlsx."""
    mock_repo = MagicMock()
    mock_extractor = MagicMock()
    service = TableIngestionService(repository=mock_repo, table_extractor=mock_extractor)

    with pytest.raises(FileValidationError) as exc_exe:
        await service.ingest_table_file(
            tenant_id=uuid4(),
            document_version_id=uuid4(),
            file=b"fake bytes",
            extension="exe",
        )
    assert "Unsupported table file extension: exe" in str(exc_exe.value)

    with pytest.raises(FileValidationError) as exc_pdf:
        await service.ingest_table_file(
            tenant_id=uuid4(),
            document_version_id=uuid4(),
            file=b"fake bytes",
            extension="pdf",
        )
    assert "Unsupported table file extension: pdf" in str(exc_pdf.value)


@pytest.mark.asyncio
async def test_run_split_document_job_missing_version_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_split_document_job raises DocumentVersionNotFoundError when version does not exist."""
    tid = str(uuid4())
    vid = str(uuid4())

    mock_context = MagicMock()
    mock_context.job.id = 1

    # Mock session returning None for DocumentVersion lookup
    mock_session = AsyncMock()
    mock_exec = AsyncMock()
    mock_exec.scalars.return_value.first.return_value = None
    mock_session.execute.return_value = mock_exec

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _mock_worker_session(_worker_ctx):
        yield mock_session

    monkeypatch.setattr("app.worker.worker_tenant_session", _mock_worker_session)
    monkeypatch.setenv("APP_PROFILE", "mock")

    with pytest.raises(DocumentVersionNotFoundError) as exc_info:
        await run_split_document_job(
            context=mock_context,
            tenant_id=tid,
            document_version_id=vid,
        )

    assert f"Document version {vid} not found in tenant {tid}" in str(exc_info.value)
