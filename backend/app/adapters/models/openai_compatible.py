"""OpenAI-compatible chat-completions adapter for hosted model endpoints."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
from pydantic import BaseModel

from app.ports.errors import (
    ConfigurationError,
    MalformedOutput,
    PortError,
    ProviderTimeout,
    ProviderUnavailable,
)
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
    ) -> None:
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

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        role: ModelRole | None = None,
    ) -> tuple[str, int | None, dict[str, Any] | None]:
        if role is not None:
            configured_limit = self._output_token_limits.get(role.value)
            if configured_limit is not None:
                max_tokens = max(max_tokens or 0, configured_limit)
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "stream": False,
            "temperature": 0.0,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}

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
            raise ProviderTimeout(message="Hosted model request timed out") from exc
        except httpx.NetworkError as exc:
            raise ProviderUnavailable(message="Hosted model endpoint unavailable") from exc
        finally:
            if client_to_close is not None:
                await client_to_close.aclose()

        if response.status_code >= 400:
            raise PortError(
                message="Hosted model returned an HTTP error",
                error_code="HOSTED_MODEL_HTTP_ERROR",
                status_code=502,
            )
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise MalformedOutput(message="Malformed hosted model response") from exc
        if not isinstance(content, str) or not content.strip():
            raise MalformedOutput(message="Hosted model returned empty content")

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
        content, usage, structured = await self._complete(
            [{"role": message.role, "content": message.content} for message in messages],
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
            role=role,
        )
        return PageReading(reading=text, model_id=self._model_name, model_version=self._model_name)

    async def read_page(self, role: ModelRole, image: bytes, question: str) -> PageReading:
        text, _, _ = await self._complete(
            self._image_message(question or "Read the relevant visible information.", image),
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
            self._image_message(prompt, image), schema=schema, role=role
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
