"""BGE cross-encoder reranker adapter for candidate scoring and reranking.

Wraps BAAI/bge-reranker-v2-m3 using sentence_transformers.CrossEncoder.
Runs on CPU by default per spec profile configuration to avoid VRAM contention.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Protocol, cast

from app.ports.errors import (
    ConfigurationError,
    RerankerOutputInvalidError,
)
from app.ports.reranker import Reranker
from app.ports.types import RerankCandidate, RerankResult

ALLOWED_DEVICES: frozenset[str] = frozenset({"cpu", "cuda"})
REQUIRED_CONFIG_KEYS: tuple[str, ...] = ("model", "device")
DEFAULT_MAX_LENGTH: int = 1024
DEFAULT_BATCH_SIZE: int = 32


class CrossEncoderModelProtocol(Protocol):
    """Protocol for cross-encoder models decoupling adapter from vendor implementation."""

    def predict(
        self,
        sentences: list[list[str]],
        *,
        batch_size: int = ...,
        show_progress_bar: bool = ...,
        **kwargs: Any,
    ) -> Any: ...


@dataclass(frozen=True)
class BgeRerankerConfig:
    """Validated configuration for BGE cross-encoder reranker."""

    model: str
    device: str = "cpu"
    max_length: int = DEFAULT_MAX_LENGTH
    batch_size: int = DEFAULT_BATCH_SIZE


def validate_bge_reranker_config(raw_config: dict[str, Any]) -> BgeRerankerConfig:
    """Validate profile configuration dictionary for reranker."""
    if not isinstance(raw_config, dict):
        raise ConfigurationError(
            message="Malformed profile configuration for reranker",
            error_code="CONFIGURATION_ERROR",
        )

    for key in REQUIRED_CONFIG_KEYS:
        if key not in raw_config:
            raise ConfigurationError(
                message=f"Profile configuration missing required reranker key: {key}",
                error_code="CONFIGURATION_ERROR",
            )

    model_val = raw_config["model"]
    if not isinstance(model_val, str) or not model_val.strip():
        raise ConfigurationError(
            message="Invalid model identifier in reranker profile configuration",
            error_code="CONFIGURATION_ERROR",
        )

    device_val = raw_config["device"]
    if not isinstance(device_val, str) or device_val not in ALLOWED_DEVICES:
        raise ConfigurationError(
            message="Invalid or unsupported device in reranker configuration",
            error_code="CONFIGURATION_ERROR",
        )

    if device_val == "cuda":
        import torch

        if not torch.cuda.is_available():
            raise ConfigurationError(
                message="CUDA requested for reranker but torch.cuda is not available",
                error_code="CONFIGURATION_ERROR",
            )

    max_length_val = raw_config.get("max_length", DEFAULT_MAX_LENGTH)
    if not isinstance(max_length_val, int) or max_length_val <= 0:
        raise ConfigurationError(
            message="Invalid max_length in reranker profile configuration",
            error_code="CONFIGURATION_ERROR",
        )

    batch_size_val = raw_config.get("batch_size", DEFAULT_BATCH_SIZE)
    if not isinstance(batch_size_val, int) or batch_size_val <= 0:
        raise ConfigurationError(
            message="Invalid batch_size in reranker profile configuration",
            error_code="CONFIGURATION_ERROR",
        )

    return BgeRerankerConfig(
        model=model_val.strip(),
        device=device_val,
        max_length=max_length_val,
        batch_size=batch_size_val,
    )


_LOADED_RERANKERS: dict[tuple[str, str, int], CrossEncoderModelProtocol] = {}


def load_bge_reranker_model(config: BgeRerankerConfig) -> CrossEncoderModelProtocol:
    """Load BGE CrossEncoder model once per process."""
    cache_key = (config.model, config.device, config.max_length)
    if cache_key in _LOADED_RERANKERS:
        return _LOADED_RERANKERS[cache_key]

    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise ConfigurationError(
            message="sentence-transformers package is required for real reranking",
            error_code="CONFIGURATION_ERROR",
        ) from exc

    try:
        loaded = CrossEncoder(
            config.model,
            max_length=config.max_length,
            device=config.device,
        )
    except Exception as exc:
        raise ConfigurationError(
            message="Reranker model files not found locally or failed to load",
            error_code="CONFIGURATION_ERROR",
        ) from exc

    protocol_model = cast(CrossEncoderModelProtocol, loaded)
    _LOADED_RERANKERS[cache_key] = protocol_model
    return protocol_model


class BgeRerankerAdapter(Reranker):
    """Adapter wrapping CrossEncoder for candidate reranking."""

    def __init__(
        self,
        model: CrossEncoderModelProtocol,
        config: BgeRerankerConfig,
    ) -> None:
        self._model = model
        self._config = config

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
    ) -> list[RerankResult]:
        """Score candidate pairs against query and return in descending relevance order."""
        if not candidates:
            return []

        pairs = [[query, c.text] for c in candidates]

        raw_scores = await asyncio.to_thread(
            self._model.predict,
            pairs,
            batch_size=self._config.batch_size,
        )

        if len(raw_scores) != len(candidates):
            raise RerankerOutputInvalidError(
                message="Reranker output score count mismatch",
                code="RERANKER_OUTPUT_INVALID",
                details={
                    "actual_count": len(raw_scores),
                    "expected_count": len(candidates),
                },
            )

        scored_candidates: list[tuple[str, float]] = []
        for cand, raw_val in zip(candidates, raw_scores, strict=False):
            score = float(raw_val)
            if not math.isfinite(score):
                raise RerankerOutputInvalidError(
                    message="Reranker produced non-finite score",
                    code="RERANKER_OUTPUT_INVALID",
                    details={"candidate_id": cand.candidate_id, "score": raw_val},
                )
            scored_candidates.append((cand.candidate_id, score))

        # Sort descending by score
        scored_candidates.sort(key=lambda item: item[1], reverse=True)

        return [
            RerankResult(
                candidate_id=cand_id,
                score=score,
                rank=rank,
            )
            for rank, (cand_id, score) in enumerate(scored_candidates, start=1)
        ]


def create_bge_reranker_adapter(
    profile: str,
    config_path: Any | None = None,
) -> BgeRerankerAdapter:
    """Factory creating BgeRerankerAdapter for configured profiles."""
    from pathlib import Path

    import yaml

    if config_path is not None:
        target_path = Path(config_path)
    else:
        backend_root = Path(__file__).resolve().parent.parent.parent.parent
        target_path = backend_root / "config" / "profiles" / f"{profile}.yaml"

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

    if not isinstance(parsed, dict) or "reranker" not in parsed:
        raise ConfigurationError(
            message="Profile configuration missing reranker section",
            error_code="CONFIGURATION_ERROR",
        )

    config = validate_bge_reranker_config(parsed["reranker"])
    model = load_bge_reranker_model(config)
    return BgeRerankerAdapter(model=model, config=config)
