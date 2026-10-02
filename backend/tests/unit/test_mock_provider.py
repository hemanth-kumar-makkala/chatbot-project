"""Unit tests for MockModelProvider with configurable failure modes (Task 1.5).

Verifies:
- Standard generation, vision reading, and field extraction return valid schema payloads.
- Structured schema generation populates structured_output compliant with provided Pydantic model.
- Configurable failure simulation injection:
  - Timeout: raises ProviderTimeout (retryable).
  - Network/Service down: raises ProviderUnavailable (retryable).
  - Malformed JSON/Output: raises MalformedOutput or returns invalid output.
  - Empty response: raises EmptyOutput or returns empty string.
  - Slow response: delays by specified seconds.
  - Transient failure counting: fails N times then recovers (for retry testing).
"""

import asyncio

import pytest
from pydantic import BaseModel

from app.adapters.models.mock import MockFailureMode, MockModelProvider
from app.ports.errors import (
    EmptyOutput,
    MalformedOutput,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.ports.types import ChatMessage, ModelRole, ReadState


class SampleSchema(BaseModel):
    category: str
    confidence: float


class TestMockModelProviderNormalOperation:
    """Verifies standard responses from MockModelProvider."""

    @pytest.fixture
    def provider(self) -> MockModelProvider:
        return MockModelProvider()

    async def test_generate_text(self, provider: MockModelProvider) -> None:
        messages = [ChatMessage(role="user", content="Hello test")]
        resp = await provider.generate(role=ModelRole.ANSWER, messages=messages)

        assert resp.content != ""
        assert resp.model_id == "mock-model"
        assert resp.model_version == "1.0"
        assert resp.usage_tokens is not None

    async def test_generate_structured_schema(self, provider: MockModelProvider) -> None:
        messages = [ChatMessage(role="user", content="Classify this")]
        resp = await provider.generate(
            role=ModelRole.ROUTER,
            messages=messages,
            schema=SampleSchema,
        )

        assert resp.structured_output is not None
        assert "category" in resp.structured_output
        assert "confidence" in resp.structured_output

    async def test_read_page(self, provider: MockModelProvider) -> None:
        reading = await provider.read_page(
            role=ModelRole.VISION,
            image=b"\x89PNG\r\n\x1a\nfakeimage",
            question="What is the date?",
        )

        assert reading.reading != ""
        assert reading.confidence > 0.0
        assert reading.model_id == "mock-vision"
        assert reading.model_version == "1.0"
        assert isinstance(reading.normalized_values, dict)

    async def test_extract_fields(self, provider: MockModelProvider) -> None:
        result = await provider.extract_fields(
            role=ModelRole.EXTRACT,
            image=b"\x89PNG\r\n\x1a\nfakeimage",
            schema=SampleSchema,
        )

        assert "category" in result.fields
        assert "confidence" in result.fields
        assert result.fields["category"].state == ReadState.PRESENT
        assert result.model_id == "mock-extract"


class TestMockModelProviderFailureInjection:
    """Verifies that configurable failure injection raises expected PortError exceptions."""

    @pytest.fixture
    def provider(self) -> MockModelProvider:
        return MockModelProvider()

    async def test_inject_timeout(self, provider: MockModelProvider) -> None:
        provider.set_failure(MockFailureMode.TIMEOUT)
        messages = [ChatMessage(role="user", content="Hello")]

        with pytest.raises(ProviderTimeout) as exc_info:
            await provider.generate(role=ModelRole.ANSWER, messages=messages)
        assert exc_info.value.retryable is True
        assert exc_info.value.error_code == "PROVIDER_TIMEOUT"

    async def test_inject_unavailable(self, provider: MockModelProvider) -> None:
        provider.set_failure(MockFailureMode.UNAVAILABLE)
        messages = [ChatMessage(role="user", content="Hello")]

        with pytest.raises(ProviderUnavailable) as exc_info:
            await provider.generate(role=ModelRole.ANSWER, messages=messages)
        assert exc_info.value.retryable is True
        assert exc_info.value.error_code == "PROVIDER_UNAVAILABLE"

    async def test_inject_malformed_output(self, provider: MockModelProvider) -> None:
        provider.set_failure(MockFailureMode.MALFORMED_OUTPUT)
        messages = [ChatMessage(role="user", content="Hello")]

        with pytest.raises(MalformedOutput) as exc_info:
            await provider.generate(role=ModelRole.ROUTER, messages=messages, schema=SampleSchema)
        assert exc_info.value.retryable is False

    async def test_inject_empty_output(self, provider: MockModelProvider) -> None:
        provider.set_failure(MockFailureMode.EMPTY_OUTPUT)
        messages = [ChatMessage(role="user", content="Hello")]

        with pytest.raises(EmptyOutput) as exc_info:
            await provider.generate(role=ModelRole.ANSWER, messages=messages)
        assert exc_info.value.retryable is False

    async def test_inject_slow_response(self, provider: MockModelProvider) -> None:
        provider.set_failure(MockFailureMode.SLOW_RESPONSE, delay_seconds=0.08)
        messages = [ChatMessage(role="user", content="Hello")]

        start_time = asyncio.get_running_loop().time()
        resp = await provider.generate(role=ModelRole.ANSWER, messages=messages)
        elapsed = asyncio.get_running_loop().time() - start_time

        assert elapsed >= 0.07
        assert resp.content != ""

    async def test_transient_failure_recovery_after_n_attempts(
        self, provider: MockModelProvider
    ) -> None:
        # Fails with TIMEOUT for exactly 2 calls, then recovers
        provider.set_failure(MockFailureMode.TIMEOUT, failure_count=2)
        messages = [ChatMessage(role="user", content="Hello")]

        with pytest.raises(ProviderTimeout):
            await provider.generate(role=ModelRole.ANSWER, messages=messages)

        with pytest.raises(ProviderTimeout):
            await provider.generate(role=ModelRole.ANSWER, messages=messages)

        # 3rd attempt succeeds
        resp = await provider.generate(role=ModelRole.ANSWER, messages=messages)
        assert resp.content != ""
