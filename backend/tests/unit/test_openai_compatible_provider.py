"""Unit tests for the OpenAI-compatible hosted model adapter."""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from PIL import Image
from pydantic import BaseModel, ConfigDict

from app.adapters.images.pillow import PillowImageProcessor
from app.adapters.models.openai_compatible import OpenAICompatibleModelProvider
from app.ports.errors import ConfigurationError, ProviderRateLimited
from app.ports.types import ChatMessage, ModelRole


class NameOutput(BaseModel):
    name: str


def test_openai_compatible_adapter_requires_url_and_key() -> None:
    with pytest.raises(ConfigurationError):
        OpenAICompatibleModelProvider("hosted/model", "", "placeholder-value")

    with pytest.raises(ConfigurationError):
        OpenAICompatibleModelProvider("hosted/model", "https://models.example/v1", " ")


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
            "mistralai/test-model",
            "https://models.example/v1",
            "unit-test-placeholder",
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
    assert requests[0].headers["authorization"] == "Bearer unit-test-placeholder"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "mistralai/test-model"
    assert payload["max_tokens"] == 384
    assert payload["response_format"] == {"type": "json_object"}
    assert result.structured_output == {"name": "Apex"}
    assert result.usage_tokens == 7


@pytest.mark.asyncio
async def test_short_mode_sends_short_ceiling_capped_by_limit_using_min() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            "mistralai/test-model",
            "https://models.example/v1",
            "unit-test-placeholder",
            http_client=client,
            output_token_limits={"answer": 2048},
        )
        # Short mode requested with engine ceiling 150: min(150, 2048) == 150
        await provider.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Answer")],
            max_tokens=150,
        )

    assert captured[0]["max_tokens"] == 150


@pytest.mark.asyncio
async def test_missing_or_invalid_max_tokens_raises_typed_error() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200))
    ) as client:
        provider = OpenAICompatibleModelProvider(
            "mistralai/test-model",
            "https://models.example/v1",
            "unit-test-placeholder",
            http_client=client,
        )
        with pytest.raises(ConfigurationError) as exc_info:
            await provider.generate(
                role=ModelRole.ANSWER,
                messages=[ChatMessage(role="user", content="Answer")],
                max_tokens=None,
            )
        assert exc_info.value.error_code == "MISSING_MAX_TOKENS"


@pytest.mark.asyncio
async def test_429_then_200_succeeds_with_backoff_and_jitter() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "recovered"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            "mistralai/test-model",
            "https://models.example/v1",
            "unit-test-placeholder",
            http_client=client,
            max_retries=3,
            max_total_wait_seconds=5.0,
        )
        resp = await provider.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Hello")],
            max_tokens=100,
        )

    assert attempts == 2
    assert resp.content == "recovered"


@pytest.mark.asyncio
async def test_repeated_429_exhausts_retries_and_raises_typed_error() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": "0.01"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            "mistralai/test-model",
            "https://models.example/v1",
            "unit-test-placeholder",
            http_client=client,
            max_retries=2,
            max_total_wait_seconds=2.0,
        )
        with pytest.raises(ProviderRateLimited) as exc_info:
            await provider.generate(
                role=ModelRole.ANSWER,
                messages=[ChatMessage(role="user", content="Hello")],
                max_tokens=100,
            )

    assert attempts == 3  # Initial attempt + 2 retries
    assert exc_info.value.error_code == "PROVIDER_RATE_LIMITED"
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_retry_after_header_is_respected() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.05"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})

    orig_sleep = asyncio.sleep

    async def tracking_sleep(secs: float) -> None:
        delays.append(secs)
        await orig_sleep(0.001)

    with patch("asyncio.sleep", side_effect=tracking_sleep):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleModelProvider(
                "mistralai/test-model",
                "https://models.example/v1",
                "unit-test-placeholder",
                http_client=client,
                max_retries=2,
                max_total_wait_seconds=5.0,
            )
            resp = await provider.generate(
                role=ModelRole.ANSWER,
                messages=[ChatMessage(role="user", content="Hello")],
                max_tokens=100,
            )

    assert attempts == 2
    assert len(delays) == 1
    assert delays[0] >= 0.05
    assert resp.content == "done"


@pytest.mark.asyncio
async def test_client_side_pacing_spaces_calls() -> None:
    # 60 requests per minute = 1.0 second per request
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "paced"}}]})

    call_times: list[float] = []
    orig_sleep = asyncio.sleep

    async def fake_sleep(secs: float) -> None:
        call_times.append(secs)
        await orig_sleep(0.001)

    with patch("asyncio.sleep", side_effect=fake_sleep):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleModelProvider(
                "mistralai/test-model",
                "https://models.example/v1",
                "unit-test-placeholder",
                http_client=client,
                requests_per_minute=60,  # 1s min interval
            )
            await provider.generate(
                role=ModelRole.ANSWER,
                messages=[ChatMessage(role="user", content="Call 1")],
                max_tokens=50,
            )
            await provider.generate(
                role=ModelRole.ANSWER,
                messages=[ChatMessage(role="user", content="Call 2")],
                max_tokens=50,
            )

    # Second call should have invoked sleep to enforce pacing interval
    assert len(call_times) >= 1
    assert call_times[0] > 0.0


@pytest.mark.asyncio
async def test_oversized_image_scaled_before_encoding_and_small_not_upscaled() -> None:
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        img_url = data["messages"][0]["content"][1]["image_url"]["url"]
        captured_urls.append(img_url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "read ok"}}]})

    # Create oversized image: 2000 x 1200
    img_large = Image.new("RGB", (2000, 1200), color="blue")
    buf_large = io.BytesIO()
    img_large.save(buf_large, format="PNG")
    large_bytes = buf_large.getvalue()

    # Create small image: 400 x 300
    img_small = Image.new("RGB", (400, 300), color="red")
    buf_small = io.BytesIO()
    img_small.save(buf_small, format="PNG")
    small_bytes = buf_small.getvalue()

    processor = PillowImageProcessor()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            "mistralai/vision-model",
            "https://models.example/v1",
            "unit-test-placeholder",
            http_client=client,
            image_processor=processor,
            max_image_dimension=1024,
        )

        # Call with large image
        await provider.read_page(ModelRole.VISION, large_bytes, "Read large")
        # Call with small image
        await provider.read_page(ModelRole.VISION, small_bytes, "Read small")

    assert len(captured_urls) == 2

    # Verify large image was downscaled to max dimension 1024
    import base64

    b64_large = captured_urls[0].split("base64,", 1)[1]
    decoded_large = Image.open(io.BytesIO(base64.b64decode(b64_large)))
    assert max(decoded_large.size) == 1024

    # Verify small image was not upscaled
    b64_small = captured_urls[1].split("base64,", 1)[1]
    decoded_small = Image.open(io.BytesIO(base64.b64decode(b64_small)))
    assert decoded_small.size == (400, 300)


@pytest.mark.asyncio
async def test_reasoning_think_blocks_and_empty_thoughts_stripped_before_json_parsing() -> None:
    # Model response with <think> reasoning before JSON
    json_with_think = (
        "<think>\n"
        "Step 1: Patient name is Jane Doe\n"
        "Step 2: Output JSON format\n"
        "</think>\n"
        '{"name": "Jane Doe"}'
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": json_with_think}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            "mistralai/test-model",
            "https://models.example/v1",
            "unit-test-placeholder",
            http_client=client,
            strip_reasoning=True,
        )
        res = await provider.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Name")],
            schema=NameOutput,
            max_tokens=200,
        )

    assert res.structured_output == {"name": "Jane Doe"}
    assert "<think>" not in res.content
    assert "Step 1" not in res.content

    # Empty thought block test
    empty_think_resp = "<think>\n</think>\nPlain text answer"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(
                200, json={"choices": [{"message": {"content": empty_think_resp}}]}
            )
        )
    ) as client:
        provider2 = OpenAICompatibleModelProvider(
            "mistralai/test-model",
            "https://models.example/v1",
            "unit-test-placeholder",
            http_client=client,
            strip_reasoning=True,
        )
        res2 = await provider2.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Text")],
            max_tokens=100,
        )
    assert res2.content == "Plain text answer"

    # Thought channel block tests (e.g. Gemma 4 format)
    channel_thought_resp = (
        '<channel name="thought">\n'
        "Deliberating on the answer...\n"
        "</channel>\n"
        '{"name": "Gemma User"}'
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(
                200, json={"choices": [{"message": {"content": channel_thought_resp}}]}
            )
        )
    ) as client:
        provider3 = OpenAICompatibleModelProvider(
            "google/gemma-4-31b-it",
            "https://models.example/v1",
            "unit-test-placeholder",
            http_client=client,
            strip_reasoning=True,
        )
        res3 = await provider3.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Name")],
            schema=NameOutput,
            max_tokens=100,
        )
    assert res3.structured_output == {"name": "Gemma User"}
    assert "Deliberating" not in res3.content
    assert '<channel name="thought">' not in res3.content

    # Self-closing thought channel test
    self_closing_channel_resp = '<channel name="thought" />\n{"name": "No Reasoning"}'
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(
                200, json={"choices": [{"message": {"content": self_closing_channel_resp}}]}
            )
        )
    ) as client:
        provider4 = OpenAICompatibleModelProvider(
            "google/gemma-4-31b-it",
            "https://models.example/v1",
            "unit-test-placeholder",
            http_client=client,
            strip_reasoning=True,
        )
        res4 = await provider4.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Name")],
            schema=NameOutput,
            max_tokens=100,
        )
    assert res4.structured_output == {"name": "No Reasoning"}


@pytest.mark.asyncio
async def test_gemini_json_schema_mode_and_reasoning_off_params() -> None:
    """Gemini sends json_schema (no strict) and reasoning_off_params when reasoning=False."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"name": "Gemini Apex"})}}],
                "usage": {"completion_tokens": 9},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            "gemini-3.5-flash",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "unit-test-placeholder",
            http_client=client,
            structured_output_mode="json_schema",
            reasoning=False,
            reasoning_off_params={"reasoning_effort": "none"},
        )
        result = await provider.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Return a name")],
            schema=NameOutput,
            max_tokens=256,
        )

    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["reasoning_effort"] == "none"
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "NameOutput",
            "schema": NameOutput.model_json_schema(),
        },
    }
    assert "strict" not in payload["response_format"]["json_schema"]
    assert result.structured_output == {"name": "Gemini Apex"}


@pytest.mark.asyncio
async def test_json_schema_mode_uses_response_when_schema_title_empty() -> None:
    """Schemas with empty/whitespace title fall back to 'response' as json_schema.name."""

    class UntitledOutput(BaseModel):
        model_config = ConfigDict(title="   ")
        ok: bool

    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"ok": True})}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            "gemini-3.5-flash",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "unit-test-placeholder",
            http_client=client,
            structured_output_mode="json_schema",
            reasoning=False,
            reasoning_off_params={"reasoning_effort": "none"},
        )
        res = await provider.generate(
            role=ModelRole.ROUTER,
            messages=[ChatMessage(role="user", content="Check")],
            schema=UntitledOutput,
            max_tokens=150,
        )

    assert captured[0]["response_format"]["json_schema"]["name"] == "response"
    assert "strict" not in captured[0]["response_format"]["json_schema"]
    assert captured[0]["reasoning_effort"] == "none"
    assert res.structured_output == {"ok": True}


@pytest.mark.asyncio
async def test_json_object_mode_and_reasoning_true_behavior() -> None:
    """json_object mode keeps legacy format, and reasoning=True omits reasoning_off_params."""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"name": "Legacy"})}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        legacy_provider = OpenAICompatibleModelProvider(
            "mistralai/test-model",
            "https://models.example/v1",
            "unit-test-placeholder",
            http_client=client,
            structured_output_mode="json_object",
            reasoning=False,
            reasoning_off_params={},
        )
        await legacy_provider.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Name")],
            schema=NameOutput,
            max_tokens=128,
        )

        reasoning_on_provider = OpenAICompatibleModelProvider(
            "gemini-3.5-flash",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "unit-test-placeholder",
            http_client=client,
            structured_output_mode="json_schema",
            reasoning=True,
            reasoning_off_params={"reasoning_effort": "none"},
        )
        await reasoning_on_provider.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Name")],
            schema=NameOutput,
            max_tokens=128,
        )

    assert captured[0]["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in captured[0]
    assert captured[1]["response_format"]["type"] == "json_schema"
    assert "reasoning_effort" not in captured[1]


def test_invalid_structured_output_mode_raises_configuration_error() -> None:
    """Adapter rejects unsupported structured_output_mode values cleanly."""
    bad_kwargs: dict[str, Any] = {"structured_output_mode": "xml_schema"}
    with pytest.raises(ConfigurationError) as exc_info:
        OpenAICompatibleModelProvider(
            "gemini-3.5-flash",
            "https://models.example/v1",
            "unit-test-placeholder",
            **bad_kwargs,
        )
    assert exc_info.value.error_code == "HOSTED_MODEL_CONFIGURATION_INVALID"
