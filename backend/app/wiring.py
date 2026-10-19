"""Composition root for the Client Knowledge Assistant application.

This is the ONLY module permitted by architecture boundary enforcement rules
to import from both app.core.* and app.adapters.*.

Provides clean factory functions for constructing the application graph
with specified ports and checkpointer without monkeypatching or test-only overrides.
"""

from __future__ import annotations

from pathlib import Path
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
from app.core.ingestion.splitter_service import DocumentSplitterService
from app.core.ingestion.upload import UploadService
from app.ports.audit import AuditLogger
from app.ports.chunker import Chunker
from app.ports.document_parser import DocumentParser
from app.ports.document_repository import DocumentRepository
from app.ports.document_splitter import DocumentSplitter
from app.ports.embedding_provider import EmbeddingProvider
from app.ports.image_processor import ImageProcessor
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

HF_HUB_OFFLINE_MANDATORY_PROFILES: frozenset[str] = frozenset({"desktop-gpu", "home-gpu"})
HF_HUB_OFFLINE_REQUIRED_MESSAGE: str = (
    "HF_HUB_OFFLINE must be set to '1' for real profiles. Offline mode is mandatory."
)


def enforce_hf_hub_offline_guard(profile: str) -> None:
    """Enforce HF_HUB_OFFLINE == '1' for real profiles at wiring time, before any model loads."""
    if profile in HF_HUB_OFFLINE_MANDATORY_PROFILES:
        import os

        if "HF_HUB_OFFLINE" not in os.environ or os.environ["HF_HUB_OFFLINE"] != "1":
            from app.ports.errors import ConfigurationError

            raise ConfigurationError(
                HF_HUB_OFFLINE_REQUIRED_MESSAGE,
                error_code="HF_HUB_OFFLINE_REQUIRED",
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


def create_document_parser(profile: str) -> DocumentParser:
    """Create a document parser adapter based on runtime profile.

    Profiles:
    - mock: MockDocumentParser
    - office-cpu, desktop-gpu, home-gpu, dev-free-api, cloud: DoclingDocumentParser
      (requires DOCLING_ARTIFACTS_PATH pointing to existing model weights folder)
    - unknown profile: ConfigurationError
    The mock parser must never be returned outside the mock profile.
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.parsing.mock import MockDocumentParser

        return MockDocumentParser()

    allowed = ("mock", "office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "cloud")
    if profile not in allowed:
        raise ConfigurationError(
            f"Unknown or unauthorized profile '{profile}' for document parser. "
            f"Allowed profiles: {', '.join(repr(p) for p in allowed)}.",
            error_code="UNAUTHORIZED_PARSER_PROFILE",
        )

    enforce_hf_hub_offline_guard(profile)

    import os
    from pathlib import Path

    if "DOCLING_ARTIFACTS_PATH" not in os.environ or not os.environ["DOCLING_ARTIFACTS_PATH"]:
        raise ConfigurationError(
            "DOCLING_ARTIFACTS_PATH environment variable is missing.",
            error_code="DOCLING_ARTIFACTS_PATH_MISSING",
        )

    artifacts_path_str = os.environ["DOCLING_ARTIFACTS_PATH"]
    artifacts_path = Path(artifacts_path_str)
    if not artifacts_path.is_dir():
        raise ConfigurationError(
            "DOCLING_ARTIFACTS_PATH directory does not exist.",
            error_code="DOCLING_ARTIFACTS_PATH_NOT_FOUND",
        )

    from app.adapters.parsing.docling import DoclingDocumentParser
    from app.ports.types import ParserDevice

    device: ParserDevice = "cuda" if profile in ("desktop-gpu", "home-gpu") else "cpu"
    return DoclingDocumentParser(artifacts_path=artifacts_path, device=device)


def create_document_repository(session: AsyncSession) -> DocumentRepository:
    """Instantiate PostgresDocumentRepository adapter.

    Note: session MUST be a tenant_session(ctx) AsyncSession,
    never an unscoped_session(), to enforce PostgreSQL RLS policies.
    """
    from app.adapters.db.postgres_document_repository import PostgresDocumentRepository

    return PostgresDocumentRepository(session)


def create_storage_backend(
    profile: str,
    base_dir: str | Path | None = None,
) -> StorageBackend:
    """Instantiate LocalStorageBackend adapter for local synthetic profiles only.

    The local storage folder comes from LOCAL_STORAGE_PATH env var if base_dir is None.
    Missing or non-existent folder raises ConfigurationError on startup.
    cloud, frontier, or unknown profiles raise ConfigurationError.
    Profile is required with no default to prevent fallback to 'mock'.
    """
    import os
    from pathlib import Path

    from app.ports.errors import ConfigurationError

    if profile not in SYNTHETIC_LOCAL_PROFILES:
        raise ConfigurationError(
            f"LocalStorageBackend is forbidden in profile '{profile}'. "
            f"Allowed synthetic profiles: {sorted(SYNTHETIC_LOCAL_PROFILES)}"
        )

    resolved: str | Path | None = base_dir
    if resolved is None:
        raw_path = os.environ.get("LOCAL_STORAGE_PATH")
        if not raw_path or not raw_path.strip():
            raise ConfigurationError(
                "LOCAL_STORAGE_PATH environment variable is missing.",
                error_code="LOCAL_STORAGE_PATH_MISSING",
            )
        resolved = raw_path.strip()

    storage_path = Path(resolved)
    if not storage_path.is_dir():
        raise ConfigurationError(
            f"LOCAL_STORAGE_PATH directory does not exist: {storage_path}",
            error_code="LOCAL_STORAGE_PATH_NOT_FOUND",
        )

    from app.adapters.storage.local import LocalStorageBackend

    return LocalStorageBackend(storage_path)


def create_upload_service(
    session: AsyncSession,
    profile: str,
    storage_dir: str | Path | None = None,
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


def create_document_splitter() -> DocumentSplitter:
    """Instantiate PyPdfiumDocumentSplitter adapter."""
    from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

    return PyPdfiumDocumentSplitter()


def create_document_splitter_service(
    session: AsyncSession,
    profile: str,
    base_dir: str | Path | None = None,
) -> DocumentSplitterService:
    """Instantiate and wire DocumentSplitterService with repository, storage, and splitter."""
    repo = create_document_repository(session)
    storage = create_storage_backend(profile=profile, base_dir=base_dir)
    splitter = create_document_splitter()
    return DocumentSplitterService(
        document_repo=repo,
        storage=storage,
        splitter=splitter,
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

    - mock: MockChunker
    - office-cpu, desktop-gpu, home-gpu, dev-free-api, cloud: StructureAwareChunker
    - unknown profile: ConfigurationError
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.chunking.mock import MockChunker

        return MockChunker()

    if profile in ("office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "cloud"):
        from app.adapters.chunking.structure_aware import StructureAwareChunker

        return StructureAwareChunker()

    raise ConfigurationError(
        "Unknown or unauthorized profile for Chunker",
        error_code="UNAUTHORIZED_CHUNKER_PROFILE",
    )


def create_embedding_provider(profile: str) -> EmbeddingProvider:
    """Instantiate EmbeddingProvider adapter based on profile.

    MockEmbeddingProvider is strictly wired only in profile 'mock'.
    desktop-gpu and home-gpu wire real BgeM3EmbeddingProvider.
    Other profiles raise ConfigurationError with fixed unauthorized error message.
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.embedding.mock import MockEmbeddingProvider

        return MockEmbeddingProvider()

    if profile in {"desktop-gpu", "home-gpu"}:
        enforce_hf_hub_offline_guard(profile)
        from app.adapters.embedding.bge_m3 import create_bge_m3_provider

        return create_bge_m3_provider(profile)

    raise ConfigurationError(
        message="Unknown or unauthorized profile for EmbeddingProvider",
        error_code="UNAUTHORIZED_EMBEDDING_PROFILE",
    )


_UNSET: Any = object()


def create_page_pipeline_service(
    session: AsyncSession,
    profile: str = "mock",
    *,
    parser: Any = _UNSET,
    chunker: Any = _UNSET,
    embedding_provider: Any = _UNSET,
    model_provider: Any = _UNSET,
    image_processor: Any = _UNSET,
    storage: Any = _UNSET,
    max_image_dimension: int = 1024,
    max_image_bytes: int = 4 * 1024 * 1024,
) -> Any:
    """Instantiate and wire PagePipelineService with repository, parser, chunker, and vision.

    Fail-closed policy:
    In any non-mock profile, building the page pipeline without a model provider
    or image processor must raise ConfigurationError. No silent skip.
    """
    from app.core.ingestion.pipeline import PagePipelineService
    from app.ports.errors import ConfigurationError

    if profile != "mock":
        enforce_hf_hub_offline_guard(profile)
        if model_provider is None:
            raise ConfigurationError(
                f"Building page pipeline in non-mock profile '{profile}' "
                "without a model provider is forbidden. Fail closed.",
                error_code="PAGE_PIPELINE_MISSING_MODEL_PROVIDER",
            )
        if model_provider is _UNSET:
            model_provider = create_model_provider(profile=profile)
            if model_provider is None:
                raise ConfigurationError(
                    f"Building page pipeline in non-mock profile '{profile}' "
                    "without a model provider is forbidden. Fail closed.",
                    error_code="PAGE_PIPELINE_MISSING_MODEL_PROVIDER",
                )

        if image_processor is None:
            raise ConfigurationError(
                f"Building page pipeline in non-mock profile '{profile}' "
                "without an image processor is forbidden. Fail closed.",
                error_code="PAGE_PIPELINE_MISSING_IMAGE_PROCESSOR",
            )
        if image_processor is _UNSET:
            image_processor = create_image_processor(profile=profile)
            if image_processor is None:
                raise ConfigurationError(
                    f"Building page pipeline in non-mock profile '{profile}' "
                    "without an image processor is forbidden. Fail closed.",
                    error_code="PAGE_PIPELINE_MISSING_IMAGE_PROCESSOR",
                )

    repo = create_document_repository(session)
    if parser is _UNSET:
        parser = create_document_parser(profile=profile)
    if chunker is _UNSET:
        chunker = create_chunker(profile=profile)
    if embedding_provider is _UNSET:
        embedder = create_embedding_provider(profile=profile)
    else:
        embedder = embedding_provider
    if image_processor is _UNSET:
        image_processor = create_image_processor(profile=profile)
    if storage is _UNSET:
        storage = create_storage_backend(profile=profile)
    if model_provider is _UNSET:
        model_provider = create_model_provider(profile=profile)

    return PagePipelineService(
        repository=repo,
        parser=parser,
        chunker=chunker,
        embedding_provider=embedder,
        model_provider=model_provider,
        image_processor=image_processor,
        storage=storage,
        max_image_dimension=max_image_dimension,
        max_image_bytes=max_image_bytes,
    )


def create_version_activation_service(
    session: AsyncSession,
) -> Any:
    """Instantiate and wire VersionActivationService with document repository."""
    from app.core.ingestion.activation import VersionActivationService

    repo = create_document_repository(session)
    return VersionActivationService(repository=repo)


def create_model_provider(profile: str) -> ModelProvider:
    """Create ModelProvider adapter based on runtime profile.

    Profiles:
    - mock: MockModelProvider
    - desktop-gpu, home-gpu: OllamaModelProvider
    - office-cpu, dev-free-api, cloud: ConfigurationError
      (real Vertex vision providers will be implemented in their respective tasks)
    - unknown profile: ConfigurationError
    The mock model provider must never be returned outside the mock profile.
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.models.mock import MockModelProvider

        return MockModelProvider()

    if profile in ("desktop-gpu", "home-gpu"):
        from app.adapters.models.ollama import OllamaModelProvider

        return OllamaModelProvider(profile=profile)

    if profile in ("office-cpu", "dev-free-api", "cloud"):
        raise ConfigurationError(
            f"ModelProvider is not yet implemented for profile '{profile}'. "
            "Real Vertex vision providers will be implemented in subsequent tasks.",
            error_code="MODEL_PROVIDER_NOT_IMPLEMENTED",
        )

    allowed = ("mock", "office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "cloud")
    raise ConfigurationError(
        f"Unknown or unauthorized profile '{profile}' for ModelProvider. "
        f"Allowed profiles: {', '.join(repr(p) for p in allowed)}.",
        error_code="UNAUTHORIZED_MODEL_PROVIDER_PROFILE",
    )


def create_image_processor(profile: str = "mock") -> ImageProcessor:
    """Instantiate ImageProcessor adapter."""
    _ = profile
    from app.adapters.images.pillow import PillowImageProcessor

    return PillowImageProcessor()
