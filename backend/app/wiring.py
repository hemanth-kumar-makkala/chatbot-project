"""Composition root for the Client Knowledge Assistant application.

This is the ONLY module permitted by architecture boundary enforcement rules
to import from both app.core.* and app.adapters.*.

Provides clean factory functions for constructing the application graph
with specified ports and checkpointer without monkeypatching or test-only overrides.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.prompts.yaml_loader import load_yaml_prompt_registry
from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
)
from app.core.ingestion.splitter_service import DocumentSplitterService
from app.core.ingestion.upload import UploadService
from app.core.prompts import set_prompt_registry
from app.ports.audit import AuditLogger
from app.ports.chunker import Chunker
from app.ports.document_parser import DocumentParser
from app.ports.document_repository import DocumentRepository
from app.ports.document_splitter import DocumentSplitter
from app.ports.embedding_provider import EmbeddingProvider
from app.ports.image_processor import ImageProcessor
from app.ports.malware_scanner import MalwareScanner
from app.ports.model_provider import ModelProvider
from app.ports.reranker import Reranker
from app.ports.retriever import Retriever
from app.ports.scrubber import ExceptionScrubber
from app.ports.storage import StorageBackend
from app.ports.table_extractor import TableExtractor
from app.ports.tracer import Tracer
from app.ports.types import TenantContext

# Initialize prompt registry from YAML outside app.core
with contextlib.suppress(Exception):
    set_prompt_registry(load_yaml_prompt_registry())

# Register default CI/unit-test safety-net scrubber; create_app overrides per profile
with contextlib.suppress(Exception):
    from app.adapters.scrubbing.mock import MockExceptionScrubber as _DefaultMockScrubber
    from app.observability.logger import set_exception_scrubber as _init_set_scrubber

    _init_set_scrubber(_DefaultMockScrubber())


def build_app_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph[GraphState, GraphRuntimeContext, Any, Any]:
    """Build and compile the core knowledge graph for the application."""
    try:
        from app.core.prompts import get_prompt_registry

        get_prompt_registry()
    except RuntimeError:
        set_prompt_registry(load_yaml_prompt_registry())
    builder = build_knowledge_graph()
    return builder.compile(checkpointer=checkpointer)


def create_production_ports(
    model_provider: ModelProvider,
    retriever: Retriever,
    reranker: Reranker | None = None,
    tracer: Tracer | None = None,
    image_processor: ImageProcessor | None = None,
    chat_model: str = "qwen3-vl:4b",
    vision_model: str = "qwen3-vl:4b",
) -> GraphPorts:
    """Create production GraphPorts with is_mock_profile=False and simulation triggers disabled."""
    from app.adapters.images.pillow import PillowImageProcessor

    return GraphPorts(
        model_provider=model_provider,
        retriever=retriever,
        reranker=reranker,
        tracer=tracer,
        image_processor=image_processor or PillowImageProcessor(),
        chat_model=chat_model,
        vision_model=vision_model,
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
    page_image_bytes: bytes = b"",
) -> GraphRuntimeContext:
    """Create request-scoped production runtime context with zero simulation switches."""
    return GraphRuntimeContext(
        ports=ports,
        tenant=tenant,
        question_text=question_text,
        evidence_text=evidence_text or {},
        raw_document_bytes=raw_document_bytes,
        page_image_bytes=page_image_bytes,
    )


def create_mock_ports(
    model_provider: ModelProvider | None = None,
    retriever: Retriever | None = None,
    reranker: Reranker | None = None,
    tracer: Tracer | None = None,
) -> GraphPorts:
    """Create mock GraphPorts wired with mock adapters for local Phase 1 execution."""
    from app.adapters.models.mock import MockModelProvider
    from app.adapters.reranking.mock import MockReranker
    from app.adapters.retrieval.mock import MockRetriever

    return GraphPorts(
        model_provider=model_provider or MockModelProvider(),
        retriever=retriever or MockRetriever(),
        reranker=reranker or MockReranker(),
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


def create_model_provider(
    profile: str,
    chat_model: str | None = None,
    *,
    vision_model: str | None = None,
    nvidia_base_url: str | None = None,
    nvidia_api_key: str | None = None,
    inception_base_url: str | None = None,
    inception_api_key: str | None = None,
) -> ModelProvider:
    """Create local/hosted model providers with answer and vision routing independent."""
    from app.ports.errors import ConfigurationError

    profile_config_path = (
        Path(__file__).resolve().parent.parent / "config" / "profiles" / f"{profile}.yaml"
    )
    generation_limits: dict[str, dict[str, int]] = {}
    if profile_config_path.is_file():
        from app.adapters.models.ollama import _load_profile_generation_limits

        generation_limits = _load_profile_generation_limits(profile, profile_config_path)
    hosted_providers: dict[str, ModelProvider] = {}

    def create_selected_provider(model_selector: str) -> ModelProvider | None:
        if model_selector in hosted_providers:
            return hosted_providers[model_selector]
        provider_name, separator, model_name = model_selector.partition(":")
        if not separator or provider_name not in {"nvidia", "inception"}:
            return None
        if not model_name.strip():
            raise ConfigurationError(
                message="Hosted model selector is missing its model identifier",
                error_code="HOSTED_MODEL_CONFIGURATION_MISSING",
            )
        if provider_name == "nvidia":
            base_url, api_key = nvidia_base_url, nvidia_api_key
            missing = [
                name
                for name, value in (
                    ("NVIDIA_BASE_URL", base_url),
                    ("NVIDIA_API_KEY", api_key),
                )
                if not value or not value.strip()
            ]
        else:
            base_url, api_key = inception_base_url, inception_api_key
            missing = [
                name
                for name, value in (
                    ("INCEPTION_BASE_URL", base_url),
                    ("INCEPTION_API_KEY", api_key),
                )
                if not value or not value.strip()
            ]
        if missing:
            raise ConfigurationError(
                message=f"Missing hosted model settings: {', '.join(missing)}",
                error_code="HOSTED_MODEL_CONFIGURATION_MISSING",
            )
        from app.adapters.models.openai_compatible import OpenAICompatibleModelProvider

        configured_limits = generation_limits.get(model_selector) or generation_limits.get(
            f"{provider_name}:*", {}
        )
        provider = OpenAICompatibleModelProvider(
            model_name=model_name.strip(),
            base_url=base_url or "",
            api_key=api_key or "",
            output_token_limits=configured_limits,
        )
        hosted_providers[model_selector] = provider
        return provider

    answer_selector = chat_model or "qwen3-vl:4b"
    vision_selector = vision_model or "qwen3-vl:4b"
    answer_remote = create_selected_provider(answer_selector)
    vision_remote = create_selected_provider(vision_selector)

    if profile == "mock" and answer_remote is None and vision_remote is None:
        from app.adapters.models.mock import MockModelProvider

        return MockModelProvider()

    known_profiles = (
        "mock",
        "office-cpu",
        "desktop-gpu",
        "home-gpu",
        "dev-free-api",
        "cloud",
        "frontier",
    )
    if profile not in known_profiles:
        raise ConfigurationError(
            f"Unknown or unauthorized profile '{profile}' for ModelProvider.",
            error_code="UNAUTHORIZED_MODEL_PROVIDER_PROFILE",
        )

    local_profile_supported = profile in ("desktop-gpu", "home-gpu")
    local_needed = answer_remote is None or vision_remote is None
    if local_needed and not local_profile_supported:
        raise ConfigurationError(
            f"Local model provider is not implemented for profile '{profile}'.",
            error_code="MODEL_PROVIDER_NOT_IMPLEMENTED",
        )

    local_provider: ModelProvider | None = None
    if local_needed:
        from app.adapters.models.ollama import OllamaModelProvider

        local_provider = OllamaModelProvider(
            profile=profile,
            chat_model=answer_selector if answer_remote is None else None,
            vision_model=vision_selector if vision_remote is None else None,
        )

    answer_provider = answer_remote or local_provider
    vision_provider = vision_remote or local_provider
    if answer_provider is None or vision_provider is None:
        raise ConfigurationError(
            "Unable to construct the selected model providers.",
            error_code="MODEL_PROVIDER_NOT_IMPLEMENTED",
        )
    if answer_provider is vision_provider:
        return answer_provider

    from app.adapters.models.provider_router import ModelProviderRouter

    return ModelProviderRouter(
        answer_provider=answer_provider,
        vision_provider=vision_provider,
    )


def create_image_processor(profile: str = "mock") -> ImageProcessor:
    """Instantiate ImageProcessor adapter."""
    _ = profile
    from app.adapters.images.pillow import PillowImageProcessor

    return PillowImageProcessor()


def create_retriever(
    profile: str,
    embedding_provider: EmbeddingProvider | None = None,
) -> Retriever:
    """Create Retriever adapter based on runtime profile.

    Profiles:
    - mock: MockRetriever
    - desktop-gpu, home-gpu, office-cpu, dev-free-api, cloud: PostgresRetriever
    - unknown profile: ConfigurationError
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.retrieval.mock import MockRetriever

        return MockRetriever()

    if profile in ("desktop-gpu", "home-gpu", "office-cpu", "dev-free-api", "cloud"):
        from app.adapters.retrieval.postgres_retriever import PostgresRetriever

        embedder = embedding_provider or create_embedding_provider(profile=profile)
        return PostgresRetriever(embedding_provider=embedder)

    allowed = ("mock", "office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "cloud")
    raise ConfigurationError(
        f"Unknown or unauthorized profile '{profile}' for Retriever. "
        f"Allowed profiles: {', '.join(repr(p) for p in allowed)}.",
        error_code="UNAUTHORIZED_RETRIEVER_PROFILE",
    )


def create_reranker(profile: str) -> Reranker:
    """Create Reranker adapter based on runtime profile.

    Profiles:
    - mock: MockReranker
    - desktop-gpu, home-gpu: BgeRerankerAdapter
    - all other profiles: ConfigurationError
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.reranking.mock import MockReranker

        return MockReranker()

    if profile in ("desktop-gpu", "home-gpu"):
        enforce_hf_hub_offline_guard(profile)
        from app.adapters.reranking.bge_reranker import create_bge_reranker_adapter

        return create_bge_reranker_adapter(profile)

    raise ConfigurationError(
        f"Unauthorized or unsupported profile '{profile}' for Reranker. "
        "Reranker is wired only for desktop-gpu and home-gpu.",
        error_code="UNAUTHORIZED_RERANKER_PROFILE",
    )


def create_exception_scrubber(profile: str) -> ExceptionScrubber:
    """Create ExceptionScrubber adapter based on runtime profile (Phase 5 Task 5.4).

    Profiles:
    - mock: MockExceptionScrubber (for CI without spaCy model weights)
    - office-cpu, desktop-gpu, home-gpu, dev-free-api, cloud: PresidioExceptionScrubber
      (requires offline spaCy model 'en_core_web_sm'; raises ConfigurationError if missing)
    - unknown profile: ConfigurationError
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.scrubbing.mock import MockExceptionScrubber

        return MockExceptionScrubber()

    allowed = ("mock", "office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "cloud")
    if profile not in allowed:
        raise ConfigurationError(
            f"Unknown or unauthorized profile '{profile}' for ExceptionScrubber. "
            f"Allowed profiles: {', '.join(repr(p) for p in allowed)}.",
            error_code="UNAUTHORIZED_SCRUBBER_PROFILE",
        )

    from app.adapters.scrubbing.presidio import PresidioExceptionScrubber

    return PresidioExceptionScrubber()
