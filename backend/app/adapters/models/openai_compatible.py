"""OpenAI-compatible chat-completions adapter for hosted model endpoints."""

from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel

from app.ports.errors import (
    ConfigurationError,
    MalformedOutput,
    PortError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.ports.image_processor import ImageProcessor
from app.ports.model_catalog import StructuredOutputMode
from app.ports.model_provider import ModelProvider
from app.ports.types import (
    ChatMessage,
    ExtractedFields,
    ExtractedFieldValue,
    ModelResponse,
    ModelRole,
    PageReading,
    ReadState,
)


def strip_reasoning_blocks(content: str) -> str:
    """Strip reasoning or think blocks from model response before parsing.

    Supports:
    - Standard thought blocks: <think>...</think>, <thought>...</thought>
    - Gemma 4 thought-channel blocks: <channel name="thought">...</channel>
    - Empty thought-channel blocks: <channel name="thought"></channel>, <channel name="thought" />
    - Residual closing tags: </think>, </thought>, </channel>
    Never logs stripped reasoning text (observability metadata only).
    """
    if not content:
        return ""
    # Strip <think>...</think> and <thought>...</thought>
    cleaned = re.sub(r"<(think|thought)>.*?</\1>", "", content, flags=re.DOTALL | re.IGNORECASE)
    # Strip <channel name="thought">...</channel> (including empty or whitespace-only)
    cleaned = re.sub(
        r'<channel\s+name=["\']thought["\']>.*?</channel>',
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Strip self-closing <channel name="thought" />
    cleaned = re.sub(
        r'<channel\s+name=["\']thought["\']\s*/>',
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Strip any residual unclosed/orphaned tags
    cleaned = re.sub(r"</?(think|thought)>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r'</?channel(?:\s+name=["\']thought["\'])?\s*>', "", cleaned, flags=re.IGNORECASE
    )
    return cleaned.strip()


class OpenAICompatibleModelProvider(ModelProvider):
    """Call one configured model through the OpenAI chat-completions wire format."""

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
        output_token_limits: dict[str, int] | None = None,
        image_processor: ImageProcessor | None = None,
        max_image_dimension: int | None = None,
        max_retries: int = 1,
        max_total_wait_seconds: float = 0.0,
        requests_per_minute: int | None = None,
        strip_reasoning: bool = True,
        temperature: float = 0.0,
        structured_output_mode: StructuredOutputMode = "json_object",
        reasoning: bool = False,
        reasoning_off_params: Mapping[str, Any] | None = None,
    ) -> None:
        if structured_output_mode not in ("json_schema", "json_object"):
            raise ConfigurationError(
                message=(
                    "Hosted model structured_output_mode must be 'json_schema' or 'json_object'"
                ),
                error_code="HOSTED_MODEL_CONFIGURATION_INVALID",
            )
        if reasoning_off_params is not None and not isinstance(reasoning_off_params, Mapping):
            raise ConfigurationError(
                message="Hosted model reasoning_off_params must be a mapping",
                error_code="HOSTED_MODEL_CONFIGURATION_INVALID",
            )
        if not model_name.strip() or not base_url.strip() or not api_key.strip():
            raise ConfigurationError(
                message="Model name, base URL, and API key are required",
                error_code="HOSTED_MODEL_CONFIGURATION_MISSING",
            )
        cleaned_url = base_url.strip().rstrip("/")
        self._url = (
            cleaned_url
            if cleaned_url.endswith("/chat/completions")
            else f"{cleaned_url}/chat/completions"
        )
        self._model_name = model_name.strip()
        self._api_key = api_key.strip()
        limits = output_token_limits or {}
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in limits.values()
        ):
            raise ConfigurationError(
                message="Hosted model token limits must be positive integers",
                error_code="HOSTED_MODEL_CONFIGURATION_INVALID",
            )
        self._output_token_limits = limits
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds
        self._image_processor = image_processor
        self._max_image_dimension = max_image_dimension
        self._max_retries = max(0, max_retries)
        self._max_total_wait_seconds = max(0.0, float(max_total_wait_seconds))
        self._requests_per_minute = requests_per_minute
        self._strip_reasoning = strip_reasoning
        self._temperature = temperature
        self._structured_output_mode: StructuredOutputMode = structured_output_mode
        self._reasoning = bool(reasoning)
        self._reasoning_off_params: dict[str, Any] = (
            dict(reasoning_off_params) if reasoning_off_params is not None else {}
        )

        self._pacing_lock = asyncio.Lock()
        self._last_request_time: float = 0.0

    async def _apply_pacing(self) -> None:
        if self._requests_per_minute is not None and self._requests_per_minute > 0:
            min_interval = 60.0 / float(self._requests_per_minute)
            async with self._pacing_lock:
                now = time.monotonic()
                elapsed = now - self._last_request_time
                if self._last_request_time > 0 and elapsed < min_interval:
                    await asyncio.sleep(min_interval - elapsed)
                self._last_request_time = time.monotonic()

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        role: ModelRole | None = None,
    ) -> tuple[str, int | None, dict[str, Any] | None]:
        if max_tokens is None or max_tokens <= 0:
            raise ConfigurationError(
                message="Requested max_tokens is required and must be a positive integer",
                error_code="MISSING_MAX_TOKENS",
            )
        if role is not None:
            configured_limit = self._output_token_limits.get(role.value)
            if configured_limit is not None:
                max_tokens = min(max_tokens, configured_limit)

        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "stream": False,
            "temperature": self._temperature,
            "max_tokens": max_tokens,
        }
        if not self._reasoning and self._reasoning_off_params:
            for param_key, param_val in self._reasoning_off_params.items():
                payload[param_key] = param_val
        if schema is not None:
            if self._structured_output_mode == "json_schema":
                raw_schema = schema.model_json_schema()
                raw_title = raw_schema.get("title")
                schema_name = (
                    raw_title.strip()
                    if isinstance(raw_title, str) and raw_title.strip()
                    else "response"
                )
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": raw_schema,
                    },
                }
            else:
                payload["response_format"] = {"type": "json_object"}

        await self._apply_pacing()

        attempts = 0
        total_waited = 0.0
        response: httpx.Response | None = None
        max_attempts = self._max_retries + 1

        while attempts < max_attempts:
            attempts += 1
            client_to_close: httpx.AsyncClient | None = None
            if self._http_client is None:
                client = httpx.AsyncClient(timeout=self._timeout_seconds)
                client_to_close = client
            else:
                client = self._http_client

            try:
                response = await client.post(
                    self._url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            except httpx.TimeoutException as exc:
                if client_to_close is not None:
                    await client_to_close.aclose()
                if attempts >= max_attempts:
                    raise ProviderTimeout(message="Hosted model request timed out") from exc
                delay = min(0.5 * (2 ** (attempts - 1)), 2.0)
                if total_waited + delay > self._max_total_wait_seconds:
                    raise ProviderTimeout(message="Hosted model request timed out") from exc
                await asyncio.sleep(delay)
                total_waited += delay
                continue
            except httpx.NetworkError as exc:
                if client_to_close is not None:
                    await client_to_close.aclose()
                if attempts >= max_attempts:
                    raise ProviderUnavailable(message="Hosted model endpoint unavailable") from exc
                delay = min(0.5 * (2 ** (attempts - 1)), 2.0)
                if total_waited + delay > self._max_total_wait_seconds:
                    raise ProviderUnavailable(message="Hosted model endpoint unavailable") from exc
                await asyncio.sleep(delay)
                total_waited += delay
                continue
            finally:
                if client_to_close is not None:
                    await client_to_close.aclose()

            if response.status_code == 429:
                if attempts >= max_attempts:
                    raise ProviderRateLimited(
                        message="Provider rate limit exceeded; maximum retry attempts reached.",
                        error_code="PROVIDER_RATE_LIMITED",
                    )
                retry_after_str = response.headers.get("Retry-After")
                delay = None
                if retry_after_str:
                    try:
                        delay = float(retry_after_str.strip())
                    except ValueError:
                        delay = None
                if delay is None:
                    delay = min(0.5 * (2 ** (attempts - 1)), 5.0) + random.uniform(0.01, 0.05)
                if total_waited + delay > self._max_total_wait_seconds:
                    raise ProviderRateLimited(
                        message="Provider rate limit exceeded; maximum retry attempts reached.",
                        error_code="PROVIDER_RATE_LIMITED",
                    )
                await asyncio.sleep(delay)
                total_waited += delay
                continue

            if response.status_code in (500, 502, 503, 504):
                if attempts >= max_attempts:
                    raise ProviderUnavailable(
                        message="Hosted model endpoint unavailable; transient 5xx error",
                        error_code="HOSTED_MODEL_HTTP_ERROR",
                    )
                delay = min(0.5 * (2 ** (attempts - 1)), 2.0)
                if total_waited + delay > self._max_total_wait_seconds:
                    raise ProviderUnavailable(
                        message="Hosted model endpoint unavailable; transient 5xx error",
                        error_code="HOSTED_MODEL_HTTP_ERROR",
                    )
                await asyncio.sleep(delay)
                total_waited += delay
                continue

            break

        if response is None:
            raise ProviderUnavailable(message="Hosted model endpoint unavailable")

        if response.status_code >= 400:
            raise PortError(
                message="Hosted model returned an HTTP error",
                error_code="HOSTED_MODEL_HTTP_ERROR",
                status_code=502,
            )

        try:
            data = response.json()
            raw_content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise MalformedOutput(message="Malformed hosted model response") from exc
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise MalformedOutput(message="Hosted model returned empty content")

        content = (
            strip_reasoning_blocks(raw_content) if self._strip_reasoning else raw_content.strip()
        )

        usage = data.get("usage", {})
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        usage_tokens = completion_tokens if isinstance(completion_tokens, int) else None

        structured: dict[str, Any] | None = None
        if schema is not None:
            try:
                parsed = json.loads(content)
                validated = schema.model_validate(parsed)
                structured = validated.model_dump()
            except (ValueError, TypeError) as exc:
                raise MalformedOutput(
                    message="Hosted model output failed schema validation"
                ) from exc
        return content.strip(), usage_tokens, structured

    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        filtered_messages = [
            {"role": message.role, "content": message.content}
            for message in messages
            if not (message.role == "assistant" and message.content == "</think>")
        ]
        content, usage, structured = await self._complete(
            filtered_messages,
            schema=schema,
            max_tokens=max_tokens,
            role=role,
        )
        return ModelResponse(
            content=content,
            model_id=self._model_name,
            model_version=self._model_name,
            structured_output=structured,
            usage_tokens=usage,
        )

    def _image_message(self, prompt: str, image: bytes) -> list[dict[str, Any]]:
        if self._image_processor is not None and self._max_image_dimension is not None:
            image = self._image_processor.prepare_image_for_vision(
                image_bytes=image,
                max_dimension=self._max_image_dimension,
                max_bytes=10 * 1024 * 1024,
            )
        encoded = base64.b64encode(image).decode("ascii")
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    },
                ],
            }
        ]

    async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReading:
        text, _, _ = await self._complete(
            self._image_message(
                "Transcribe all visible text from this document page verbatim. "
                "Output only the text appearing on the page.",
                image,
            ),
            max_tokens=2048,
            role=role,
        )
        return PageReading(reading=text, model_id=self._model_name, model_version=self._model_name)

    async def read_page(self, role: ModelRole, image: bytes, question: str) -> PageReading:
        text, _, _ = await self._complete(
            self._image_message(question or "Read the relevant visible information.", image),
            max_tokens=2048,
            role=role,
        )
        return PageReading(reading=text, model_id=self._model_name, model_version=self._model_name)

    async def extract_fields(
        self,
        role: ModelRole,
        image: bytes,
        schema: type[BaseModel],
    ) -> ExtractedFields:
        prompt = "Extract the requested fields from this document image as JSON only. "
        prompt += json.dumps(schema.model_json_schema())
        text, _, structured = await self._complete(
            self._image_message(prompt, image), schema=schema, max_tokens=2048, role=role
        )
        values = structured or json.loads(text)
        fields: dict[str, ExtractedFieldValue] = {}
        for name, value in values.items():
            state = ReadState.PRESENT if value not in (None, "") else ReadState.NOT_PRESENT
            fields[name] = ExtractedFieldValue(field_name=name, value=value, state=state)
        return ExtractedFields(
            fields=fields,
            model_id=self._model_name,
            model_version=self._model_name,
            raw_text=text,
        )
