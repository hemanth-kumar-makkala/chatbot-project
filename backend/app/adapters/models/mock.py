"""Mock ModelProvider adapter with configurable failure injection (Task 1.5).

Implements the ModelProvider port with deterministic canned responses and explicit
failure simulation injection:
- Timeout: raises ProviderTimeout (retryable)
- Network error / Server down: raises ProviderUnavailable (retryable)
- Malformed output: raises MalformedOutput (non-retryable)
- Empty response: raises EmptyOutput (non-retryable)
- Slow response: injects configurable sleep delay
- Transient failure counting: triggers failure N times then recovers cleanly
"""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from app.ports.errors import (
    EmptyOutput,
    MalformedOutput,
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


class MockFailureMode(StrEnum):
    """Failure triggers supported by MockModelProvider."""

    NONE = "none"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    MALFORMED_OUTPUT = "malformed_output"
    EMPTY_OUTPUT = "empty_output"
    SLOW_RESPONSE = "slow_response"


class MockModelProvider(ModelProvider):
    """Mock implementation of ModelProvider for Phase 1 local development and testing."""

    def __init__(self) -> None:
        self._failure_mode: MockFailureMode = MockFailureMode.NONE
        self._failure_count: int = 0
        self._delay_seconds: float = 0.0

    def set_failure(
        self,
        mode: MockFailureMode,
        failure_count: int = 1,
        delay_seconds: float = 0.0,
    ) -> None:
        """Configure mock provider failure injection."""
        self._failure_mode = mode
        self._failure_count = failure_count
        self._delay_seconds = delay_seconds

    def reset(self) -> None:
        """Reset mock provider to normal operational mode."""
        self._failure_mode = MockFailureMode.NONE
        self._failure_count = 0
        self._delay_seconds = 0.0

    async def _check_failure(self) -> None:
        """Evaluate failure triggers before executing mock operation."""
        if self._failure_mode == MockFailureMode.NONE or self._failure_count <= 0:
            return

        mode = self._failure_mode
        self._failure_count -= 1
        if self._failure_count <= 0:
            self._failure_mode = MockFailureMode.NONE

        if mode == MockFailureMode.TIMEOUT:
            raise ProviderTimeout("Mock model timeout triggered")
        if mode == MockFailureMode.UNAVAILABLE:
            raise ProviderUnavailable("Mock model server unreachable")
        if mode == MockFailureMode.MALFORMED_OUTPUT:
            raise MalformedOutput("Mock malformed output triggered")
        if mode == MockFailureMode.EMPTY_OUTPUT:
            raise EmptyOutput("Mock empty output triggered")
        if mode == MockFailureMode.SLOW_RESPONSE:
            await asyncio.sleep(self._delay_seconds)

    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        del role, messages, max_tokens
        await self._check_failure()

        if schema is not None:
            # Generate deterministic mock structured output matching schema fields
            structured: dict[str, Any] = {}
            for field_name, field_info in schema.model_fields.items():
                annotation = field_info.annotation
                if annotation is int:
                    structured[field_name] = 1
                elif annotation is float:
                    structured[field_name] = 0.95
                elif annotation is bool:
                    structured[field_name] = True
                else:
                    structured[field_name] = f"mock_{field_name}"

            return ModelResponse(
                content=json.dumps(structured),
                structured_output=structured,
                model_id="mock-model",
                model_version="1.0",
                usage_tokens=42,
            )

        return ModelResponse(
            content="Mock answer based on grounded evidence.",
            model_id="mock-model",
            model_version="1.0",
            usage_tokens=42,
        )

    async def read_page(
        self,
        role: ModelRole,
        image: bytes,
        question: str,
    ) -> PageReading:
        del role, image
        await self._check_failure()

        return PageReading(
            reading=f"Mock vision reading for question: {question}",
            confidence=0.98,
            model_id="mock-vision",
            model_version="1.0",
            normalized_values={"date": "2026-10-01", "observed_id": "12345"},
        )

    async def extract_fields(
        self,
        role: ModelRole,
        image: bytes,
        schema: type[BaseModel],
    ) -> ExtractedFields:
        del role, image
        await self._check_failure()

        fields: dict[str, ExtractedFieldValue] = {}
        for field_name in schema.model_fields:
            fields[field_name] = ExtractedFieldValue(
                field_name=field_name,
                value=f"extracted_{field_name}",
                state=ReadState.PRESENT,
                confidence=0.95,
            )

        return ExtractedFields(
            fields=fields,
            model_id="mock-extract",
            model_version="1.0",
            raw_text="Mock raw OCR text from document page.",
        )
