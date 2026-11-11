"""Port contracts and DTOs for the model catalog and provider connection status.

Clean architecture invariant:
- Pure interfaces and Pydantic data schemas only (no vendor SDKs, no env reads, no YAML I/O).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProviderConnectionStatus = Literal["connected", "not_connected", "coming_later"]
ModelGovernanceBadge = Literal["local", "cloud_test_data_only", "cloud_with_baa"]
SelectableRole = Literal["answer", "judge"]
EngineRoleName = Literal["router", "answer", "vision", "extract", "judge"]


class CatalogModelSpec(BaseModel):
    """Curated model entry declared in config/model_catalog.yaml."""

    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    supports_vision: bool
    remote: bool
    baa_covered: bool
    allowed_roles: list[SelectableRole] = Field(..., min_length=1)
    temperature: float
    reasoning: bool
    generation_limits: dict[EngineRoleName, int]
    notes: str = ""

    @property
    def governance_badge(self) -> ModelGovernanceBadge:
        """Derive standardized governance badge from explicit capability flags."""
        if not self.remote:
            return "local"
        if self.baa_covered:
            return "cloud_with_baa"
        return "cloud_test_data_only"


class ProviderRuntimeSettings(BaseModel):
    """Explicit client pacing, timeout, and retry settings for a provider."""

    timeout_seconds: float = Field(..., gt=0)
    max_retries: int = Field(..., ge=0)
    max_total_wait_seconds: float = Field(..., ge=0)
    requests_per_minute: int = Field(..., gt=0)
    max_image_dimension: int = Field(..., gt=0)


class ProviderCatalogConfig(BaseModel):
    """Provider block parsed from model_catalog.yaml."""

    provider: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    transport: Literal["ollama", "openai_compatible", "native_coming_later"]
    coming_later: bool = False
    required_env: list[str] = Field(default_factory=list)
    runtime_settings: ProviderRuntimeSettings | None = None
    notes: str = ""
    models: list[CatalogModelSpec] = Field(default_factory=list)


class CatalogModelView(BaseModel):
    """API-safe representation of a selectable model with its governance badge."""

    provider: str
    model: str
    display_name: str
    supports_vision: bool
    remote: bool
    baa_covered: bool
    governance_badge: ModelGovernanceBadge
    allowed_roles: list[SelectableRole]
    notes: str


class ProviderStatusEntry(BaseModel):
    """API-safe connection status and available models for one provider (zero secret values)."""

    provider: str
    display_name: str
    status: ProviderConnectionStatus
    missing_settings: list[str]
    notes: str
    models: list[CatalogModelView]


class ProvidersStatusResponse(BaseModel):
    """Response payload for GET /v1/models/providers."""

    providers: list[ProviderStatusEntry]


class ProviderTestConnectionResponse(BaseModel):
    """Response payload for POST /v1/models/providers/{provider}/test-connection."""

    provider: str
    ok: bool
    error_code: str | None = None
    message: str


__all__ = [
    "CatalogModelSpec",
    "CatalogModelView",
    "EngineRoleName",
    "ModelGovernanceBadge",
    "ProviderCatalogConfig",
    "ProviderConnectionStatus",
    "ProviderRuntimeSettings",
    "ProviderStatusEntry",
    "ProviderTestConnectionResponse",
    "ProvidersStatusResponse",
    "SelectableRole",
]
