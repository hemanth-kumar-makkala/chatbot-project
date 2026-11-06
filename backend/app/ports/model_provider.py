"""Port interface for ModelProvider.

Multi-role model interface mapped by runtime configuration:
- router: classifies query intent and picks path
- answer: structured generation from validated evidence
- vision: multimodal page reading
- extract: field extraction from PHI documents
- judge: automated evaluation judge
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel

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


def is_cloud_model(model_name: str | None, is_cloud_override: bool = False) -> bool:
    """Check if model name or tag indicates a remote/cloud hosted model."""
    if is_cloud_override:
        return True
    if not model_name:
        return False
    tag = model_name.strip().lower()
    return (
        ":cloud" in tag
        or "-cloud" in tag
        or tag.startswith("cloud/")
        or tag.startswith("hosted/")
        or tag.startswith("nvidia:")
        or tag.startswith("inception:")
        or "/cloud" in tag
        or tag.endswith(":cloud")
        or tag.endswith("-cloud")
    )


def is_vision_model(model_name: str | None) -> bool:
    """Check if model name or tag supports multimodal visual document reading."""
    if not model_name:
        return False
    tag = model_name.strip().lower()
    return any(
        v in tag
        for v in (
            "-vl",
            ":vl",
            "vl:",
            "vl-",
            "vision",
            "multimodal",
            "omni",
            "mistral-large-4",
        )
    )
