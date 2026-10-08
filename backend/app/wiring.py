"""Composition root for the Client Knowledge Assistant application.

This is the ONLY module permitted by architecture boundary enforcement rules
to import from both app.core.* and app.adapters.*.

Provides clean factory functions for constructing the application graph
with specified ports and checkpointer without monkeypatching or test-only overrides.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
)
from app.core.ingestion.upload import UploadService
from app.ports.audit import AuditLogger
from app.ports.chunker import Chunker
from app.ports.document_parser import DocumentParser
from app.ports.document_repository import DocumentRepository
from app.ports.embedding_provider import EmbeddingProvider
from app.ports.malware_scanner import MalwareScanner
from app.ports.model_provider import ModelProvider
from app.ports.retriever import Retriever
from app.ports.storage import StorageBackend
from app.ports.table_extractor import TableExtractor
from app.ports.tracer import Tracer
from app.ports.types import TenantContext


def build_app_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph[GraphState, GraphRuntimeContext, Any, Any]:
    """Build and compile the core knowledge graph for the application."""
    builder = build_knowledge_graph()
    return builder.compile(checkpointer=checkpointer)


def create_production_ports(
    model_provider: ModelProvider,
    retriever: Retriever,
    tracer: Tracer | None = None,
) -> GraphPorts:
    """Create production GraphPorts with is_mock_profile=False and simulation triggers disabled."""
    return GraphPorts(
        model_provider=model_provider,
        retriever=retriever,
        tracer=tracer,
        is_mock_profile=False,
        mock_evidence_state=None,
        mock_forced_route=None,
        mock_forced_answer_mode=None,
    )


def create_production_context(
    ports: GraphPorts,
    tenant: TenantContext,
    question_text: str,
    evidence_text: dict[str, str] | None = None,
    raw_document_bytes: bytes = b"",
) -> GraphRuntimeContext:
    """Create request-scoped production runtime context with zero simulation switches."""
    return GraphRuntimeContext(
        ports=ports,
        tenant=tenant,
        question_text=question_text,
        evidence_text=evidence_text or {},
        raw_document_bytes=raw_document_bytes,
    )


def create_mock_ports(
    model_provider: ModelProvider | None = None,
    retriever: Retriever | None = None,
    tracer: Tracer | None = None,
) -> GraphPorts:
    """Create mock GraphPorts wired with mock adapters for local Phase 1 execution."""
    from app.adapters.models.mock import MockModelProvider
    from app.adapters.retrieval.mock import MockRetriever

    return GraphPorts(
        model_provider=model_provider or MockModelProvider(),
        retriever=retriever or MockRetriever(),
        tracer=tracer,
        is_mock_profile=True,
    )


def create_auth_provider(
    idle_timeout_minutes: int = 30,
    absolute_timeout_hours: int = 24,
) -> Any:
    """Instantiate the built-in database-backed AuthProvider."""
    from app.adapters.auth.builtin import BuiltinAuthProvider

    return BuiltinAuthProvider(
        idle_timeout_minutes=idle_timeout_minutes,
        absolute_timeout_hours=absolute_timeout_hours,
    )


SYNTHETIC_LOCAL_PROFILES: frozenset[str] = frozenset(
    {"mock", "office-cpu", "desktop-gpu", "home-gpu", "dev-free-api"}
)


def create_malware_scanner(profile: str) -> MalwareScanner:
    """Create a malware scanner adapter based on runtime profile.

    The MockMalwareScanner is strictly permitted only in local synthetic profiles:
    mock, office-cpu, desktop-gpu, home-gpu, dev-free-api.
    Any other profile (e.g., 'cloud', 'frontier', or unknown) raises ConfigurationError.
    Profile is required with no default to prevent fallback to 'mock'.
    """
    from app.ports.errors import ConfigurationError

    if profile in SYNTHETIC_LOCAL_PROFILES:
        from app.adapters.malware.mock import MockMalwareScanner

        return MockMalwareScanner()
    raise ConfigurationError(
        f"MockMalwareScanner is forbidden in profile '{profile}'. "
        f"Allowed synthetic profiles: {sorted(SYNTHETIC_LOCAL_PROFILES)}"
    )


def create_document_parser(
    profile: str,
    *,
    disable_model_download: bool = False,
    pinned_model_paths: str | dict[str, str] | None = None,
) -> DocumentParser:
    """Create a document parser adapter based on runtime profile.

    Profiles:
    - mock: MockDocumentParser
    - office-cpu, desktop-gpu, home-gpu, dev-free-api: DoclingDocumentParser (real)
    - cloud: DoclingDocumentParser (production), strictly requires model auto-download
      disabled and pinned model paths configured. If missing, raises ConfigurationError.
    - unknown profile: ConfigurationError
    The mock parser must never be returned outside the mock profile.
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.parsing.mock import MockDocumentParser

        return MockDocumentParser()

    if profile in ("office-cpu", "desktop-gpu", "home-gpu", "dev-free-api"):
        from app.adapters.parsing.docling import DoclingDocumentParser

        return DoclingDocumentParser(
            disable_model_download=disable_model_download,
            pinned_model_paths=pinned_model_paths,
        )

    if profile == "cloud":
        if not disable_model_download or not pinned_model_paths:
            raise ConfigurationError(
                "Cloud profile requires model auto-download disabled and pinned model paths "
                "configured.",
                error_code="CLOUD_PARSER_CONFIG_MISSING",
            )
        from app.adapters.parsing.docling import DoclingDocumentParser

        return DoclingDocumentParser(
            disable_model_download=disable_model_download,
            pinned_model_paths=pinned_model_paths,
        )

    allowed = ("mock", "office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "cloud")
    raise ConfigurationError(
        f"Unknown or unauthorized profile '{profile}' for document parser. "
        f"Allowed profiles: {', '.join(repr(p) for p in allowed)}.",
        error_code="UNAUTHORIZED_PARSER_PROFILE",
    )


def create_document_repository(session: AsyncSession) -> DocumentRepository:
    """Instantiate PostgresDocumentRepository adapter.

    Note: session MUST be a tenant_session(ctx) AsyncSession,
    never an unscoped_session(), to enforce PostgreSQL RLS policies.
    """
    from app.adapters.db.postgres_document_repository import PostgresDocumentRepository

    return PostgresDocumentRepository(session)


def create_storage_backend(profile: str, base_dir: str = "storage") -> StorageBackend:
    """Instantiate LocalStorageBackend adapter for local synthetic profiles only.

    cloud, frontier, or unknown profiles raise ConfigurationError.
    Profile is required with no default to prevent fallback to 'mock'.
    """
    from app.ports.errors import ConfigurationError

    if profile in SYNTHETIC_LOCAL_PROFILES:
        from app.adapters.storage.local import LocalStorageBackend

        return LocalStorageBackend(base_dir)
    raise ConfigurationError(
        f"LocalStorageBackend is forbidden in profile '{profile}'. "
        f"Allowed synthetic profiles: {sorted(SYNTHETIC_LOCAL_PROFILES)}"
    )


def create_upload_service(
    session: AsyncSession,
    profile: str,
    storage_dir: str = "storage",
    audit_logger: AuditLogger | None = None,
) -> UploadService:
    """Instantiate and wire UploadService with adapters for a session.

    Note: session MUST be a tenant_session(ctx) AsyncSession,
    never an unscoped_session(), to enforce PostgreSQL RLS policies.
    Profile is required with no default to prevent fallback to 'mock'.
    """
    from app.observability.audit import AuditLogger as PostgresAuditLogger

    repo = create_document_repository(session)
    scanner = create_malware_scanner(profile)
    storage = create_storage_backend(profile=profile, base_dir=storage_dir)
    logger = audit_logger if audit_logger is not None else PostgresAuditLogger()
    return UploadService(
        document_repo=repo,
        storage=storage,
        malware_scanner=scanner,
        audit_logger=logger,
    )


def create_table_extractor() -> TableExtractor:
    """Instantiate BuiltinTableExtractor adapter."""
    from app.adapters.tables.builtin import BuiltinTableExtractor

    return BuiltinTableExtractor()


def create_table_ingestion_service(
    session: AsyncSession,
) -> Any:
    """Instantiate and wire TableIngestionService with repository and table extractor.

    Note: session MUST be a tenant_session(ctx) AsyncSession,
    never an unscoped_session(), to enforce PostgreSQL RLS policies.
    """
    from app.core.ingestion.table_service import TableIngestionService

    repo = create_document_repository(session)
    extractor = create_table_extractor()
    return TableIngestionService(repository=repo, table_extractor=extractor)


def create_chunker(profile: str) -> Chunker:
    """Instantiate Chunker adapter based on profile.

    MockChunker is strictly wired only in profile 'mock'.
    Other profiles raise ConfigurationError until real chunker is built in Task 3.1.
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.chunking.mock import MockChunker

        return MockChunker()

    raise ConfigurationError(
        f"Chunker is not yet implemented for profile '{profile}'. "
        "Real chunker will be implemented in Task 3.1."
    )


def create_embedding_provider(profile: str) -> EmbeddingProvider:
    """Instantiate EmbeddingProvider adapter based on profile.

    MockEmbeddingProvider is strictly wired only in profile 'mock'.
    Other profiles raise ConfigurationError until real BGE-M3 provider is built in Task 3.1.
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.embedding.mock import MockEmbeddingProvider

        return MockEmbeddingProvider()

    raise ConfigurationError(
        f"EmbeddingProvider is not yet implemented for profile '{profile}'. "
        "Real BGE-M3 embedding provider will be implemented in Task 3.1."
    )


def create_page_pipeline_service(
    session: AsyncSession,
    profile: str = "mock",
) -> Any:
    """Instantiate and wire PagePipelineService with repository, parser, chunker, and embedder."""
    from app.core.ingestion.pipeline import PagePipelineService

    repo = create_document_repository(session)
    parser = create_document_parser(profile=profile)
    chunker = create_chunker(profile=profile)
    embedder = create_embedding_provider(profile=profile)
    return PagePipelineService(
        repository=repo,
        parser=parser,
        chunker=chunker,
        embedding_provider=embedder,
    )


def create_version_activation_service(
    session: AsyncSession,
) -> Any:
    """Instantiate and wire VersionActivationService with document repository."""
    from app.core.ingestion.activation import VersionActivationService

    repo = create_document_repository(session)
    return VersionActivationService(repository=repo)
