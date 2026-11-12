"""Composition root for the Client Knowledge Assistant application.

This is the ONLY module permitted by architecture boundary enforcement rules
to import from both app.core.* and app.adapters.*.

Provides clean factory functions for constructing the application graph
with specified ports and checkpointer without monkeypatching or test-only overrides.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
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
from app.ports.model_catalog import (
    ProviderCatalogConfig,
    ProvidersStatusResponse,
    ProviderTestConnectionResponse,
    StructuredOutputMode,
)
from app.ports.model_provider import ModelCapabilities, ModelProvider
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
    chat_model: str = "",
    vision_model: str = "",
    capabilities_map: Mapping[tuple[str, str], ModelCapabilities] | None = None,
) -> GraphPorts:
    """Create production GraphPorts with is_mock_profile=False and simulation triggers disabled."""
    from app.adapters.images.pillow import PillowImageProcessor

    return GraphPorts(
        capabilities_map=capabilities_map or {},
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
    capabilities_map: Mapping[tuple[str, str], ModelCapabilities] | None = None,
) -> GraphPorts:
    """Create mock GraphPorts wired with mock adapters for local Phase 1 execution."""
    from app.adapters.models.mock import MockModelProvider
    from app.adapters.reranking.mock import MockReranker
    from app.adapters.retrieval.mock import MockRetriever

    return GraphPorts(
        capabilities_map=capabilities_map or {},
        model_provider=model_provider or MockModelProvider(),
        retriever=retriever or MockRetriever(),
        reranker=reranker or MockReranker(),
        tracer=tracer,
        is_mock_profile=True,
    )


def create_auth_provider(
    idle_timeout_minutes: int = 30,
    absolute_timeout_hours: int = 24,
    provider_type: str = "builtin",
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    api_keys_json: str | None = None,
) -> Any:
    """Instantiate the configured AuthProvider (builtin, jwt, or api_key)."""
    norm_type = (provider_type or "builtin").strip().lower()
    if norm_type == "builtin":
        from app.adapters.auth.builtin import BuiltinAuthProvider

        return BuiltinAuthProvider(
            idle_timeout_minutes=idle_timeout_minutes,
            absolute_timeout_hours=absolute_timeout_hours,
        )
    if norm_type == "jwt":
        from app.adapters.auth.delegated import JwtAuthProvider

        return JwtAuthProvider(
            jwt_secret=jwt_secret or "",
            issuer=jwt_issuer,
            audience=jwt_audience,
        )
    if norm_type == "api_key":
        from app.adapters.auth.delegated import ApiKeyAuthProvider

        return ApiKeyAuthProvider.from_json(api_keys_json or "")

    from app.ports.errors import ConfigurationError

    raise ConfigurationError(
        f"Unknown auth provider: {provider_type}",
        error_code="INVALID_CONFIGURATION",
    )


SYNTHETIC_LOCAL_PROFILES: frozenset[str] = frozenset(
    {"mock", "office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "desktop-api"}
)

HF_HUB_OFFLINE_MANDATORY_PROFILES: frozenset[str] = frozenset(
    {"desktop-gpu", "home-gpu", "desktop-api"}
)
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
    mock, office-cpu, desktop-gpu, home-gpu, dev-free-api, desktop-api.
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
    - office-cpu, desktop-gpu, home-gpu, dev-free-api, cloud, desktop-api: DoclingDocumentParser
      (requires DOCLING_ARTIFACTS_PATH pointing to existing model weights folder)
    - unknown profile: ConfigurationError
    The mock parser must never be returned outside the mock profile.
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.parsing.mock import MockDocumentParser

        return MockDocumentParser()

    allowed = (
        "mock",
        "office-cpu",
        "desktop-gpu",
        "home-gpu",
        "dev-free-api",
        "cloud",
        "desktop-api",
    )
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

    if profile in (
        "office-cpu",
        "desktop-gpu",
        "home-gpu",
        "dev-free-api",
        "cloud",
        "desktop-api",
    ):
        from app.adapters.chunking.structure_aware import StructureAwareChunker

        return StructureAwareChunker()

    raise ConfigurationError(
        "Unknown or unauthorized profile for Chunker",
        error_code="UNAUTHORIZED_CHUNKER_PROFILE",
    )


def create_embedding_provider(profile: str) -> EmbeddingProvider:
    """Instantiate EmbeddingProvider adapter based on profile.

    MockEmbeddingProvider is strictly wired only in profile 'mock'.
    desktop-gpu, home-gpu, and desktop-api wire real BgeM3EmbeddingProvider.
    Other profiles raise ConfigurationError with fixed unauthorized error message.
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.embedding.mock import MockEmbeddingProvider

        return MockEmbeddingProvider()

    if profile in {"desktop-gpu", "home-gpu", "desktop-api"}:
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


def load_and_validate_profile_models(
    profile: str,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Load and validate role configuration and capabilities from profile YAML.

    Enforces:
    - Rejects deprecated environment overrides (ANSWER_MODEL, CHAT_MODEL, VISION_MODEL).
    - Profile YAML file exists.
    - 'models' section contains all 5 required roles: router, answer, vision, extract, judge.
    - Each role declares provider, model, supports_vision, remote, and baa_covered.
    - Remote models require explicit 'temperature' setting per role.
    - Vision and extract roles require supports_vision == True.
    - Judge model must differ from answer model in every non-mock profile.
    - Builds immutable capabilities map keyed by (provider, model) with strict matching.
    """
    import os
    from types import MappingProxyType

    import yaml

    from app.ports.errors import ConfigurationError
    from app.ports.model_provider import ModelCapabilities

    for var in ("ANSWER_MODEL", "CHAT_MODEL", "VISION_MODEL"):
        if os.getenv(var) is not None:
            raise ConfigurationError(
                f"Environment variable '{var}' is no longer supported. "
                "Model configuration must come strictly from the profile YAML.",
                error_code="DEPRECATED_MODEL_ENV_VAR",
            )

    if config_path is None:
        config_path = (
            Path(__file__).resolve().parent.parent / "config" / "profiles" / f"{profile}.yaml"
        )

    if not config_path.is_file():
        raise ConfigurationError(
            f"Profile configuration file not found: {config_path}",
            error_code="PROFILE_NOT_FOUND",
        )

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigurationError(
            f"Failed to parse profile configuration {config_path}: {exc}",
            error_code="PROFILE_PARSE_ERROR",
        ) from exc

    if not isinstance(data, dict) or "models" not in data or not isinstance(data["models"], dict):
        raise ConfigurationError(
            "Profile configuration missing 'models' section",
            error_code="PROFILE_MODELS_SECTION_MISSING",
        )

    models_dict = data["models"]
    required_roles = ("router", "answer", "vision", "extract", "judge")
    capabilities_map: dict[tuple[str, str], ModelCapabilities] = {}

    for role in required_roles:
        if role not in models_dict or not isinstance(models_dict[role], dict):
            raise ConfigurationError(
                f"Missing required model role: {role}",
                error_code="PROFILE_ROLE_MISSING",
            )
        role_cfg = models_dict[role]
        if not role_cfg.get("provider") or not isinstance(role_cfg["provider"], str):
            raise ConfigurationError(
                f"Role '{role}' is missing 'provider'",
                error_code="ROLE_PROVIDER_MISSING",
            )
        if not role_cfg.get("model") or not isinstance(role_cfg["model"], str):
            raise ConfigurationError(
                f"Role '{role}' is missing 'model'",
                error_code="ROLE_MODEL_MISSING",
            )
        for flag in ("supports_vision", "remote", "baa_covered"):
            if flag not in role_cfg or not isinstance(role_cfg[flag], bool):
                raise ConfigurationError(
                    f"Role '{role}' is missing capability flag '{flag}'",
                    error_code="CAPABILITY_FLAG_MISSING",
                )

        if role in ("vision", "extract") and not role_cfg["supports_vision"]:
            raise ConfigurationError(
                f"Model for role '{role}' lacks vision capability (supports_vision=False)",
                error_code="MODEL_LACKS_VISION_CAPABILITY",
            )

        if role_cfg["remote"] and (
            "temperature" not in role_cfg or not isinstance(role_cfg["temperature"], int | float)
        ):
            raise ConfigurationError(
                f"Remote role '{role}' is missing required generation setting 'temperature'",
                error_code="ROLE_TEMPERATURE_MISSING",
            )

        # Exact tuple key with no case folding and no prefix stripping
        exact_key = (role_cfg["provider"], role_cfg["model"])
        capabilities_map[exact_key] = ModelCapabilities(
            supports_vision=role_cfg["supports_vision"],
            remote=role_cfg["remote"],
            baa_covered=role_cfg["baa_covered"],
        )

    # Distinct judge check: fail if judge resolves to same provider and model as answer
    if (
        profile != "mock"
        and models_dict["judge"]["provider"] == models_dict["answer"]["provider"]
        and models_dict["judge"]["model"] == models_dict["answer"]["model"]
    ):
        raise ConfigurationError(
            message="Judge model must differ from answer model",
            error_code="JUDGE_MODEL_SAME_AS_ANSWER",
        )

    models_dict["_capabilities_map"] = MappingProxyType(capabilities_map)
    return models_dict


def create_model_provider(
    profile: str,
    chat_model: str | None = None,
    *,
    vision_model: str | None = None,
    gemini_base_url: str | None = None,
    gemini_api_key: str | None = None,
    nvidia_base_url: str | None = None,
    nvidia_api_key: str | None = None,
    inception_base_url: str | None = None,
    inception_api_key: str | None = None,
    config_path: Path | None = None,
) -> ModelProvider:
    """Create local/hosted model providers with independent role routing."""
    import os

    import yaml

    from app.ports.errors import ConfigurationError
    from app.ports.types import ModelRole

    known_profiles = (
        "mock",
        "office-cpu",
        "desktop-gpu",
        "home-gpu",
        "dev-free-api",
        "cloud",
        "frontier",
        "desktop-api",
    )
    if profile not in known_profiles and config_path is None:
        raise ConfigurationError(
            f"Unknown or unauthorized profile '{profile}' for ModelProvider.",
            error_code="UNAUTHORIZED_MODEL_PROVIDER_PROFILE",
        )

    hosted_param_map: dict[str, tuple[tuple[str, str | None], tuple[str, str | None]]] = {
        "gemini": (("GEMINI_BASE_URL", gemini_base_url), ("GEMINI_API_KEY", gemini_api_key)),
        "nvidia": (("NVIDIA_BASE_URL", nvidia_base_url), ("NVIDIA_API_KEY", nvidia_api_key)),
        "inception": (
            ("INCEPTION_BASE_URL", inception_base_url),
            ("INCEPTION_API_KEY", inception_api_key),
        ),
    }

    def _resolve_hosted_credentials(
        provider_name: str, *, allow_env_fallback: bool
    ) -> tuple[str, str]:
        (url_env, raw_url), (key_env, raw_key) = hosted_param_map[provider_name]
        b_url = raw_url or (os.getenv(url_env) if allow_env_fallback else None)
        a_key = raw_key or (os.getenv(key_env) if allow_env_fallback else None)
        missing: list[str] = []
        if not b_url or not b_url.strip():
            missing.append(url_env)
        if not a_key or not a_key.strip():
            missing.append(key_env)
        if missing or b_url is None or a_key is None:
            raise ConfigurationError(
                message=f"Missing hosted model settings: {', '.join(missing)}",
                error_code="HOSTED_MODEL_CONFIGURATION_MISSING",
            )
        return b_url, a_key

    def _validate_remote_selector(selector: str | None) -> None:
        if not selector or ":" not in selector:
            return
        p_name, _, m_name = selector.partition(":")
        if p_name not in hosted_param_map:
            return
        if not m_name.strip():
            raise ConfigurationError(
                message="Hosted model selector is missing its model identifier",
                error_code="HOSTED_MODEL_CONFIGURATION_MISSING",
            )
        _resolve_hosted_credentials(
            p_name,
            allow_env_fallback=(profile == "desktop-api" and p_name in ("gemini", "nvidia")),
        )

    _validate_remote_selector(chat_model)
    _validate_remote_selector(vision_model)

    if profile in ("office-cpu", "dev-free-api", "cloud") and (
        chat_model is None or not chat_model.startswith(("gemini:", "nvidia:", "inception:"))
    ):
        raise ConfigurationError(
            f"Local model provider is not implemented for profile '{profile}'.",
            error_code="MODEL_PROVIDER_NOT_IMPLEMENTED",
        )

    profile_config_path = config_path or (
        Path(__file__).resolve().parent.parent / "config" / "profiles" / f"{profile}.yaml"
    )

    profile_models = load_and_validate_profile_models(profile, config_path=profile_config_path)

    profile_data: dict[str, Any] = {}
    generation_limits: dict[str, dict[str, int]] = {}
    if profile_config_path.is_file():
        try:
            profile_data = yaml.safe_load(profile_config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            profile_data = {}
        from app.adapters.models.ollama import _load_profile_generation_limits

        generation_limits = _load_profile_generation_limits(profile, profile_config_path)

    providers_config: dict[str, Any] = profile_data.get("providers", {})
    raw_catalog_providers = profile_data.get("catalog_providers", [])
    if not isinstance(raw_catalog_providers, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_catalog_providers
    ):
        raise ConfigurationError(
            "Profile 'catalog_providers' must be a list of non-empty provider names.",
            error_code="PROFILE_CATALOG_PROVIDERS_INVALID",
        )
    catalog_provider_names: set[str] = set(raw_catalog_providers)
    catalog_by_provider: dict[str, ProviderCatalogConfig] = {}
    if catalog_provider_names:
        from app.adapters.models.catalog import load_model_catalog

        catalog_by_provider = {entry.provider: entry for entry in load_model_catalog()}
        for cat_provider in sorted(catalog_provider_names):
            cat_entry = catalog_by_provider.get(cat_provider)
            if cat_entry is None or cat_entry.runtime_settings is None:
                raise ConfigurationError(
                    f"Catalog provider '{cat_provider}' is missing runtime_settings "
                    "in model_catalog.yaml.",
                    error_code="CATALOG_PROVIDER_CONFIGURATION_MISSING",
                )

    answer_override = chat_model
    vision_override = vision_model

    pure_mock = (
        profile == "mock"
        and all(
            isinstance(cfg, dict) and cfg.get("provider") == "mock"
            for k, cfg in profile_models.items()
            if not k.startswith("_")
        )
        and not (answer_override and ":" in answer_override)
        and not (vision_override and ":" in vision_override)
    )
    if pure_mock:
        from app.adapters.models.mock import MockModelProvider

        return MockModelProvider()

    pure_ollama = (
        not catalog_provider_names
        and all(
            isinstance(cfg, dict) and cfg.get("provider") == "ollama"
            for k, cfg in profile_models.items()
            if not k.startswith("_")
        )
        and not (answer_override and ":" in answer_override)
        and not (vision_override and ":" in vision_override)
    )
    if pure_ollama:
        local_profile_supported = profile in ("desktop-gpu", "home-gpu")
        if not local_profile_supported:
            raise ConfigurationError(
                f"Local model provider is not implemented for profile '{profile}'.",
                error_code="MODEL_PROVIDER_NOT_IMPLEMENTED",
            )
        from app.adapters.models.ollama import OllamaModelProvider

        return OllamaModelProvider(
            profile=profile,
            chat_model=answer_override or profile_models["answer"]["model"],
            vision_model=vision_override or profile_models["vision"]["model"],
            capabilities_map=profile_models["_capabilities_map"],
        )

    if profile in ("office-cpu", "dev-free-api", "cloud") and not any(
        isinstance(cfg, dict)
        and cfg.get("provider") in ("gemini", "nvidia", "inception", "openai_compatible")
        for cfg in profile_models.values()
    ):
        raise ConfigurationError(
            f"Local model provider is not implemented for profile '{profile}'.",
            error_code="MODEL_PROVIDER_NOT_IMPLEMENTED",
        )

    hosted_providers_cache: dict[str, ModelProvider] = {}

    def _resolve_catalog_settings(
        provider_name: str,
        model_name: str,
        role_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve validated catalog settings and effective parameters for one role."""
        cat_entry = catalog_by_provider[provider_name]
        if cat_entry.runtime_settings is None:
            raise ConfigurationError(
                f"Catalog provider '{provider_name}' is missing runtime_settings "
                "in model_catalog.yaml.",
                error_code="CATALOG_PROVIDER_CONFIGURATION_MISSING",
            )
        model_spec = next(
            (spec for spec in cat_entry.models if spec.model == model_name),
            None,
        )
        if model_spec is None:
            raise ConfigurationError(
                f"Model '{model_name}' for provider '{provider_name}' is not declared "
                "in model_catalog.yaml.",
                error_code="CATALOG_MODEL_NOT_FOUND",
            )
        rt = cat_entry.runtime_settings
        profile_limits = (
            generation_limits.get(f"{provider_name}:{model_name}")
            or generation_limits.get(model_name)
            or generation_limits.get(f"{provider_name}:*")
        )
        configured_limits: dict[str, int] = (
            dict(profile_limits)
            if profile_limits
            else {str(k): int(v) for k, v in model_spec.generation_limits.items()}
        )
        effective_off_params = (
            model_spec.reasoning_off_params
            if model_spec.reasoning_off_params is not None
            else cat_entry.reasoning_off_params
        )
        return {
            "timeout_seconds": rt.timeout_seconds,
            "max_retries": rt.max_retries,
            "max_total_wait_seconds": rt.max_total_wait_seconds,
            "requests_per_minute": rt.requests_per_minute,
            "max_image_dimension": rt.max_image_dimension,
            "reasoning": bool(role_cfg.get("reasoning", model_spec.reasoning)),
            "temperature": float(role_cfg.get("temperature", model_spec.temperature)),
            "configured_limits": configured_limits,
            "structured_output_mode": cat_entry.structured_output_mode,
            "reasoning_off_params": dict(effective_off_params),
            "num_ctx": cat_entry.num_ctx,
        }

    def get_or_create_provider(role_key: str, model_override: str | None = None) -> ModelProvider:
        role_cfg = profile_models[role_key]
        provider_name = role_cfg["provider"]
        model_name = role_cfg["model"]

        if model_override:
            if ":" in model_override:
                p_name, _, m_name = model_override.partition(":")
                if p_name in ("gemini", "nvidia", "inception", "mock", "ollama"):
                    provider_name = p_name
                    model_name = m_name
                else:
                    model_name = model_override
            else:
                model_name = model_override

        cache_key = f"{provider_name}:{model_name}"
        if cache_key in hosted_providers_cache:
            return hosted_providers_cache[cache_key]

        if provider_name in hosted_param_map:
            base_url, api_key = _resolve_hosted_credentials(provider_name, allow_env_fallback=True)

            if provider_name in catalog_provider_names:
                resolved = _resolve_catalog_settings(provider_name, model_name, role_cfg)
                timeout_seconds = resolved["timeout_seconds"]
                max_retries = resolved["max_retries"]
                max_total_wait_seconds = resolved["max_total_wait_seconds"]
                requests_per_minute = resolved["requests_per_minute"]
                max_image_dimension = resolved["max_image_dimension"]
                reasoning = resolved["reasoning"]
                temperature = resolved["temperature"]
                configured_limits = resolved["configured_limits"]
                structured_output_mode: StructuredOutputMode = resolved["structured_output_mode"]
                reasoning_off_params: dict[str, Any] = resolved["reasoning_off_params"]
            else:
                provider_cfg = providers_config.get(provider_name, {})
                timeout_seconds = float(provider_cfg.get("timeout_seconds", 60.0))
                max_retries = int(provider_cfg.get("max_retries", 3))
                max_total_wait_seconds = float(provider_cfg.get("max_total_wait_seconds", 20.0))
                requests_per_minute = int(provider_cfg.get("requests_per_minute", 30))
                max_image_dimension = int(provider_cfg.get("max_image_dimension", 1536))
                reasoning = bool(role_cfg.get("reasoning", provider_cfg.get("reasoning", False)))
                temperature = float(role_cfg.get("temperature", 0.0))

                configured_limits = (
                    generation_limits.get(f"{provider_name}:{model_name}")
                    or generation_limits.get(model_name)
                    or generation_limits.get(f"{provider_name}:*", {})
                )
                structured_output_mode = "json_object"
                reasoning_off_params = {}

            from app.adapters.models.openai_compatible import OpenAICompatibleModelProvider

            openai_inst: ModelProvider = OpenAICompatibleModelProvider(
                model_name=model_name,
                base_url=base_url,
                api_key=api_key,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                max_total_wait_seconds=max_total_wait_seconds,
                requests_per_minute=requests_per_minute,
                max_image_dimension=max_image_dimension,
                strip_reasoning=not reasoning,
                temperature=temperature,
                structured_output_mode=structured_output_mode,
                reasoning=reasoning,
                reasoning_off_params=reasoning_off_params,
                output_token_limits=configured_limits,
            )
            hosted_providers_cache[cache_key] = openai_inst
            return openai_inst

        if provider_name == "mock":
            from app.adapters.models.mock import MockModelProvider

            mock_inst: ModelProvider = MockModelProvider()
            hosted_providers_cache[cache_key] = mock_inst
            return mock_inst

        if provider_name == "ollama":
            if profile not in ("desktop-gpu", "home-gpu"):
                raise ConfigurationError(
                    f"Local model provider is not implemented for profile '{profile}'.",
                    error_code="MODEL_PROVIDER_NOT_IMPLEMENTED",
                )
            from app.adapters.models.ollama import OllamaModelProvider

            ollama_kwargs: dict[str, Any] = {
                "profile": profile,
                "capabilities_map": profile_models["_capabilities_map"],
                "config_path": profile_config_path,
            }
            if provider_name in catalog_provider_names:
                resolved = _resolve_catalog_settings(provider_name, model_name, role_cfg)
                if resolved["num_ctx"] is None:
                    raise ConfigurationError(
                        "Catalog provider 'ollama' is missing num_ctx in model_catalog.yaml.",
                        error_code="CATALOG_PROVIDER_CONFIGURATION_MISSING",
                    )
                ollama_kwargs["timeout_seconds"] = resolved["timeout_seconds"]
                ollama_kwargs["structured_output_mode"] = resolved["structured_output_mode"]
                ollama_kwargs["reasoning"] = resolved["reasoning"]
                ollama_kwargs["reasoning_off_params"] = resolved["reasoning_off_params"]
                ollama_kwargs["num_ctx"] = resolved["num_ctx"]
                ollama_kwargs["temperature"] = resolved["temperature"]

            if role_key == "vision":
                ollama_kwargs["vision_model"] = model_name
            else:
                ollama_kwargs["chat_model"] = model_name

            ollama_inst: ModelProvider = OllamaModelProvider(**ollama_kwargs)
            hosted_providers_cache[cache_key] = ollama_inst
            return ollama_inst

        raise ConfigurationError(
            f"Unknown provider '{provider_name}' in profile models",
            error_code="UNKNOWN_MODEL_PROVIDER",
        )

    if answer_override and vision_override and profile in ("desktop-gpu", "home-gpu"):
        answer_prov = get_or_create_provider("answer", answer_override)
        vision_prov = get_or_create_provider("vision", vision_override)
        if answer_prov is vision_prov:
            return answer_prov
        from app.adapters.models.provider_router import ModelProviderRouter

        return ModelProviderRouter(
            answer_provider=answer_prov,
            vision_provider=vision_prov,
        )

    role_map = {
        ModelRole.ROUTER: get_or_create_provider("router"),
        ModelRole.ANSWER: get_or_create_provider("answer", answer_override),
        ModelRole.VISION: get_or_create_provider("vision", vision_override),
        ModelRole.EXTRACT: get_or_create_provider("extract"),
        ModelRole.JUDGE: get_or_create_provider("judge"),
    }

    unique_instances = set(role_map.values())
    if len(unique_instances) == 1:
        return next(iter(unique_instances))

    caps_map = profile_models["_capabilities_map"]

    from app.adapters.models.provider_router import ModelProviderRouter

    return ModelProviderRouter(
        providers=role_map,
        capabilities_map=caps_map,
        enforce_distinct_judge=True,
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

    if profile in (
        "desktop-gpu",
        "home-gpu",
        "office-cpu",
        "dev-free-api",
        "cloud",
        "desktop-api",
    ):
        from app.adapters.retrieval.postgres_retriever import PostgresRetriever

        embedder = embedding_provider or create_embedding_provider(profile=profile)
        return PostgresRetriever(embedding_provider=embedder)

    allowed = (
        "mock",
        "office-cpu",
        "desktop-gpu",
        "home-gpu",
        "dev-free-api",
        "cloud",
        "desktop-api",
    )
    raise ConfigurationError(
        f"Unknown or unauthorized profile '{profile}' for Retriever. "
        f"Allowed profiles: {', '.join(repr(p) for p in allowed)}.",
        error_code="UNAUTHORIZED_RETRIEVER_PROFILE",
    )


def create_reranker(profile: str) -> Reranker:
    """Create Reranker adapter based on runtime profile.

    Profiles:
    - mock: MockReranker
    - desktop-gpu, home-gpu, desktop-api: BgeRerankerAdapter
    - all other profiles: ConfigurationError
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.reranking.mock import MockReranker

        return MockReranker()

    if profile in ("desktop-gpu", "home-gpu", "desktop-api"):
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
    - office-cpu, desktop-gpu, home-gpu, dev-free-api, cloud, desktop-api: PresidioExceptionScrubber
      (requires offline spaCy model 'en_core_web_sm'; raises ConfigurationError if missing)
    - unknown profile: ConfigurationError
    """
    from app.ports.errors import ConfigurationError

    if profile == "mock":
        from app.adapters.scrubbing.mock import MockExceptionScrubber

        return MockExceptionScrubber()

    allowed = (
        "mock",
        "office-cpu",
        "desktop-gpu",
        "home-gpu",
        "dev-free-api",
        "cloud",
        "desktop-api",
    )
    if profile not in allowed:
        raise ConfigurationError(
            f"Unknown or unauthorized profile '{profile}' for ExceptionScrubber. "
            f"Allowed profiles: {', '.join(repr(p) for p in allowed)}.",
            error_code="UNAUTHORIZED_SCRUBBER_PROFILE",
        )

    from app.adapters.scrubbing.presidio import PresidioExceptionScrubber

    return PresidioExceptionScrubber()


async def get_model_provider_statuses(
    settings: Any,
    config_path: Path | None = None,
) -> ProvidersStatusResponse:
    """Inspect model provider connection statuses and selectable curated models."""
    from app.adapters.models.catalog import inspect_providers_status

    return await inspect_providers_status(settings=settings, config_path=config_path)


async def test_model_provider_connection(
    provider_id: str,
    settings: Any,
    config_path: Path | None = None,
) -> ProviderTestConnectionResponse:
    """Execute single tiny connection test request for a configured provider."""
    from app.adapters.models.catalog import probe_provider_connection

    return await probe_provider_connection(
        provider_id=provider_id,
        settings=settings,
        config_path=config_path,
    )
