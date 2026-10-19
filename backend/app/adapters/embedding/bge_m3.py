"""BGE-M3 embedding provider adapter (Adapter 3).

Provides dense vector embeddings for knowledge chunks and search queries using BAAI/bge-m3.
Enforces:
1. Profile configuration validation (model, pinned revision, device, max_seq_length).
2. Offline-only model loading with local_files_only=True and pinned revision.
3. Token count validation before encoding against max_seq_length (no silent truncation).
4. Output validation (count matching input count, 1024 dimensions, all values finite).
5. Asynchronous thread-pool execution via asyncio.to_thread with BATCH_SIZE=32.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from app.ports.embedding_provider import EmbeddingProvider
from app.ports.errors import (
    ConfigurationError,
    EmbeddingInputTooLongError,
    EmbeddingOutputInvalidError,
)

BATCH_SIZE: int = 32
EMBEDDING_DIMENSION: int = 1024
ALLOWED_DEVICES: frozenset[str] = frozenset({"cpu", "cuda"})
REQUIRED_CONFIG_KEYS: tuple[str, ...] = ("model", "revision", "device", "max_seq_length")


class TokenizerProtocol(Protocol):
    """Protocol for tokenizers providing text tokenization without vendor dependencies."""

    def encode(self, text: str, **kwargs: Any) -> list[int]: ...


class SentenceTransformerModelProtocol(Protocol):
    """Protocol for sentence_transformers models decoupling adapter from concrete implementation."""

    @property
    def tokenizer(self) -> TokenizerProtocol: ...

    def encode(
        self,
        inputs: Any,
        *,
        batch_size: int = ...,
        normalize_embeddings: bool = ...,
        **kwargs: Any,
    ) -> Any: ...


@dataclass(frozen=True)
class BgeM3Config:
    """Validated configuration for BGE-M3 embedding adapter."""

    model: str
    revision: str
    device: str
    max_seq_length: int


def validate_bge_m3_config(raw_config: dict[str, Any]) -> BgeM3Config:
    """Validate profile configuration dictionary for BGE-M3 embedding model."""
    if not isinstance(raw_config, dict):
        raise ConfigurationError(
            message="Malformed profile configuration for embeddings",
            error_code="CONFIGURATION_ERROR",
        )

    for key in REQUIRED_CONFIG_KEYS:
        if key not in raw_config:
            raise ConfigurationError(
                message="Profile configuration missing required embedding key",
                error_code="CONFIGURATION_ERROR",
            )

    model_val = raw_config["model"]
    if not isinstance(model_val, str) or not model_val.strip():
        raise ConfigurationError(
            message="Invalid model identifier in embedding profile configuration",
            error_code="CONFIGURATION_ERROR",
        )

    revision_val = raw_config["revision"]
    if not isinstance(revision_val, str) or not revision_val.strip():
        raise ConfigurationError(
            message="Invalid revision hash in embedding profile configuration",
            error_code="CONFIGURATION_ERROR",
        )

    device_val = raw_config["device"]
    if not isinstance(device_val, str):
        raise ConfigurationError(
            message="Invalid device in embedding profile configuration",
            error_code="CONFIGURATION_ERROR",
        )

    if device_val not in ALLOWED_DEVICES:
        raise ConfigurationError(
            message="Unsupported device for embedding model",
            error_code="CONFIGURATION_ERROR",
        )

    if device_val == "cuda":
        import torch

        if not torch.cuda.is_available():
            raise ConfigurationError(
                message="CUDA requested for embedding but torch.cuda is not available",
                error_code="CONFIGURATION_ERROR",
            )

    max_seq_len_val = raw_config["max_seq_length"]
    if not isinstance(max_seq_len_val, int) or max_seq_len_val <= 0:
        raise ConfigurationError(
            message="Invalid max_seq_length for embedding model",
            error_code="CONFIGURATION_ERROR",
        )

    return BgeM3Config(
        model=model_val.strip(),
        revision=revision_val.strip(),
        device=device_val,
        max_seq_length=max_seq_len_val,
    )


_LOADED_MODELS: dict[tuple[str, str, str], SentenceTransformerModelProtocol] = {}


def load_bge_m3_model(config: BgeM3Config) -> SentenceTransformerModelProtocol:
    """Load BGE-M3 SentenceTransformer model once per process strictly offline."""
    cache_key = (config.model, config.revision, config.device)
    if cache_key in _LOADED_MODELS:
        return _LOADED_MODELS[cache_key]

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ConfigurationError(
            message="sentence-transformers package is required for real embeddings",
            error_code="CONFIGURATION_ERROR",
        ) from exc

    try:
        loaded = SentenceTransformer(
            config.model,
            revision=config.revision,
            local_files_only=True,
            device=config.device,
        )
    except Exception as exc:
        raise ConfigurationError(
            message="Embedding model files not found locally",
            error_code="CONFIGURATION_ERROR",
        ) from exc

    _LOADED_MODELS[cache_key] = loaded
    return loaded


class BgeM3EmbeddingProvider(EmbeddingProvider):
    """Adapter wrapping BAAI/bge-m3 dense vector embedding model."""

    def __init__(
        self,
        model: SentenceTransformerModelProtocol,
        config: BgeM3Config,
    ) -> None:
        self._model = model
        self._config = config

    @property
    def model_id(self) -> str:
        """Identifier of the active embedding model."""
        return self._config.model

    @property
    def version(self) -> str:
        """Version of the active embedding model (pinned revision hash)."""
        return self._config.revision

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Compute 1024-dimensional normalized vector embeddings for a list of text strings."""
        if not texts:
            return []

        tokenizer = self._model.tokenizer
        for text in texts:
            tokens = tokenizer.encode(text)
            if len(tokens) > self._config.max_seq_length:
                raise EmbeddingInputTooLongError(
                    message="Embedding input token length exceeds maximum sequence length",
                    code="EMBEDDING_INPUT_TOO_LONG",
                    details={
                        "token_count": len(tokens),
                        "max_seq_length": self._config.max_seq_length,
                    },
                )

        raw_output = await asyncio.to_thread(
            self._model.encode,
            texts,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
        )

        if len(raw_output) != len(texts):
            raise EmbeddingOutputInvalidError(
                message="Embedding provider output count mismatch",
                code="EMBEDDING_OUTPUT_INVALID",
            )

        result: list[list[float]] = []
        for item in raw_output:
            vec = [float(val) for val in item]
            if len(vec) != EMBEDDING_DIMENSION:
                raise EmbeddingOutputInvalidError(
                    message="Embedding vector length must be 1024",
                    code="EMBEDDING_OUTPUT_INVALID",
                    details={
                        "actual_dimension": len(vec),
                        "expected_dimension": EMBEDDING_DIMENSION,
                    },
                )
            for val in vec:
                if not math.isfinite(val):
                    raise EmbeddingOutputInvalidError(
                        message="Embedding vector contains non-finite values",
                        code="EMBEDDING_OUTPUT_INVALID",
                    )
            result.append(vec)

        return result


def create_bge_m3_provider(
    profile: str,
    config_path: Path | None = None,
) -> BgeM3EmbeddingProvider:
    """Factory creating BgeM3EmbeddingProvider for desktop-gpu and home-gpu profiles."""
    if config_path is not None:
        target_path = config_path
    else:
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

    if not isinstance(parsed, dict) or "embeddings" not in parsed:
        raise ConfigurationError(
            message="Profile configuration missing embeddings section",
            error_code="CONFIGURATION_ERROR",
        )

    if profile in {"desktop-gpu", "home-gpu"}:
        import os

        if "HF_HUB_OFFLINE" not in os.environ or os.environ["HF_HUB_OFFLINE"] != "1":
            raise ConfigurationError(
                message=(
                    "HF_HUB_OFFLINE must be set to '1' for real profiles. "
                    "Offline mode is mandatory."
                ),
                error_code="HF_HUB_OFFLINE_REQUIRED",
            )

    config = validate_bge_m3_config(parsed["embeddings"])
    model = load_bge_m3_model(config)
    return BgeM3EmbeddingProvider(model=model, config=config)
