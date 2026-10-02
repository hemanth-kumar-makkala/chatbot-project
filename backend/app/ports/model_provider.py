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
