"""Ollama ModelProvider adapter for local GPU runtime profiles (Task Phase 2).

Implements the ModelProvider port using the local Ollama HTTP REST API for desktop-gpu
and home-gpu profiles.

Key Architectural Guarantees:
- profile is a required parameter with no default.
- Base URL from OLLAMA_BASE_URL environment variable, required, no default.
- Model names loaded from config/profiles/<profile>.yaml via yaml.safe_load.
- transcribe_page scales images with ImageProcessor to fit 6 GB VRAM (RTX 3050).
- think: False parameter sent to Ollama API and any <think>...</think> blocks stripped from output.
- model_version uses the model digest reported by Ollama from /api/tags, cached per provider.
- Confidence: geometric mean of token probabilities exp(mean(logprob)) over text tokens,
  excluding structural and special tokens. Returns None if logprobs missing/empty.
- Timeouts and connection errors raise TransientIngestionError (worker retryable).
- HTTP errors and malformed output raise typed permanent errors (retryable=False).
- Methods not needed for page processing yet raise OllamaMethodNotImplementedError.
- Fixed error messages across all exceptions; zero f-strings.
"""

from __future__ import annotations

import base64
import math
import os
import re
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel

from app.adapters.images.pillow import PillowImageProcessor
from app.ports.errors import (
    ConfigurationError,
    MalformedOutput,
    PortError,
    TransientIngestionError,
)
from app.ports.image_processor import ImageProcessor
from app.ports.model_provider import ModelProvider
from app.ports.types import (
    ChatMessage,
    ExtractedFields,
    ModelResponse,
    ModelRole,
    PageReading,
)

DEFAULT_TRANSCRIPTION_PROMPT: str = (
    "Transcribe all visible text from this document page image verbatim. "
    "Output ONLY the text appearing on the page. "
    "Do NOT include any preamble, introduction, markdown explanation, thinking, "
    "notes, or commentary. "
    "Maintain original layout, headings, tables, numbers, and formatting "
    "exactly as shown on the page."
)

# Special / structural tokens excluded from geometric mean confidence calculation.
# These tokens represent chat template formatting, reasoning boundaries, or tokenizer control
# codes rather than text on the document page. Including them skews the confidence score.
SPECIAL_CONTROL_TOKENS: frozenset[str] = frozenset(
    {
        "<s>",
        "</s>",
        "<bos>",
        "<eos>",
        "<pad>",
        "<unk>",
        "<think>",
        "</think>",
    }
)

# Named constants for deterministic page transcription generation options.
# Source: Ollama Modelfile documentation (docs/modelfile.md - "Valid Parameters and Values"):
# - temperature: float (default 0.8) - "The temperature of the model. Increasing the
#   temperature will make the model answer more creatively." Set to 0.0 for greedy argmax sampling.
# - seed: int (default 0) - "Sets the random number seed to use for generation. Setting this to
#   a specific number will make the model generate the same text for the same prompt."
#   Set to a fixed integer (e.g. 42).
# - top_k: int (default 40) - "Reduces the probability of generating nonsense. A higher value
#   (e.g. 100) will give more diverse answers, while a lower value (e.g. 10) will be more
#   conservative." Set to 1 to restrict candidate sampling strictly to the top token.
# - top_p: float (default 0.9) - "Works together with top-k. A higher value (e.g. 0.95) will
#   lead to more diverse text, while a lower value (e.g. 0.5) will generate more focused and
#   conservative text."
TRANSCRIPTION_TEMPERATURE: float = 0.0
TRANSCRIPTION_SEED: int = 42
TRANSCRIPTION_TOP_K: int = 1
TRANSCRIPTION_TOP_P: float = 1.0

TRANSCRIPTION_GENERATION_OPTIONS: dict[str, Any] = {
    "temperature": TRANSCRIPTION_TEMPERATURE,
    "seed": TRANSCRIPTION_SEED,
    "top_k": TRANSCRIPTION_TOP_K,
    "top_p": TRANSCRIPTION_TOP_P,
}


class OllamaPermanentError(PortError):
    """Raised when Ollama returns an unrecoverable bad response or HTTP client error."""

    def __init__(
        self,
        message: str = "Ollama permanent error",
        error_code: str = "OLLAMA_PERMANENT_ERROR",
        status_code: int = 502,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=status_code,
            retryable=False,
        )


class OllamaMethodNotImplementedError(PortError):
    """Raised when an OllamaModelProvider method is not yet implemented for page processing."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message=message,
            error_code="METHOD_NOT_IMPLEMENTED",
            status_code=501,
            retryable=False,
        )


def _is_special_token(token_str: str) -> bool:
    """Determine whether a token is a special/control token to exclude from confidence."""
    if token_str.startswith("<|") and token_str.endswith("|>"):
        return True
    return token_str in SPECIAL_CONTROL_TOKENS


def _calculate_confidence_from_logprobs(
    logprobs_list: list[dict[str, Any]],
) -> float | None:
    """Calculate geometric mean confidence exp(mean(logprob)) over text tokens."""
    if len(logprobs_list) == 0:
        return None

    valid_logprobs: list[float] = []
    for item in logprobs_list:
        if "token" not in item:
            continue
        token_str = str(item["token"])
        if _is_special_token(token_str):
            continue
        if "logprob" in item:
            val = float(item["logprob"])
            valid_logprobs.append(val)

    if len(valid_logprobs) == 0:
        return None

    mean_lp = sum(valid_logprobs) / len(valid_logprobs)
    score = math.exp(mean_lp)
    if score > 1.0:
        return 1.0
    if score < 0.0:
        return 0.0
    return score


def _load_profile_models(profile: str, config_path: Path | None = None) -> dict[str, str]:
    """Parse role model assignments from config/profiles/{profile}.yaml using PyYAML."""
    if config_path is not None:
        target_path = config_path
    else:
        # backend root is 4 levels up: app/adapters/models/ollama.py -> backend
        backend_root = Path(__file__).resolve().parent.parent.parent.parent
        target_path = backend_root / "config" / "profiles" / (profile + ".yaml")

    if not target_path.is_file():
        raise ConfigurationError(
            message="Profile configuration file not found",
            error_code="CONFIGURATION_ERROR",
        )

    try:
        with open(target_path, encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
    except Exception as exc:
        raise ConfigurationError(
            message="Malformed profile configuration file",
            error_code="CONFIGURATION_ERROR",
        ) from exc

    if not isinstance(parsed, dict):
        raise ConfigurationError(
            message="Malformed profile configuration file",
            error_code="CONFIGURATION_ERROR",
        )

    if "models" not in parsed:
        raise ConfigurationError(
            message="Malformed profile configuration file",
            error_code="CONFIGURATION_ERROR",
        )

    models_section = parsed["models"]
    if not isinstance(models_section, dict):
        raise ConfigurationError(
            message="Malformed profile configuration file",
            error_code="CONFIGURATION_ERROR",
        )

    models_by_role: dict[str, str] = {}
    for role_name, role_config in models_section.items():
        if not isinstance(role_config, dict):
            raise ConfigurationError(
                message="Malformed profile configuration file",
                error_code="CONFIGURATION_ERROR",
            )
        if "model" not in role_config:
            raise ConfigurationError(
                message="Malformed profile configuration file",
                error_code="CONFIGURATION_ERROR",
            )
        model_val = role_config["model"]
        if not isinstance(model_val, str) or len(model_val.strip()) == 0:
            raise ConfigurationError(
                message="Malformed profile configuration file",
                error_code="CONFIGURATION_ERROR",
            )
        models_by_role[str(role_name)] = model_val.strip()

    return models_by_role


class OllamaModelProvider(ModelProvider):
    """ModelProvider adapter communicating with local Ollama service."""

    def __init__(
        self,
        profile: str,
        base_url: str | None = None,
        config_path: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
        image_processor: ImageProcessor | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if base_url is not None:
            cleaned_url = base_url.strip()
            if len(cleaned_url) == 0:
                raise ConfigurationError(
                    message="OLLAMA_BASE_URL environment variable is not set",
                    error_code="CONFIGURATION_ERROR",
                )
            self._base_url = cleaned_url.rstrip("/")
        else:
            if "OLLAMA_BASE_URL" not in os.environ:
                raise ConfigurationError(
                    message="OLLAMA_BASE_URL environment variable is not set",
                    error_code="CONFIGURATION_ERROR",
                )
            env_url = os.environ["OLLAMA_BASE_URL"].strip()
            if len(env_url) == 0:
                raise ConfigurationError(
                    message="OLLAMA_BASE_URL environment variable is not set",
                    error_code="CONFIGURATION_ERROR",
                )
            self._base_url = env_url.rstrip("/")

        self._profile = profile
        self._models = _load_profile_models(profile=profile, config_path=config_path)

        if image_processor is not None:
            self._image_processor = image_processor
        else:
            self._image_processor = PillowImageProcessor()

        self._http_client = http_client
        self._timeout_seconds = timeout_seconds
        self._model_digests: dict[str, str] = {}

    async def _get_model_digest(self, model_name: str, client: httpx.AsyncClient) -> str:
        """Fetch model digest from /api/tags, cached per provider instance."""
        if model_name in self._model_digests:
            return self._model_digests[model_name]

        url = self._base_url + "/api/tags"
        try:
            response = await client.get(url)
        except httpx.TimeoutException as exc:
            raise TransientIngestionError(
                message="Ollama request timed out",
                error_code="OLLAMA_TIMEOUT",
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientIngestionError(
                message="Ollama connection failed",
                error_code="OLLAMA_CONNECTION_ERROR",
            ) from exc
        except httpx.NetworkError as exc:
            raise TransientIngestionError(
                message="Ollama network error",
                error_code="OLLAMA_NETWORK_ERROR",
            ) from exc

        if response.status_code >= 400:
            raise OllamaPermanentError(
                message="Ollama HTTP error",
                error_code="OLLAMA_HTTP_ERROR",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except Exception as exc:
            raise MalformedOutput(
                message="Malformed Ollama JSON response",
                error_code="MALFORMED_OUTPUT",
            ) from exc

        if not isinstance(data, dict):
            raise MalformedOutput(
                message="Ollama response payload must be a JSON object",
                error_code="MALFORMED_OUTPUT",
            )

        if "models" not in data or not isinstance(data["models"], list):
            raise MalformedOutput(
                message="Malformed Ollama JSON response",
                error_code="MALFORMED_OUTPUT",
            )

        for item in data["models"]:
            if not isinstance(item, dict):
                continue
            name_matches = "name" in item and item["name"] == model_name
            model_matches = "model" in item and item["model"] == model_name
            if (name_matches or model_matches) and (
                "digest" in item and isinstance(item["digest"], str)
            ):
                digest_val = item["digest"]
                self._model_digests[model_name] = digest_val
                return digest_val

        raise OllamaPermanentError(
            message="Ollama model digest missing",
            error_code="OLLAMA_DIGEST_MISSING",
        )

    async def transcribe_page(
        self,
        role: ModelRole,
        image: bytes,
    ) -> PageReading:
        """Perform multimodal visual transcription of a page image at upload time."""
        role_str = str(role.value)
        if role_str not in self._models:
            raise ConfigurationError(
                message="Role not configured in profile",
                error_code="CONFIGURATION_ERROR",
            )
        model_name = self._models[role_str]
        model_id = model_name

        # Scale image to fit within 6 GB VRAM budget (RTX 3050)
        scaled_image = self._image_processor.prepare_image_for_vision(
            image_bytes=image,
            max_dimension=1024,
            max_bytes=4 * 1024 * 1024,
        )

        b64_image = base64.b64encode(scaled_image).decode("utf-8")

        # Disable thinking explicitly via think: False for thinking-capable models
        payload: dict[str, Any] = {
            "model": model_name,
            "prompt": DEFAULT_TRANSCRIPTION_PROMPT,
            "images": [b64_image],
            "stream": False,
            "think": False,
            "logprobs": True,
            "options": dict(TRANSCRIPTION_GENERATION_OPTIONS),
        }

        url = self._base_url + "/api/generate"

        client_to_close: httpx.AsyncClient | None = None
        if self._http_client is not None:
            client = self._http_client
        else:
            client = httpx.AsyncClient(timeout=self._timeout_seconds)
            client_to_close = client

        try:
            model_version = await self._get_model_digest(model_name=model_name, client=client)
            response = await client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise TransientIngestionError(
                message="Ollama request timed out",
                error_code="OLLAMA_TIMEOUT",
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientIngestionError(
                message="Ollama connection failed",
                error_code="OLLAMA_CONNECTION_ERROR",
            ) from exc
        except httpx.NetworkError as exc:
            raise TransientIngestionError(
                message="Ollama network error",
                error_code="OLLAMA_NETWORK_ERROR",
            ) from exc
        finally:
            if client_to_close is not None:
                await client_to_close.aclose()

        if response.status_code >= 400:
            raise OllamaPermanentError(
                message="Ollama HTTP error",
                error_code="OLLAMA_HTTP_ERROR",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except Exception as exc:
            raise MalformedOutput(
                message="Malformed Ollama JSON response",
                error_code="MALFORMED_OUTPUT",
            ) from exc

        if not isinstance(data, dict):
            raise MalformedOutput(
                message="Ollama response payload must be a JSON object",
                error_code="MALFORMED_OUTPUT",
            )

        if "response" not in data:
            raise MalformedOutput(
                message="Ollama response missing required 'response' field",
                error_code="MALFORMED_OUTPUT",
            )

        response_text = str(data["response"])

        # Strip any <think>...</think> blocks emitted by thinking-capable models
        cleaned_text = re.sub(r"<think>[\s\S]*?</think>", "", response_text).strip()

        if "logprobs" in data and isinstance(data["logprobs"], list):
            raw_logprobs = data["logprobs"]
            confidence = _calculate_confidence_from_logprobs(raw_logprobs)
        else:
            confidence = None

        return PageReading(
            reading=cleaned_text,
            confidence=confidence,
            model_id=model_id,
            model_version=model_version,
        )

    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        del role, messages, schema, max_tokens
        raise OllamaMethodNotImplementedError("Ollama provider: generate not implemented yet")

    async def read_page(
        self,
        role: ModelRole,
        image: bytes,
        question: str,
    ) -> PageReading:
        del role, image, question
        raise OllamaMethodNotImplementedError("Ollama provider: read_page not implemented yet")

    async def extract_fields(
        self,
        role: ModelRole,
        image: bytes,
        schema: type[BaseModel],
    ) -> ExtractedFields:
        del role, image, schema
        raise OllamaMethodNotImplementedError("Ollama provider: extract_fields not implemented yet")
