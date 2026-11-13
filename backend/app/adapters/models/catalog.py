"""Catalog loader, provider status checker, and connection probe adapter."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

import httpx
import yaml
from pydantic import SecretStr, ValidationError

from app.ports.errors import ConfigurationError
from app.ports.model_catalog import (
    CatalogModelSpec,
    CatalogModelView,
    EngineRoleName,
    ProviderCatalogConfig,
    ProviderRuntimeSettings,
    ProvidersStatusResponse,
    ProviderStatusEntry,
    ProviderTestConnectionResponse,
)
from app.ports.model_provider import ModelCapabilities

logger = logging.getLogger(__name__)

_DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[3] / "config" / "model_catalog.yaml"
_REQUIRED_ENGINE_ROLES: frozenset[EngineRoleName] = frozenset(
    {"router", "answer", "vision", "extract", "judge"}
)
_OLLAMA_PROBE_TIMEOUT = httpx.Timeout(connect=0.8, read=1.5, write=1.5, pool=1.5)


class CatalogApiSettingsProtocol(Protocol):
    """Typed structural contract for reading optional provider settings without secrets leakage."""

    ollama_base_url: str | None
    gemini_base_url: str | None
    gemini_api_key: SecretStr | None


def _default_catalog_path() -> Path:
    return _DEFAULT_CATALOG_PATH


def _parse_reasoning_off_params(raw_val: object, *, allow_none: bool) -> dict[str, Any] | None:
    """Validate optional or required reasoning_off_params mapping without blank keys."""
    if raw_val is None and allow_none:
        return None
    if not isinstance(raw_val, dict) or not all(
        isinstance(k, str) and bool(k.strip()) for k in raw_val
    ):
        raise ConfigurationError(
            "Provider entry must declare reasoning_off_params as a mapping of parameter names.",
            error_code="MODEL_CATALOG_INVALID",
        )
    return dict(raw_val)


def load_model_catalog(config_path: Path | None = None) -> list[ProviderCatalogConfig]:
    """Load and strictly validate model_catalog.yaml with fail-closed error codes."""
    resolved_path = config_path if config_path is not None else _default_catalog_path()
    if not resolved_path.is_file():
        raise ConfigurationError(
            "Model catalog configuration file was not found.",
            error_code="MODEL_CATALOG_NOT_FOUND",
        )

    try:
        raw_data = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigurationError(
            "Failed to parse model catalog YAML configuration.",
            error_code="MODEL_CATALOG_INVALID",
        ) from exc

    if not isinstance(raw_data, dict) or not isinstance(raw_data.get("providers"), dict):
        raise ConfigurationError(
            "Model catalog YAML must define a top-level 'providers' mapping.",
            error_code="MODEL_CATALOG_INVALID",
        )

    raw_providers: dict[object, object] = raw_data["providers"]
    entries: list[ProviderCatalogConfig] = []

    for provider_key, provider_val in raw_providers.items():
        if not isinstance(provider_key, str) or not isinstance(provider_val, dict):
            raise ConfigurationError(
                "Invalid provider entry in model catalog configuration.",
                error_code="MODEL_CATALOG_INVALID",
            )

        raw_models = provider_val.get("models")
        if not isinstance(raw_models, list):
            raise ConfigurationError(
                "Provider entry in model catalog must declare a 'models' list.",
                error_code="MODEL_CATALOG_INVALID",
            )

        coming_later = bool(provider_val.get("coming_later", False))
        raw_runtime = provider_val.get("runtime_settings")
        runtime_settings: ProviderRuntimeSettings | None = None
        if not coming_later:
            if not isinstance(raw_runtime, dict):
                raise ConfigurationError(
                    "Active provider in model catalog must declare explicit runtime_settings.",
                    error_code="MODEL_CATALOG_INVALID",
                )
            try:
                runtime_settings = ProviderRuntimeSettings.model_validate(raw_runtime)
            except ValidationError as exc:
                raise ConfigurationError(
                    "Invalid runtime_settings in model catalog configuration.",
                    error_code="MODEL_CATALOG_INVALID",
                ) from exc

        parsed_models: list[CatalogModelSpec] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, dict):
                raise ConfigurationError(
                    "Invalid model entry in model catalog configuration.",
                    error_code="MODEL_CATALOG_INVALID",
                )
            model_payload = dict(raw_model)
            if "provider" not in model_payload:
                raise ConfigurationError(
                    "Model entry in catalog must explicitly declare 'provider'.",
                    error_code="MODEL_CATALOG_INVALID",
                )
            if model_payload["provider"] != provider_key:
                raise ConfigurationError(
                    "Model entry provider mismatch in model catalog configuration.",
                    error_code="MODEL_CATALOG_INVALID",
                )
            for flag in ("supports_vision", "remote", "baa_covered", "reasoning"):
                if not isinstance(model_payload.get(flag), bool):
                    raise ConfigurationError(
                        "Model entry missing required boolean capability flag in catalog.",
                        error_code="MODEL_CATALOG_INVALID",
                    )
            if "reasoning_off_params" in model_payload:
                model_payload["reasoning_off_params"] = _parse_reasoning_off_params(
                    model_payload.get("reasoning_off_params"),
                    allow_none=True,
                )
            try:
                spec = CatalogModelSpec.model_validate(model_payload)
            except ValidationError as exc:
                raise ConfigurationError(
                    "Model entry failed catalog schema validation.",
                    error_code="MODEL_CATALOG_INVALID",
                ) from exc

            if set(spec.generation_limits.keys()) != _REQUIRED_ENGINE_ROLES:
                raise ConfigurationError(
                    "Model entry generation_limits must declare all five engine roles.",
                    error_code="MODEL_CATALOG_INVALID",
                )
            parsed_models.append(spec)

        raw_structured_mode = provider_val.get("structured_output_mode")
        if raw_structured_mode not in ("json_schema", "json_object"):
            raise ConfigurationError(
                "Provider entry must declare structured_output_mode "
                "('json_schema' or 'json_object').",
                error_code="MODEL_CATALOG_INVALID",
            )
        parsed_reasoning_off = _parse_reasoning_off_params(
            provider_val.get("reasoning_off_params"),
            allow_none=False,
        )
        raw_num_ctx = provider_val.get("num_ctx")
        if raw_num_ctx is not None and (
            not isinstance(raw_num_ctx, int) or isinstance(raw_num_ctx, bool) or raw_num_ctx <= 0
        ):
            raise ConfigurationError(
                "Provider entry num_ctx must be a positive integer.",
                error_code="MODEL_CATALOG_INVALID",
            )

        try:
            provider_entry = ProviderCatalogConfig.model_validate(
                {
                    "provider": provider_key,
                    "display_name": provider_val.get("display_name"),
                    "transport": provider_val.get("transport"),
                    "coming_later": coming_later,
                    "structured_output_mode": raw_structured_mode,
                    "reasoning_off_params": parsed_reasoning_off,
                    "num_ctx": raw_num_ctx,
                    "required_env": provider_val.get("required_env", []),
                    "runtime_settings": runtime_settings,
                    "notes": provider_val.get("notes", ""),
                    "models": parsed_models,
                }
            )
        except ValidationError as exc:
            raise ConfigurationError(
                "Provider entry failed catalog schema validation.",
                error_code="MODEL_CATALOG_INVALID",
            ) from exc

        entries.append(provider_entry)

    return entries


def build_catalog_capabilities_map(
    catalog: Sequence[ProviderCatalogConfig],
) -> Mapping[tuple[str, str], ModelCapabilities]:
    """Build an immutable capabilities map keyed strictly by (provider, model)."""
    caps: dict[tuple[str, str], ModelCapabilities] = {}
    for provider_entry in catalog:
        for spec in provider_entry.models:
            caps[(spec.provider, spec.model)] = ModelCapabilities(
                supports_vision=spec.supports_vision,
                remote=spec.remote,
                baa_covered=spec.baa_covered,
            )
    return MappingProxyType(caps)


def _extract_env_values(settings: CatalogApiSettingsProtocol) -> dict[str, str | None]:
    """Extract present provider credentials/URLs without exposing defaults or logging values."""
    return {
        "OLLAMA_BASE_URL": settings.ollama_base_url,
        "GEMINI_BASE_URL": settings.gemini_base_url,
        "GEMINI_API_KEY": (
            settings.gemini_api_key.get_secret_value()
            if settings.gemini_api_key is not None
            else None
        ),
    }


def _missing_settings_for_provider(
    entry: ProviderCatalogConfig, env_values: Mapping[str, str | None]
) -> list[str]:
    missing: list[str] = []
    for var_name in entry.required_env:
        val = env_values.get(var_name)
        if val is None or not val.strip():
            missing.append(var_name)
    return missing


def _create_probe_http_client(timeout: float | httpx.Timeout) -> httpx.AsyncClient:
    """Create short-lived AsyncClient for provider probes (isolated helper for test transport)."""
    return httpx.AsyncClient(timeout=timeout)


async def _fetch_ollama_downloaded_tags(
    base_url: str,
    http_client: httpx.AsyncClient | None = None,
) -> set[str] | None:
    """Query Ollama GET /api/tags with short timeout; return None when unreachable."""
    cleaned_base = base_url.strip().rstrip("/")
    tags_url = f"{cleaned_base}/api/tags"
    owns_client = http_client is None
    client = (
        http_client if http_client is not None else _create_probe_http_client(_OLLAMA_PROBE_TIMEOUT)
    )
    try:
        response = await client.get(tags_url)
        if response.status_code != 200:
            return None
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return None
        tags: set[str] = set()
        for item in payload["models"]:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                tag_name = item["name"].strip()
                if tag_name:
                    tags.add(tag_name)
        return tags
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if owns_client:
            await client.aclose()


def _to_model_view(spec: CatalogModelSpec) -> CatalogModelView:
    return CatalogModelView(
        provider=spec.provider,
        model=spec.model,
        display_name=spec.display_name,
        supports_vision=spec.supports_vision,
        remote=spec.remote,
        baa_covered=spec.baa_covered,
        governance_badge=spec.governance_badge,
        allowed_roles=list(spec.allowed_roles),
        notes=spec.notes,
    )


async def inspect_providers_status(
    settings: CatalogApiSettingsProtocol,
    config_path: Path | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ProvidersStatusResponse:
    """Inspect connection status and selectable curated models for all catalog providers."""
    catalog = load_model_catalog(config_path)
    env_values = _extract_env_values(settings)
    provider_entries: list[ProviderStatusEntry] = []

    for entry in catalog:
        if entry.coming_later:
            provider_entries.append(
                ProviderStatusEntry(
                    provider=entry.provider,
                    display_name=entry.display_name,
                    status="coming_later",
                    missing_settings=[],
                    notes=entry.notes,
                    models=[],
                )
            )
            continue

        missing = _missing_settings_for_provider(entry, env_values)
        if missing:
            provider_entries.append(
                ProviderStatusEntry(
                    provider=entry.provider,
                    display_name=entry.display_name,
                    status="not_connected",
                    missing_settings=missing,
                    notes=entry.notes,
                    models=[],
                )
            )
            continue

        if entry.transport == "ollama":
            ollama_url = env_values.get("OLLAMA_BASE_URL")
            if not ollama_url:
                provider_entries.append(
                    ProviderStatusEntry(
                        provider=entry.provider,
                        display_name=entry.display_name,
                        status="not_connected",
                        missing_settings=["OLLAMA_BASE_URL"],
                        notes=entry.notes,
                        models=[],
                    )
                )
                continue

            downloaded_tags = await _fetch_ollama_downloaded_tags(
                ollama_url, http_client=http_client
            )
            if downloaded_tags is None:
                provider_entries.append(
                    ProviderStatusEntry(
                        provider=entry.provider,
                        display_name=entry.display_name,
                        status="not_connected",
                        missing_settings=[],
                        notes=entry.notes,
                        models=[],
                    )
                )
                continue

            available_models = [
                _to_model_view(m) for m in entry.models if m.model in downloaded_tags
            ]
            provider_entries.append(
                ProviderStatusEntry(
                    provider=entry.provider,
                    display_name=entry.display_name,
                    status="connected",
                    missing_settings=[],
                    notes=entry.notes,
                    models=available_models,
                )
            )
            continue

        # OpenAI-compatible provider with all required env settings present
        provider_entries.append(
            ProviderStatusEntry(
                provider=entry.provider,
                display_name=entry.display_name,
                status="connected",
                missing_settings=[],
                notes=entry.notes,
                models=[_to_model_view(m) for m in entry.models],
            )
        )

    return ProvidersStatusResponse(providers=provider_entries)


async def probe_provider_connection(
    provider_id: str,
    settings: CatalogApiSettingsProtocol,
    config_path: Path | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ProviderTestConnectionResponse:
    """Execute one tiny probe request for an admin connection check; never returns secrets."""
    catalog = load_model_catalog(config_path)
    by_id = {entry.provider: entry for entry in catalog}
    if provider_id not in by_id:
        raise ConfigurationError(
            "Unknown model provider.",
            error_code="PROVIDER_NOT_FOUND",
        )

    entry = by_id[provider_id]
    if entry.coming_later:
        return ProviderTestConnectionResponse(
            provider=provider_id,
            ok=False,
            error_code="PROVIDER_COMING_LATER",
            message="Provider is coming later and does not have an active adapter yet.",
        )

    env_values = _extract_env_values(settings)
    missing = _missing_settings_for_provider(entry, env_values)
    if missing:
        return ProviderTestConnectionResponse(
            provider=provider_id,
            ok=False,
            error_code="PROVIDER_NOT_CONNECTED",
            message="Required provider configuration settings are missing.",
        )

    if entry.transport == "ollama":
        ollama_url = env_values["OLLAMA_BASE_URL"] or ""
        tags = await _fetch_ollama_downloaded_tags(ollama_url, http_client=http_client)
        if tags is None:
            return ProviderTestConnectionResponse(
                provider=provider_id,
                ok=False,
                error_code="PROVIDER_NOT_CONNECTED",
                message="Ollama daemon did not respond.",
            )
        return ProviderTestConnectionResponse(
            provider=provider_id,
            ok=True,
            error_code=None,
            message="Connected to local Ollama daemon.",
        )

    base_var = next((v for v in entry.required_env if v.endswith("_BASE_URL")), None)
    key_var = next((v for v in entry.required_env if v.endswith("_API_KEY")), None)
    base_url = (env_values.get(base_var) if base_var else None) or ""
    api_key = (env_values.get(key_var) if key_var else None) or ""
    cleaned_base = base_url.strip().rstrip("/")
    timeout_secs = entry.runtime_settings.timeout_seconds if entry.runtime_settings else 15.0

    owns_client = http_client is None
    client = (
        http_client
        if http_client is not None
        else _create_probe_http_client(min(timeout_secs, 15.0))
    )

    try:
        headers = {"Authorization": f"Bearer {api_key.strip()}"}
        if entry.models:
            completions_url = (
                cleaned_base
                if cleaned_base.endswith("/chat/completions")
                else f"{cleaned_base}/chat/completions"
            )
            payload = {
                "model": entry.models[0].model,
                "messages": [{"role": "user", "content": "Reply OK"}],
                "max_tokens": 8,
                "temperature": 0.0,
            }
            response = await client.post(completions_url, headers=headers, json=payload)
        else:
            models_url = (
                cleaned_base[: -len("/chat/completions")] + "/models"
                if cleaned_base.endswith("/chat/completions")
                else f"{cleaned_base}/models"
            )
            response = await client.get(models_url, headers=headers)
    except httpx.TimeoutException:
        return ProviderTestConnectionResponse(
            provider=provider_id,
            ok=False,
            error_code="PROVIDER_TIMEOUT",
            message="Provider connection test timed out.",
        )
    except httpx.HTTPError:
        return ProviderTestConnectionResponse(
            provider=provider_id,
            ok=False,
            error_code="PROVIDER_UNAVAILABLE",
            message="Provider endpoint could not be reached.",
        )
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code in (200, 201):
        return ProviderTestConnectionResponse(
            provider=provider_id,
            ok=True,
            error_code=None,
            message="Provider connection verified.",
        )
    if response.status_code in (401, 403):
        return ProviderTestConnectionResponse(
            provider=provider_id,
            ok=False,
            error_code="PROVIDER_AUTH_FAILED",
            message="Provider rejected the configured credentials.",
        )
    if response.status_code == 429:
        return ProviderTestConnectionResponse(
            provider=provider_id,
            ok=False,
            error_code="PROVIDER_RATE_LIMITED",
            message="Provider endpoint rate limit exceeded.",
        )
    return ProviderTestConnectionResponse(
        provider=provider_id,
        ok=False,
        error_code="PROVIDER_UNAVAILABLE",
        message="Provider endpoint returned an unexpected error status.",
    )
