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

import asyncio
import base64
import json
import logging
import math
import os
import re
from collections.abc import Mapping
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
from app.ports.model_provider import (
    ModelCapabilities,
    ModelProvider,
    is_cloud_model,
    is_vision_model,
)
from app.ports.types import (
    ChatMessage,
    ExtractedFields,
    ModelResponse,
    ModelRole,
    PageReading,
)

logger = logging.getLogger(__name__)

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


def _load_profile_generation_limits(
    profile: str, config_path: Path | None = None
) -> dict[str, dict[str, int]]:
    """Load optional per-model/per-role output token limits from a profile YAML file."""
    if config_path is not None:
        target_path = config_path
    else:
        backend_root = Path(__file__).resolve().parent.parent.parent.parent
        target_path = backend_root / "config" / "profiles" / (profile + ".yaml")

    try:
        with open(target_path, encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
    except Exception as exc:
        raise ConfigurationError(
            message="Malformed profile generation limits",
            error_code="CONFIGURATION_ERROR",
        ) from exc

    section = parsed.get("generation_limits", {}) if isinstance(parsed, dict) else {}
    if not isinstance(section, dict):
        raise ConfigurationError(
            message="Malformed profile generation limits",
            error_code="CONFIGURATION_ERROR",
        )

    limits: dict[str, dict[str, int]] = {}
    for model_name, role_limits in section.items():
        if not isinstance(model_name, str) or not isinstance(role_limits, dict):
            raise ConfigurationError(
                message="Malformed profile generation limits",
                error_code="CONFIGURATION_ERROR",
            )
        limits[model_name] = {}
        for role_name, token_limit in role_limits.items():
            if (
                role_name not in {role.value for role in ModelRole}
                or not isinstance(token_limit, int)
                or isinstance(token_limit, bool)
                or token_limit <= 0
            ):
                raise ConfigurationError(
                    message="Malformed profile generation limits",
                    error_code="CONFIGURATION_ERROR",
                )
            limits[model_name][role_name] = token_limit
    return limits


class OllamaModelProvider(ModelProvider):
    """ModelProvider adapter communicating with local Ollama service."""

    def __init__(
        self,
        profile: str,
        capabilities_map: Mapping[tuple[str, str], ModelCapabilities],
        base_url: str | None = None,
        config_path: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
        image_processor: ImageProcessor | None = None,
        timeout_seconds: float = 120.0,
        chat_model: str | None = None,
        vision_model: str | None = None,
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
        self._capabilities_map = capabilities_map
        self._models = _load_profile_models(profile=profile, config_path=config_path)
        self._generation_limits = _load_profile_generation_limits(
            profile=profile, config_path=config_path
        )

        # Allow configured chat_model setting or CHAT_MODEL env var to override
        # chat and generation roles
        configured_chat_model = chat_model or os.getenv("CHAT_MODEL")
        if configured_chat_model and configured_chat_model.strip():
            cm = configured_chat_model.strip()
            self._models["answer"] = cm
            self._models["router"] = cm
            self._models["judge"] = cm
            self._models["extract"] = cm
            if is_vision_model(cm, capabilities_map=self._capabilities_map, provider="ollama"):
                self._models["vision"] = cm
            # Note: If cm has no vision, vision role retains profile model (e.g. qwen3-vl:4b)

        if vision_model is not None:
            if not vision_model.strip():
                raise ConfigurationError(
                    message="VISION_MODEL setting is empty",
                    error_code="CONFIGURATION_ERROR",
                )
            self._models["vision"] = vision_model.strip()

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

        if is_cloud_model(model_name, capabilities_map=self._capabilities_map, provider="ollama"):
            digest_val = f"cloud:{model_name}"
            self._model_digests[model_name] = digest_val
            return digest_val

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
        role_str = role.value if hasattr(role, "value") else str(role)
        if role_str not in self._models:
            raise OllamaPermanentError(
                message="Role not configured in profile models",
                error_code="MODEL_ROLE_NOT_CONFIGURED",
            )
        model_name = self._models[role_str]
        model_id = model_name
        configured_token_limit = self._generation_limits.get(model_name, {}).get(role_str)
        if configured_token_limit is not None:
            max_tokens = max(max_tokens or 0, configured_token_limit)

        if "qwen" in model_name.lower():
            chat_messages = [{"role": m.role, "content": m.content} for m in messages]
        else:
            # Prefill </think> is tailored for qwen-vl; filter it for other models (e.g. beam:cloud)
            chat_messages = [
                {"role": m.role, "content": m.content}
                for m in messages
                if not (m.role == "assistant" and m.content.strip() == "</think>")
            ]

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": chat_messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.0,
                "seed": 42,
            },
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        if schema is not None:
            payload["format"] = schema.model_json_schema()

        headers: dict[str, str] = {}
        api_key = os.getenv("OLLAMA_API_KEY")
        if api_key and api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"

        url = self._base_url + "/api/chat"

        client_to_close: httpx.AsyncClient | None = None
        if self._http_client is not None:
            client = self._http_client
        else:
            client = httpx.AsyncClient(timeout=self._timeout_seconds)
            client_to_close = client

        max_attempts = 3
        backoff_base = 0.2
        last_transient_exc: Exception | None = None
        response: httpx.Response | None = None

        try:
            model_version = await self._get_model_digest(model_name=model_name, client=client)
            for attempt in range(max_attempts):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                    break
                except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                    last_transient_exc = exc
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(backoff_base * (2**attempt))
                        continue
                    raise TransientIngestionError(
                        message="Ollama request timed out after retries",
                        error_code="OLLAMA_TIMEOUT",
                    ) from exc
            else:
                raise TransientIngestionError(
                    message="Ollama request timed out after retries",
                    error_code="OLLAMA_TIMEOUT",
                ) from last_transient_exc
        finally:
            if client_to_close is not None:
                await client_to_close.aclose()

        if response is None:
            raise TransientIngestionError(
                message="Ollama request timed out after retries",
                error_code="OLLAMA_TIMEOUT",
            )

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

        if "message" not in data or not isinstance(data["message"], dict):
            raise MalformedOutput(
                message="Ollama response missing required 'message' object",
                error_code="MALFORMED_OUTPUT",
            )

        msg_obj = data["message"]
        raw_content = str(msg_obj.get("content", ""))
        if not raw_content:
            logger.error("Ollama returned empty raw_content! Full response data: %r", data)
        cleaned_text = re.sub(r"<think>[\s\S]*?</think>", "", raw_content).strip()

        structured_dict: dict[str, Any] | None = None
        if schema is not None:
            try:
                clean_json = cleaned_text
                code_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned_text)
                if code_match:
                    clean_json = code_match.group(1).strip()
                elif "{" in clean_json and "}" in clean_json:
                    start = clean_json.find("{")
                    end = clean_json.rfind("}") + 1
                    clean_json = clean_json[start:end]

                try:
                    parsed = json.loads(clean_json)
                except json.JSONDecodeError:
                    # Handle truncated array/object (e.g. explain mode points hitting token ceiling)
                    last_brace = clean_json.rfind("}")
                    if last_brace != -1:
                        candidate = clean_json[: last_brace + 1].strip()
                        if "[" in candidate and not candidate.endswith("]"):
                            candidate += "]}"
                        elif not candidate.endswith("}"):
                            candidate += "}"
                        parsed = json.loads(candidate)
                    else:
                        raise

                if not isinstance(parsed, dict):
                    raise ValueError("Parsed JSON is not an object")
                validated = schema.model_validate(parsed)
                structured_dict = validated.model_dump()
            except Exception as exc:
                raise MalformedOutput(
                    message="Malformed Ollama structured output",
                    error_code="MALFORMED_OUTPUT",
                ) from exc

        usage_tokens: int | None = None
        if "eval_count" in data and isinstance(data["eval_count"], int):
            usage_tokens = data["eval_count"]

        return ModelResponse(
            content=cleaned_text,
            model_id=model_id,
            model_version=model_version,
            structured_output=structured_dict,
            usage_tokens=usage_tokens,
        )

    async def read_page(
        self,
        role: ModelRole,
        image: bytes,
        question: str = "",
    ) -> PageReading:
        """Perform multimodal visual reading of a single page image at question time."""
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

        prompt_text = (
            "Examine the attached page image carefully.\n"
            f"Question: {question}\n"
            "Transcribe visible text relevant to the question "
            "and provide a direct factual answer.\n"
            "State clearly if the target region is illegible or unreadable."
        )

        payload: dict[str, Any] = {
            "model": model_name,
            "prompt": prompt_text,
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
            normalized_values={},
        )

    async def extract_fields(
        self,
        role: ModelRole,
        image: bytes,
        schema: type[BaseModel],
    ) -> ExtractedFields:
        del role, image, schema
        raise OllamaMethodNotImplementedError("Ollama provider: extract_fields not implemented yet")
