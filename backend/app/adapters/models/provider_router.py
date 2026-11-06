"""Role router for independent answer-generation and vision providers."""

from __future__ import annotations

from pydantic import BaseModel

from app.ports.model_provider import ModelProvider
from app.ports.types import (
    ChatMessage,
    ExtractedFields,
    ModelResponse,
    ModelRole,
    PageReading,
)


class ModelProviderRouter(ModelProvider):
    """Route answer-like generation and visual reading to separately selected providers."""

    def __init__(self, answer_provider: ModelProvider, vision_provider: ModelProvider) -> None:
        self._answer_provider = answer_provider
        self._vision_provider = vision_provider

    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        provider = self._vision_provider if role == ModelRole.VISION else self._answer_provider
        return await provider.generate(role, messages, schema=schema, max_tokens=max_tokens)

    async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReading:
        return await self._vision_provider.transcribe_page(role, image)

    async def read_page(self, role: ModelRole, image: bytes, question: str) -> PageReading:
        return await self._vision_provider.read_page(role, image, question)

    async def extract_fields(
        self, role: ModelRole, image: bytes, schema: type[BaseModel]
    ) -> ExtractedFields:
        return await self._vision_provider.extract_fields(role, image, schema)
