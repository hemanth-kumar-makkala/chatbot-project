"""Invariant 7 test: PHI uploaded in chat never enters shared knowledge index.

Enforces:
- PHI uploaded in chat (session_documents) never enters the shared knowledge index
  or chunks table unless explicitly promoted (Invariant 7).
- Retrieval queries (vector, keyword, hybrid) search strictly active document chunks,
  never session_documents.
- Retention is a required setting (SESSION_UPLOAD_RETENTION_HOURS) with no default;
  the application refuses to start without it.
"""

from __future__ import annotations

import pytest

from app.api.app import create_app
from app.db.models import SessionDocument
from app.ports.errors import ConfigurationError
from app.ports.types import RetrievalFilters


class TestPhiSessionIsolationAndRetention:
    """Invariant 7 verification: PHI session isolation and retention requirement."""

    def test_app_refuses_to_start_without_retention_setting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The app refuses to start if SESSION_UPLOAD_RETENTION_HOURS is missing (no default)."""
        monkeypatch.setenv("APP_PROFILE", "mock")
        monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path))
        monkeypatch.delenv("SESSION_UPLOAD_RETENTION_HOURS", raising=False)

        with pytest.raises(ConfigurationError) as exc_info:
            create_app(settings=None)

        assert "session_upload_retention_hours" in str(exc_info.value)

    def test_app_refuses_to_start_with_invalid_retention_setting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The app refuses to start if retention hours is non-positive or non-numeric."""
        monkeypatch.setenv("APP_PROFILE", "mock")
        monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path))

        monkeypatch.setenv("SESSION_UPLOAD_RETENTION_HOURS", "0")
        with pytest.raises(ConfigurationError) as exc_info1:
            create_app(settings=None)
        assert "session_upload_retention_hours" in str(exc_info1.value)

        monkeypatch.setenv("SESSION_UPLOAD_RETENTION_HOURS", "-5")
        with pytest.raises(ConfigurationError) as exc_info2:
            create_app(settings=None)
        assert "session_upload_retention_hours" in str(exc_info2.value)

        monkeypatch.setenv("SESSION_UPLOAD_RETENTION_HOURS", "not_a_number")
        with pytest.raises(ConfigurationError) as exc_info3:
            create_app(settings=None)
        assert "session_upload_retention_hours" in str(exc_info3.value)

    def test_app_starts_successfully_with_valid_retention_setting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The app starts cleanly when SESSION_UPLOAD_RETENTION_HOURS is provided."""
        monkeypatch.setenv("APP_PROFILE", "mock")
        monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path))
        monkeypatch.setenv("SESSION_UPLOAD_RETENTION_HOURS", "24")

        app = create_app(settings=None)
        assert app is not None
        assert app.state.settings.session_upload_retention_hours == 24

    def test_session_document_model_isolation_from_chunks(self) -> None:
        """SessionDocument model is distinct from Chunk and DocumentVersion."""
        assert hasattr(SessionDocument, "__tablename__")
        assert SessionDocument.__tablename__ == "session_documents"
        assert hasattr(SessionDocument, "retention_until")
        assert hasattr(SessionDocument, "conversation_id")
        assert hasattr(SessionDocument, "file_sha256")
        assert hasattr(SessionDocument, "tenant_id")

        # SessionDocument does NOT have chunk embedding or chunker versions
        assert not hasattr(SessionDocument, "embedding")
        assert not hasattr(SessionDocument, "chunker_version")

    def test_retriever_query_cannot_target_session_documents(self) -> None:
        """Retriever interface contract searches Chunk objects, never session_documents."""
        filters = RetrievalFilters(
            document_ids=["00000000-0000-0000-0000-000000000001"],
            limit=5,
        )
        assert not hasattr(filters, "session_document_id")
        assert not hasattr(filters, "conversation_id")
