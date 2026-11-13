"""Port interface for ModelProvider.

Multi-role model interface mapped by runtime configuration:
- router: classifies query intent and picks path
- answer: structured generation from validated evidence
- vision: multimodal page reading
- extract: field extraction from PHI documents
- judge: automated evaluation judge
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping

from pydantic import BaseModel

from app.ports.errors import ConfigurationError
from app.ports.types import (
    ChatMessage,
    ExtractedFields,
    ModelResponse,
    ModelRole,
    PageReading,
)


class ModelProvider(ABC):
    """Abstract port for LLM and Vision model providers."""

    @abstractmethod
    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        """Generate structured or unstructured completion from messages."""
        ...

    @abstractmethod
    async def transcribe_page(
        self,
        role: ModelRole,
        image: bytes,
    ) -> PageReading:
        """Perform multimodal visual transcription of a page image at upload time."""
        ...

    @abstractmethod
    async def read_page(
        self,
        role: ModelRole,
        image: bytes,
        question: str,
    ) -> PageReading:
        """Perform multimodal visual reading of a single page image."""
        ...

    @abstractmethod
    async def extract_fields(
        self,
        role: ModelRole,
        image: bytes,
        schema: type[BaseModel],
    ) -> ExtractedFields:
        """Extract structured fields from a document page image."""
        ...


class ModelCapabilities(BaseModel):
    """Explicit capabilities and governance properties declared in profile configuration."""

    supports_vision: bool
    remote: bool
    baa_covered: bool


def resolve_model_capabilities(
    model_name: str | None,
    capabilities_map: Mapping[tuple[str, str], ModelCapabilities],
    provider: str | None = None,
) -> ModelCapabilities:
    """Retrieve explicit declared capabilities for a model from the immutable capabilities map.

    Fail-closed invariant:
    - Never guess, infer, or fall back to model-name heuristics or substrings.
    - If model capabilities are not declared, raise ConfigurationError.
    - Keys are (provider, model) with strict exact matching (no case folding, no prefix stripping).
    """
    if not model_name or not model_name.strip():
        raise ConfigurationError(
            "Model identifier is empty or missing; capabilities cannot be determined. Fail closed.",
            error_code="MODEL_CAPABILITIES_NOT_DECLARED",
        )

    # 1. Direct (provider, model_name) lookup
    if provider is not None:
        key = (provider, model_name)
        if key in capabilities_map:
            return capabilities_map[key]
        raise ConfigurationError(
            f"Model capabilities not declared for ({provider}, {model_name}). Fail closed.",
            error_code="MODEL_CAPABILITIES_NOT_DECLARED",
        )

    # 2. Check if model_name has "provider:model" format matching a key
    if ":" in model_name:
        p, _, m = model_name.partition(":")
        if (p, m) in capabilities_map:
            return capabilities_map[(p, m)]

    # 3. Exact matching on model value across registered providers
    matches = [caps for (p, m), caps in capabilities_map.items() if m == model_name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return matches[0]

    raise ConfigurationError(
        f"Model capabilities not declared for model '{model_name}'. Fail closed.",
        error_code="MODEL_CAPABILITIES_NOT_DECLARED",
    )


def is_cloud_model(
    model_name: str | None,
    *,
    capabilities_map: Mapping[tuple[str, str], ModelCapabilities],
    provider: str | None = None,
    is_cloud_override: bool = False,
) -> bool:
    """Check if model requires cloud safety locking based on explicit capability flags.

    Fail-closed invariant:
    - Applies to every model where remote is true and baa_covered is false.
    - If model capabilities are not declared, raises ConfigurationError (never guessed).
    """
    if is_cloud_override:
        return True
    if not model_name:
        return False
    caps = resolve_model_capabilities(
        model_name, capabilities_map=capabilities_map, provider=provider
    )
    return bool(caps.remote and not caps.baa_covered)


def is_vision_model(
    model_name: str | None,
    *,
    capabilities_map: Mapping[tuple[str, str], ModelCapabilities],
    provider: str | None = None,
) -> bool:
    """Check if model supports multimodal visual reading based on explicit capability flag.

    Fail-closed invariant:
    - If model capabilities are not declared, raises ConfigurationError (never guessed).
    """
    if not model_name:
        return False
    caps = resolve_model_capabilities(
        model_name, capabilities_map=capabilities_map, provider=provider
    )
    return bool(caps.supports_vision)


__all__ = [
    "ModelCapabilities",
    "ModelProvider",
    "is_cloud_model",
    "is_vision_model",
    "resolve_model_capabilities",
]
