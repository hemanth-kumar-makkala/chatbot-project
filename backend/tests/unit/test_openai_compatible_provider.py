"""Unit tests for the OpenAI-compatible hosted model adapter."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from app.adapters.models.openai_compatible import OpenAICompatibleModelProvider
from app.ports.errors import ConfigurationError
from app.ports.types import ChatMessage, ModelRole


class NameOutput(BaseModel):
    name: str


def test_openai_compatible_adapter_requires_url_and_key() -> None:
    with pytest.raises(ConfigurationError):
        OpenAICompatibleModelProvider(model_name="hosted/model", base_url="", api_key="key")

    with pytest.raises(ConfigurationError):
        OpenAICompatibleModelProvider(
            model_name="hosted/model", base_url="https://models.example/v1", api_key=" "
        )


@pytest.mark.asyncio
async def test_generate_posts_once_with_bearer_and_validates_schema() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"name": "Apex"})}}],
                "usage": {"completion_tokens": 7},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            model_name="mistralai/test-model",
            base_url="https://models.example/v1",
            api_key="test-key",
            http_client=client,
        )
        result = await provider.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Return a name")],
            schema=NameOutput,
            max_tokens=384,
        )

    assert len(requests) == 1
    assert str(requests[0].url) == "https://models.example/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-key"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "mistralai/test-model"
    assert payload["max_tokens"] == 384
    assert payload["response_format"] == {"type": "json_object"}
    assert result.structured_output == {"name": "Apex"}
    assert result.usage_tokens == 7


@pytest.mark.asyncio
async def test_model_token_limit_overrides_small_role_default() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            model_name="mistralai/test-model",
            base_url="https://models.example/v1",
            api_key="test-key",
            http_client=client,
            output_token_limits={"answer": 2048},
        )
        await provider.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Answer")],
            max_tokens=150,
        )

    assert captured[0]["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_read_page_sends_image_to_vision_model() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "member ID ZX4471902"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            model_name="mistralai/vision-model",
            base_url="https://models.example/v1",
            api_key="test-key",
            http_client=client,
        )
        reading = await provider.read_page(ModelRole.VISION, b"fake-image", "Read member ID")

    payload = json.loads(requests[0].content)
    image_part = payload["messages"][0]["content"][1]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert "ZX4471902" in reading.reading


def test_cloud_model_detection_covers_vendor_model_selectors() -> None:
    from app.ports.model_provider import is_cloud_model, is_vision_model

    assert is_cloud_model("nvidia:mistralai/test-model")
    assert is_cloud_model("inception:mercury-test")
    assert is_vision_model("nvidia:mistralai/mistral-large-4-1t-a52b")
