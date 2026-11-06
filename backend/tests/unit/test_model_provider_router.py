"""Unit tests for independent answer and vision provider routing."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.adapters.models.provider_router import ModelProviderRouter
from app.ports.model_provider import ModelProvider
from app.ports.types import ChatMessage, ExtractedFields, ModelResponse, ModelRole, PageReading


class StubProvider(ModelProvider):
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.roles: list[ModelRole] = []

    async def generate(self, role, messages, schema=None, max_tokens=None) -> ModelResponse:
        self.roles.append(role)
        return ModelResponse(content=self.model_name, model_id=self.model_name, model_version="v1")

    async def transcribe_page(self, role, image: bytes) -> PageReading:
        return PageReading(reading=self.model_name, model_id=self.model_name, model_version="v1")

    async def read_page(self, role, image: bytes, question: str) -> PageReading:
        return PageReading(reading=self.model_name, model_id=self.model_name, model_version="v1")

    async def extract_fields(self, role, image: bytes, schema: type[BaseModel]) -> ExtractedFields:
        return ExtractedFields(fields={}, model_id=self.model_name, model_version="v1")


@pytest.mark.asyncio
async def test_router_uses_separate_answer_and_vision_providers() -> None:
    answer = StubProvider("answer-model")
    vision = StubProvider("vision-model")
    router = ModelProviderRouter(answer_provider=answer, vision_provider=vision)

    answer_result = await router.generate(
        ModelRole.ANSWER, [ChatMessage(role="user", content="answer")]
    )
    vision_result = await router.generate(
        ModelRole.VISION, [ChatMessage(role="user", content="read")]
    )
    page = await router.read_page(ModelRole.VISION, b"image", "read image")

    assert answer_result.model_id == "answer-model"
    assert vision_result.model_id == "vision-model"
    assert page.model_id == "vision-model"
    assert answer.roles == [ModelRole.ANSWER]
    assert vision.roles == [ModelRole.VISION]
