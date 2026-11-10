"""Unit tests for independent answer, vision, router, extract, and judge provider routing."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.adapters.models.provider_router import ModelProviderRouter
from app.ports.errors import ConfigurationError
from app.ports.model_provider import ModelProvider
from app.ports.types import ChatMessage, ExtractedFields, ModelResponse, ModelRole, PageReading


class StubProvider(ModelProvider):
    def __init__(self, model_name: str, provider_id: str = "stub") -> None:
        self.model_name = model_name
        self.provider_id = provider_id
        self.roles: list[ModelRole] = []
        self.calls: list[tuple[str, ModelRole]] = []

    async def generate(self, role, messages, schema=None, max_tokens=None) -> ModelResponse:
        _ = (messages, schema, max_tokens)
        self.roles.append(role)
        self.calls.append(("generate", role))
        return ModelResponse(content=self.model_name, model_id=self.model_name, model_version="v1")

    async def transcribe_page(self, role, image: bytes) -> PageReading:
        _ = (image,)
        self.calls.append(("transcribe_page", role))
        return PageReading(reading=self.model_name, model_id=self.model_name, model_version="v1")

    async def read_page(self, role, image: bytes, question: str) -> PageReading:
        _ = (image, question)
        self.calls.append(("read_page", role))
        return PageReading(reading=self.model_name, model_id=self.model_name, model_version="v1")

    async def extract_fields(self, role, image: bytes, schema: type[BaseModel]) -> ExtractedFields:
        _ = (image, schema)
        self.calls.append(("extract_fields", role))
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


@pytest.mark.asyncio
async def test_router_routes_each_of_5_roles_to_its_own_provider() -> None:
    p_router = StubProvider("router-model")
    p_answer = StubProvider("answer-model")
    p_vision = StubProvider("vision-model")
    p_extract = StubProvider("extract-model")
    p_judge = StubProvider("judge-model")

    router = ModelProviderRouter(
        providers={
            ModelRole.ROUTER: p_router,
            ModelRole.ANSWER: p_answer,
            ModelRole.VISION: p_vision,
            ModelRole.EXTRACT: p_extract,
            ModelRole.JUDGE: p_judge,
        }
    )

    r_out = await router.generate(ModelRole.ROUTER, [ChatMessage(role="user", content="route")])
    a_out = await router.generate(ModelRole.ANSWER, [ChatMessage(role="user", content="answer")])
    v_out = await router.generate(ModelRole.VISION, [ChatMessage(role="user", content="vision")])
    j_out = await router.generate(ModelRole.JUDGE, [ChatMessage(role="user", content="judge")])
    e_out = await router.extract_fields(ModelRole.EXTRACT, b"img", BaseModel)
    t_out = await router.transcribe_page(ModelRole.VISION, b"img")
    p_out = await router.read_page(ModelRole.VISION, b"img", "question")

    assert r_out.model_id == "router-model"
    assert a_out.model_id == "answer-model"
    assert v_out.model_id == "vision-model"
    assert j_out.model_id == "judge-model"
    assert e_out.model_id == "extract-model"
    assert t_out.model_id == "vision-model"
    assert p_out.model_id == "vision-model"

    assert p_router.roles == [ModelRole.ROUTER]
    assert p_answer.roles == [ModelRole.ANSWER]
    assert p_vision.roles == [ModelRole.VISION]
    assert p_judge.roles == [ModelRole.JUDGE]
    assert p_extract.roles == []
    assert p_extract.calls == [("extract_fields", ModelRole.EXTRACT)]
    assert p_vision.calls == [
        ("generate", ModelRole.VISION),
        ("transcribe_page", ModelRole.VISION),
        ("read_page", ModelRole.VISION),
    ]


def test_router_startup_check_fails_if_judge_resolves_to_same_as_answer() -> None:
    shared = StubProvider("shared-model", provider_id="same-provider")
    with pytest.raises(ConfigurationError) as exc_info:
        ModelProviderRouter(
            providers={
                ModelRole.ROUTER: StubProvider("router-model"),
                ModelRole.ANSWER: shared,
                ModelRole.VISION: StubProvider("vision-model"),
                ModelRole.EXTRACT: StubProvider("extract-model"),
                ModelRole.JUDGE: shared,
            },
            enforce_distinct_judge=True,
        )
    assert exc_info.value.error_code == "JUDGE_MODEL_SAME_AS_ANSWER"
