"""Unit tests for OllamaModelProvider adapter (desktop-gpu/home-gpu profile).

Tests first protocol:
- Missing OLLAMA_BASE_URL environment variable raises ConfigurationError.
- profile parameter is required with no default.
- Malformed or missing profile configuration file raises ConfigurationError.
- transcribe_page extracts text, calculates confidence exp(mean(logprob)) over non-special tokens.
- Thinking blocks <think>...</think> are stripped from returned reading.
- model_version is fetched from /api/tags digest field and cached once per provider.
- Missing or empty logprobs returns confidence as None (vision_confidence_missing).
- Timeouts and connection errors raise TransientIngestionError (worker retryable).
- HTTP errors and malformed output raise typed permanent error (retryable=False).
- Unimplemented methods raise typed error with fixed message.
- Real model test marked @pytest.mark.models with synthetic scanned fax page at 200 DPI.
"""

from __future__ import annotations

import io
import json
import math
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

from app.adapters.models.ollama import (
    TRANSCRIPTION_GENERATION_OPTIONS,
    TRANSCRIPTION_SEED,
    TRANSCRIPTION_TEMPERATURE,
    TRANSCRIPTION_TOP_K,
    TRANSCRIPTION_TOP_P,
    OllamaMethodNotImplementedError,
    OllamaModelProvider,
    OllamaPermanentError,
)
from app.ports.errors import (
    ConfigurationError,
    MalformedOutput,
    TransientIngestionError,
)
from app.ports.types import (
    ChatMessage,
    ModelRole,
    PageReading,
)

EXPECTED_DIGEST = "sha256:1343d82ebee38e26a4dd6b0180b915eb91550184e67c505dea97509571c8f683"


def _create_synthetic_page_png() -> bytes:
    """Generate a clean synthetic page image for tests."""
    img = Image.new("RGB", (300, 400), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_synthetic_fax_page_200dpi_png() -> bytes:
    """Generate a synthetic scanned fax page rendered at approximately 200 DPI (1700x2200)."""
    # 8.5 x 11 inches at 200 DPI = 1700 x 2200 pixels
    img = Image.new("RGB", (1700, 2200), color=(250, 250, 250))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=44)

    # Header section
    draw.rectangle([100, 100, 1600, 260], outline=(0, 0, 0), width=4)
    draw.text((150, 130), "PRIOR AUTHORIZATION REQUEST FORM", fill=(0, 0, 0), font=font)
    draw.text((150, 190), "CONFIDENTIAL MEDICAL FAX TRANSMISSION", fill=(0, 0, 0), font=font)

    # Body key values
    draw.text((150, 350), "PATIENT CLINICAL RECORD", fill=(0, 0, 0), font=font)
    draw.text((150, 430), "MEMBER ID ZX4471902", fill=(0, 0, 0), font=font)
    draw.text((150, 510), "PRIMARY DIAGNOSIS: G35 MULTIPLE SCLEROSIS", fill=(0, 0, 0), font=font)
    draw.text((150, 590), "REQUESTED MEDICATION: OCREVUS 300MG/10ML", fill=(0, 0, 0), font=font)
    draw.text((150, 670), "PRESCRIBER: DR. ELEANOR VANCE, MD", fill=(0, 0, 0), font=font)

    # Fax footer
    draw.line([100, 1850, 1600, 1850], fill=(0, 0, 0), width=3)
    draw.text(
        (150, 1880),
        "URGENT ATTENTION: CLINICAL REVIEW DEPARTMENT",
        fill=(0, 0, 0),
        font=font,
    )
    draw.text((150, 1950), "FAX 555-0199", fill=(0, 0, 0), font=font)
    draw.text((150, 2020), "TRANSMITTED: 2026-10-15 09:14 EST", fill=(0, 0, 0), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_fuzzy_phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Build regex matching a phrase tolerating case, whitespace and hyphen variations."""
    parts = [re.escape(part) for part in re.split(r"[\s-]+", phrase) if part]
    return re.compile(r"[\s\-]+".join(parts), re.IGNORECASE)


class DummySchema(BaseModel):
    name: str


class TestOllamaModelProviderUnit:
    """Unit tests for OllamaModelProvider using httpx.MockTransport."""

    def test_missing_base_url_raises_configuration_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing OLLAMA_BASE_URL raises ConfigurationError with fixed message."""
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        with pytest.raises(ConfigurationError) as exc_info:
            OllamaModelProvider(profile="desktop-gpu", base_url=None)

        assert exc_info.value.message == "OLLAMA_BASE_URL environment variable is not set"
        assert exc_info.value.error_code == "CONFIGURATION_ERROR"
        assert exc_info.value.retryable is False

    def test_empty_base_url_raises_configuration_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty string OLLAMA_BASE_URL raises ConfigurationError."""
        monkeypatch.setenv("OLLAMA_BASE_URL", "   ")
        with pytest.raises(ConfigurationError) as exc_info:
            OllamaModelProvider(profile="desktop-gpu", base_url=None)

        assert exc_info.value.message == "OLLAMA_BASE_URL environment variable is not set"

    def test_missing_profile_file_raises_configuration_error(
        self,
        tmp_path: Path,
    ) -> None:
        """Non-existent profile file raises ConfigurationError with fixed string."""
        missing_file = tmp_path / "nonexistent.yaml"
        with pytest.raises(ConfigurationError) as exc_info:
            OllamaModelProvider(
                profile="nonexistent",
                base_url="http://mock-ollama:11434",
                config_path=missing_file,
            )

        assert exc_info.value.message == "Profile configuration file not found"
        assert exc_info.value.error_code == "CONFIGURATION_ERROR"

    def test_malformed_profile_file_raises_configuration_error(
        self,
        tmp_path: Path,
    ) -> None:
        """Malformed YAML structure raises ConfigurationError with fixed string."""
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("profile: bad\nmodels: not_a_dict\n", encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            OllamaModelProvider(
                profile="bad",
                base_url="http://mock-ollama:11434",
                config_path=bad_file,
            )

        assert exc_info.value.message == "Malformed profile configuration file"
        assert exc_info.value.error_code == "CONFIGURATION_ERROR"

    @pytest.mark.asyncio
    async def test_transcribe_page_success_with_geometric_mean_confidence(self) -> None:
        """transcribe_page extracts text, calculates confidence exp(mean(logprob)).

        Special and structural tokens are excluded from the geometric mean.
        model_version is derived from /api/tags digest.
        """
        # Logprobs:
        # "Patient" (-0.1), " report" (-0.2), " normal" (-0.3)
        # Excluded tokens: "<|im_end|>" (0.0), "<think>" (-3.0)
        # Mean logprob over text tokens = (-0.1 + -0.2 + -0.3) / 3 = -0.2
        # Expected confidence = exp(-0.2) ~= 0.8187307530779818
        mock_generate_payload: dict[str, Any] = {
            "model": "qwen3-vl:4b",
            "response": "Patient report normal",
            "logprobs": [
                {"token": "<think>", "logprob": -3.0},
                {"token": "Patient", "logprob": -0.1},
                {"token": " report", "logprob": -0.2},
                {"token": " normal", "logprob": -0.3},
                {"token": "<|im_end|>", "logprob": 0.0},
            ],
            "done": True,
        }
        mock_tags_payload: dict[str, Any] = {
            "models": [
                {
                    "name": "qwen3-vl:4b",
                    "digest": EXPECTED_DIGEST,
                }
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/tags":
                return httpx.Response(200, json=mock_tags_payload)
            if request.method == "POST" and request.url.path == "/api/generate":
                return httpx.Response(200, json=mock_generate_payload)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = OllamaModelProvider(
            profile="desktop-gpu",
            base_url="http://mock-ollama:11434",
            http_client=client,
        )

        image_bytes = _create_synthetic_page_png()
        reading = await provider.transcribe_page(
            role=ModelRole.VISION,
            image=image_bytes,
        )

        assert isinstance(reading, PageReading)
        assert reading.reading == "Patient report normal"
        assert reading.model_id == "qwen3-vl:4b"
        assert reading.model_version == EXPECTED_DIGEST
        assert reading.confidence is not None

        expected_confidence = math.exp(-0.2)
        assert abs(reading.confidence - expected_confidence) < 1e-6

    @pytest.mark.asyncio
    async def test_transcribe_page_strips_think_blocks(self) -> None:
        """transcribe_page strips <think>...</think> blocks from response."""
        mock_generate_payload: dict[str, Any] = {
            "model": "qwen3-vl:4b",
            "response": "<think>\nInternal thinking process\n</think>PRIOR AUTHORIZATION APPROVED",
            "logprobs": [
                {"token": "PRIOR", "logprob": -0.05},
                {"token": " APPROVED", "logprob": -0.05},
            ],
            "done": True,
        }
        mock_tags_payload: dict[str, Any] = {
            "models": [
                {
                    "name": "qwen3-vl:4b",
                    "digest": EXPECTED_DIGEST,
                }
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/tags":
                return httpx.Response(200, json=mock_tags_payload)
            if request.method == "POST" and request.url.path == "/api/generate":
                return httpx.Response(200, json=mock_generate_payload)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = OllamaModelProvider(
            profile="desktop-gpu",
            base_url="http://mock-ollama:11434",
            http_client=client,
        )

        image_bytes = _create_synthetic_page_png()
        reading = await provider.transcribe_page(
            role=ModelRole.VISION,
            image=image_bytes,
        )

        assert reading.reading == "PRIOR AUTHORIZATION APPROVED"
        assert "<think>" not in reading.reading
        assert "</think>" not in reading.reading

    @pytest.mark.asyncio
    async def test_model_digest_cached_once_per_provider(self) -> None:
        """Model digest from /api/tags is cached after first lookup."""
        tags_call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal tags_call_count
            if request.method == "GET" and request.url.path == "/api/tags":
                tags_call_count += 1
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "qwen3-vl:4b",
                                "digest": EXPECTED_DIGEST,
                            }
                        ]
                    },
                )
            if request.method == "POST" and request.url.path == "/api/generate":
                return httpx.Response(
                    200,
                    json={
                        "model": "qwen3-vl:4b",
                        "response": "Page text",
                        "logprobs": [],
                        "done": True,
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = OllamaModelProvider(
            profile="desktop-gpu",
            base_url="http://mock-ollama:11434",
            http_client=client,
        )

        image_bytes = _create_synthetic_page_png()
        # First call
        await provider.transcribe_page(role=ModelRole.VISION, image=image_bytes)
        # Second call
        await provider.transcribe_page(role=ModelRole.VISION, image=image_bytes)

        assert tags_call_count == 1

    @pytest.mark.asyncio
    async def test_transcribe_page_empty_or_missing_logprobs_returns_none_confidence(self) -> None:
        """Missing or empty logprobs yields None confidence for vision_confidence_missing."""
        mock_payload: dict[str, Any] = {
            "model": "qwen3-vl:4b",
            "response": "Page content with missing confidence signal",
            "logprobs": [],
            "done": True,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3-vl:4b", "digest": EXPECTED_DIGEST}]},
                )
            return httpx.Response(200, json=mock_payload)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = OllamaModelProvider(
            profile="desktop-gpu",
            base_url="http://mock-ollama:11434",
            http_client=client,
        )

        image_bytes = _create_synthetic_page_png()
        reading = await provider.transcribe_page(
            role=ModelRole.VISION,
            image=image_bytes,
        )

        assert reading.reading == "Page content with missing confidence signal"
        assert reading.confidence is None

    @pytest.mark.asyncio
    async def test_transcribe_page_timeout_raises_transient_error(self) -> None:
        """Network timeout raises TransientIngestionError so the worker retries."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3-vl:4b", "digest": EXPECTED_DIGEST}]},
                )
            raise httpx.ReadTimeout("Connection timed out")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = OllamaModelProvider(
            profile="desktop-gpu",
            base_url="http://mock-ollama:11434",
            http_client=client,
        )

        image_bytes = _create_synthetic_page_png()
        with pytest.raises(TransientIngestionError) as exc_info:
            await provider.transcribe_page(
                role=ModelRole.VISION,
                image=image_bytes,
            )

        assert exc_info.value.retryable is True
        assert exc_info.value.error_code == "OLLAMA_TIMEOUT"

    @pytest.mark.asyncio
    async def test_transcribe_page_connect_error_raises_transient_error(self) -> None:
        """Connection failure raises TransientIngestionError so the worker retries."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3-vl:4b", "digest": EXPECTED_DIGEST}]},
                )
            raise httpx.ConnectError("Failed to connect to host")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = OllamaModelProvider(
            profile="desktop-gpu",
            base_url="http://mock-ollama:11434",
            http_client=client,
        )

        image_bytes = _create_synthetic_page_png()
        with pytest.raises(TransientIngestionError) as exc_info:
            await provider.transcribe_page(
                role=ModelRole.VISION,
                image=image_bytes,
            )

        assert exc_info.value.retryable is True
        assert exc_info.value.error_code == "OLLAMA_CONNECTION_ERROR"

    @pytest.mark.asyncio
    async def test_transcribe_page_http_error_raises_permanent_error(self) -> None:
        """HTTP 5xx / 4xx raises typed permanent error with retryable=False."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3-vl:4b", "digest": EXPECTED_DIGEST}]},
                )
            return httpx.Response(500, text="Internal Ollama Error")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = OllamaModelProvider(
            profile="desktop-gpu",
            base_url="http://mock-ollama:11434",
            http_client=client,
        )

        image_bytes = _create_synthetic_page_png()
        with pytest.raises(OllamaPermanentError) as exc_info:
            await provider.transcribe_page(
                role=ModelRole.VISION,
                image=image_bytes,
            )

        assert exc_info.value.retryable is False
        assert exc_info.value.error_code == "OLLAMA_HTTP_ERROR"

    @pytest.mark.asyncio
    async def test_transcribe_page_malformed_json_raises_permanent_error(self) -> None:
        """Non-JSON response raises typed permanent error."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3-vl:4b", "digest": EXPECTED_DIGEST}]},
                )
            return httpx.Response(200, text="<html>502 Bad Gateway</html>")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = OllamaModelProvider(
            profile="desktop-gpu",
            base_url="http://mock-ollama:11434",
            http_client=client,
        )

        image_bytes = _create_synthetic_page_png()
        with pytest.raises(MalformedOutput) as exc_info:
            await provider.transcribe_page(
                role=ModelRole.VISION,
                image=image_bytes,
            )

        assert exc_info.value.retryable is False
        assert exc_info.value.error_code == "MALFORMED_OUTPUT"

    @pytest.mark.asyncio
    async def test_transcribe_page_missing_response_field_raises_permanent_error(self) -> None:
        """JSON missing 'response' field raises MalformedOutput."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3-vl:4b", "digest": EXPECTED_DIGEST}]},
                )
            return httpx.Response(200, json={"done": True})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = OllamaModelProvider(
            profile="desktop-gpu",
            base_url="http://mock-ollama:11434",
            http_client=client,
        )

        image_bytes = _create_synthetic_page_png()
        with pytest.raises(MalformedOutput) as exc_info:
            await provider.transcribe_page(
                role=ModelRole.VISION,
                image=image_bytes,
            )

        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_unimplemented_methods_raise_typed_error(self) -> None:
        """Methods not needed for page processing yet raise typed error with fixed message."""
        provider = OllamaModelProvider(profile="desktop-gpu", base_url="http://mock-ollama:11434")

        with pytest.raises(OllamaMethodNotImplementedError) as exc1:
            await provider.generate(
                role=ModelRole.ANSWER,
                messages=[ChatMessage(role="user", content="hello")],
            )
        assert exc1.value.message == "Ollama provider: generate not implemented yet"
        assert exc1.value.error_code == "METHOD_NOT_IMPLEMENTED"

        with pytest.raises(OllamaMethodNotImplementedError) as exc2:
            await provider.read_page(
                role=ModelRole.VISION,
                image=b"img",
                question="What is this?",
            )
        assert exc2.value.message == "Ollama provider: read_page not implemented yet"
        assert exc2.value.error_code == "METHOD_NOT_IMPLEMENTED"

        with pytest.raises(OllamaMethodNotImplementedError) as exc3:
            await provider.extract_fields(
                role=ModelRole.EXTRACT,
                image=b"img",
                schema=DummySchema,
            )
        assert exc3.value.message == "Ollama provider: extract_fields not implemented yet"
        assert exc3.value.error_code == "METHOD_NOT_IMPLEMENTED"

    def test_wiring_desktop_gpu_and_home_gpu_creates_ollama_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """create_model_provider returns OllamaModelProvider for desktop-gpu and home-gpu only."""
        from app.wiring import create_model_provider

        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

        provider_desktop = create_model_provider("desktop-gpu")
        assert isinstance(provider_desktop, OllamaModelProvider)

        provider_home = create_model_provider("home-gpu")
        assert isinstance(provider_home, OllamaModelProvider)

        with pytest.raises(ConfigurationError):
            create_model_provider("office-cpu")

        with pytest.raises(ConfigurationError):
            create_model_provider("cloud")

    def test_fuzzy_phrase_pattern_matching(self) -> None:
        """Fuzzy phrase matcher handles case, whitespace, and hyphens accurately."""
        prior_auth_pattern = build_fuzzy_phrase_pattern("prior authorization")
        assert prior_auth_pattern.search("PRIOR  AUTHORIZATION") is not None
        assert prior_auth_pattern.search("prior-authorization") is not None
        assert prior_auth_pattern.search("prior auth") is None

        fax_pattern = build_fuzzy_phrase_pattern("fax 555-0199")
        assert fax_pattern.search("FAX 555 0199") is not None

    @pytest.mark.asyncio
    async def test_transcribe_page_payload_includes_deterministic_options(self) -> None:
        """The request payload sent to /api/generate contains deterministic options."""
        captured_requests: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3-vl:4b", "digest": EXPECTED_DIGEST}]},
                )
            if request.method == "POST" and request.url.path == "/api/generate":
                body = json.loads(request.content)
                captured_requests.append(body)
                return httpx.Response(
                    200,
                    json={
                        "response": "Page text",
                        "done": True,
                        "logprobs": [{"token": "Page", "logprob": -0.1}],
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = OllamaModelProvider(
            profile="desktop-gpu",
            base_url="http://mock-ollama:11434",
            http_client=client,
        )

        image_bytes = _create_synthetic_page_png()
        await provider.transcribe_page(
            role=ModelRole.VISION,
            image=image_bytes,
        )

        assert len(captured_requests) == 1
        payload = captured_requests[0]
        assert "options" in payload
        options = payload["options"]
        assert options["temperature"] == TRANSCRIPTION_TEMPERATURE
        assert options["seed"] == TRANSCRIPTION_SEED
        assert options["top_k"] == TRANSCRIPTION_TOP_K
        assert options["top_p"] == TRANSCRIPTION_TOP_P
        assert options == TRANSCRIPTION_GENERATION_OPTIONS


@pytest.mark.models
@pytest.mark.asyncio
async def test_real_ollama_vision_transcription_desktop_gpu() -> None:
    """Integration test invoking real local Ollama on desktop-gpu profile.

    Uses a synthetic scanned fax page rendered at approximately 200 DPI with known text.
    Fails clearly if OLLAMA_BASE_URL is missing.
    Marked @pytest.mark.models. Do NOT run in regular suite.
    """
    import os

    if "OLLAMA_BASE_URL" not in os.environ:
        pytest.fail("OLLAMA_BASE_URL environment variable is not set for real model test")

    base_url = os.environ["OLLAMA_BASE_URL"].strip()
    if len(base_url) == 0:
        pytest.fail("OLLAMA_BASE_URL environment variable is empty for real model test")

    provider = OllamaModelProvider(
        profile="desktop-gpu",
        base_url=base_url,
    )

    image_bytes = _create_synthetic_fax_page_200dpi_png()
    reading = await provider.transcribe_page(
        role=ModelRole.VISION,
        image=image_bytes,
    )

    assert isinstance(reading, PageReading)
    text = reading.reading
    assert len(text.strip()) > 0
    assert reading.confidence is not None

    print(f"\n--- Synthetic Fax Reading ---\n{text}\n")
    print(f"--- Confidence: {reading.confidence} ---\n")

    required_phrases = (
        "prior authorization",
        "member id zx4471902",
        "fax 555-0199",
    )
    for phrase in required_phrases:
        pattern = build_fuzzy_phrase_pattern(phrase)
        assert pattern.search(text) is not None
