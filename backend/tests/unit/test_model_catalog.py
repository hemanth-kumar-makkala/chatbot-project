"""Unit tests for Stage 1 model catalog, provider status inspection, and conversations cleanup."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
import yaml
from pydantic import SecretStr
from starlette.requests import Request

from app.adapters.models.catalog import (
    build_catalog_capabilities_map,
    inspect_providers_status,
    load_model_catalog,
)
from app.api.config import ApiSettings
from app.api.routes import conversations as conversations_route
from app.core.graph import GraphPorts
from app.ports.errors import ConfigurationError
from app.ports.model_catalog import CatalogModelSpec, EngineRoleName
from app.ports.model_provider import ModelCapabilities
from app.ports.types import SessionContext, TenantContext, UserRole


def test_load_model_catalog_validates_confirmed_providers_and_explicit_settings() -> None:
    """Catalog declares confirmed providers/models and explicit generation/pacing settings."""
    catalog = load_model_catalog()
    by_id = {entry.provider: entry for entry in catalog}

    assert set(by_id.keys()) == {
        "ollama",
        "gemini",
        "nvidia",
        "inception",
        "openai",
        "anthropic",
        "vertex",
    }

    # Gemini confirmed models and requests_per_minute == 10
    gemini = by_id["gemini"]
    assert gemini.coming_later is False
    assert gemini.required_env == ["GEMINI_API_KEY", "GEMINI_BASE_URL"]
    assert gemini.structured_output_mode == "json_schema"
    assert gemini.reasoning_off_params == {"reasoning_effort": "none"}
    assert gemini.runtime_settings is not None
    assert gemini.runtime_settings.requests_per_minute == 10
    gemini_models = {m.model: m for m in gemini.models}
    assert set(gemini_models.keys()) == {"gemini-3.5-flash", "gemini-3.5-flash-lite"}
    assert gemini_models["gemini-3.5-flash"].supports_vision is True
    assert gemini_models["gemini-3.5-flash"].remote is True
    assert gemini_models["gemini-3.5-flash"].baa_covered is False
    assert gemini_models["gemini-3.5-flash"].allowed_roles == ["answer"]
    assert gemini_models["gemini-3.5-flash-lite"].supports_vision is False
    assert gemini_models["gemini-3.5-flash-lite"].remote is True
    assert gemini_models["gemini-3.5-flash-lite"].baa_covered is False
    assert gemini_models["gemini-3.5-flash-lite"].allowed_roles == ["judge"]

    # NVIDIA confirmed: mistralai/mistral-large-4-1t-a52b only (no nemotron)
    nvidia = by_id["nvidia"]
    assert nvidia.required_env == ["NVIDIA_API_KEY", "NVIDIA_BASE_URL"]
    assert nvidia.structured_output_mode == "json_object"
    assert nvidia.reasoning_off_params == {}
    assert nvidia.runtime_settings is not None
    assert [m.model for m in nvidia.models] == ["mistralai/mistral-large-4-1t-a52b"]
    nv_model = nvidia.models[0]
    assert nv_model.supports_vision is False
    assert nv_model.remote is True
    assert nv_model.baa_covered is False

    # Placeholders with zero models
    for placeholder in ("inception", "openai"):
        assert by_id[placeholder].models == []
        assert by_id[placeholder].coming_later is False
        assert by_id[placeholder].runtime_settings is not None

    # Coming later providers
    for future_provider in ("anthropic", "vertex"):
        assert by_id[future_provider].coming_later is True
        assert by_id[future_provider].models == []

    # Ollama matches all models from desktop-gpu, home-gpu, office-cpu YAMLs
    ollama = by_id["ollama"]
    assert ollama.required_env == ["OLLAMA_BASE_URL"]
    assert ollama.structured_output_mode == "json_object"
    assert ollama.reasoning_off_params == {}
    assert ollama.runtime_settings is not None
    ollama_models = {m.model for m in ollama.models}
    assert ollama_models == {
        "qwen3-vl:4b",
        "qwen2.5:7b",
        "qwen2-vl:7b",
        "llama3.1:8b",
        "qwen2.5:3b",
        "qwen2-vl:2b",
        "llama3.2:3b",
    }

    # Every model has explicit temperature, reasoning, generation_limits
    for entry in catalog:
        for m in entry.models:
            assert isinstance(m.temperature, float)
            assert isinstance(m.reasoning, bool)
            assert set(m.generation_limits.keys()) == {
                "router",
                "answer",
                "vision",
                "extract",
                "judge",
            }


def test_load_model_catalog_fails_closed_on_missing_or_invalid_file(tmp_path: Path) -> None:
    """Missing catalog path or invalid YAML raises ConfigurationError with fixed code."""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(ConfigurationError) as exc_missing:
        load_model_catalog(missing)
    assert exc_missing.value.error_code == "MODEL_CATALOG_NOT_FOUND"

    bad_yaml = tmp_path / "bad_catalog.yaml"
    bad_yaml.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "gemini": {
                        "display_name": "Gemini",
                        "transport": "openai_compatible",
                        "models": [{"model": "gemini-3.5-flash"}],  # missing required flags
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc_bad:
        load_model_catalog(bad_yaml)
    assert exc_bad.value.error_code == "MODEL_CATALOG_INVALID"


def test_load_model_catalog_requires_structured_output_and_reasoning_off_params(
    tmp_path: Path,
) -> None:
    """Providers missing structured_output_mode or reasoning_off_params fail catalog validation."""
    base_runtime = {
        "timeout_seconds": 60.0,
        "max_retries": 3,
        "max_total_wait_seconds": 20.0,
        "requests_per_minute": 10,
        "max_image_dimension": 1536,
    }

    missing_mode = tmp_path / "missing_mode.yaml"
    missing_mode.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "gemini": {
                        "display_name": "Gemini",
                        "transport": "openai_compatible",
                        "runtime_settings": base_runtime,
                        "reasoning_off_params": {"reasoning_effort": "none"},
                        "models": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc_mode:
        load_model_catalog(missing_mode)
    assert exc_mode.value.error_code == "MODEL_CATALOG_INVALID"

    missing_reasoning = tmp_path / "missing_reasoning.yaml"
    missing_reasoning.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "gemini": {
                        "display_name": "Gemini",
                        "transport": "openai_compatible",
                        "runtime_settings": base_runtime,
                        "structured_output_mode": "json_schema",
                        "models": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc_reason:
        load_model_catalog(missing_reasoning)
    assert exc_reason.value.error_code == "MODEL_CATALOG_INVALID"

    invalid_mode = tmp_path / "invalid_mode.yaml"
    invalid_mode.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "gemini": {
                        "display_name": "Gemini",
                        "transport": "openai_compatible",
                        "runtime_settings": base_runtime,
                        "structured_output_mode": "xml",
                        "reasoning_off_params": {},
                        "models": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc_invalid:
        load_model_catalog(invalid_mode)
    assert exc_invalid.value.error_code == "MODEL_CATALOG_INVALID"


def test_governance_badge_derivation() -> None:
    """Badges map accurately from remote and baa_covered flags."""
    limits: dict[EngineRoleName, int] = {
        "router": 1024,
        "answer": 2048,
        "vision": 2048,
        "extract": 2048,
        "judge": 1024,
    }
    local_spec = CatalogModelSpec(
        provider="ollama",
        model="qwen3-vl:4b",
        display_name="Qwen",
        supports_vision=True,
        remote=False,
        baa_covered=False,
        allowed_roles=["answer"],
        temperature=0.0,
        reasoning=False,
        generation_limits=limits,
    )
    cloud_test_spec = CatalogModelSpec(
        provider="gemini",
        model="gemini-3.5-flash",
        display_name="Gemini",
        supports_vision=True,
        remote=True,
        baa_covered=False,
        allowed_roles=["answer"],
        temperature=0.0,
        reasoning=False,
        generation_limits=limits,
    )
    baa_spec = CatalogModelSpec(
        provider="vertex",
        model="gemini-baa",
        display_name="Vertex Gemini",
        supports_vision=True,
        remote=True,
        baa_covered=True,
        allowed_roles=["answer"],
        temperature=0.0,
        reasoning=False,
        generation_limits=limits,
    )

    assert local_spec.governance_badge == "local"
    assert cloud_test_spec.governance_badge == "cloud_test_data_only"
    assert baa_spec.governance_badge == "cloud_with_baa"


def test_build_catalog_capabilities_map_returns_immutable_mapping() -> None:
    """Catalog capabilities map is immutable and strictly keyed by (provider, model)."""
    catalog = load_model_catalog()
    caps = build_catalog_capabilities_map(catalog)
    assert isinstance(caps, MappingProxyType)
    assert caps[("gemini", "gemini-3.5-flash")] == ModelCapabilities(
        supports_vision=True, remote=True, baa_covered=False
    )
    assert caps[("gemini", "gemini-3.5-flash-lite")] == ModelCapabilities(
        supports_vision=False, remote=True, baa_covered=False
    )
    assert caps[("ollama", "qwen3-vl:4b")] == ModelCapabilities(
        supports_vision=True, remote=False, baa_covered=False
    )


@pytest.mark.asyncio
async def test_inspect_providers_status_reports_connected_vs_missing_without_keys(
    tmp_path: Path,
) -> None:
    """Missing env settings produce not_connected + missing names; keys never leak."""
    secret_gemini = "secret-gemini-api-key-do-not-leak"  # pragma: allowlist secret
    secret_nvidia = "secret-nvidia-api-key-do-not-leak"  # pragma: allowlist secret
    settings = ApiSettings(
        profile="mock",
        storage_dir=str(tmp_path),
        gemini_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        gemini_api_key=SecretStr(secret_gemini),
        nvidia_api_key=SecretStr(secret_nvidia),
        # nvidia_base_url deliberately omitted
    )

    resp = await inspect_providers_status(settings=settings)
    serialized = resp.model_dump_json()
    assert secret_gemini not in serialized
    assert secret_nvidia not in serialized

    by_id = {p.provider: p for p in resp.providers}
    assert by_id["gemini"].status == "connected"
    assert by_id["gemini"].missing_settings == []
    assert [m.model for m in by_id["gemini"].models] == [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]

    assert by_id["nvidia"].status == "not_connected"
    assert by_id["nvidia"].missing_settings == ["NVIDIA_BASE_URL"]
    assert by_id["nvidia"].models == []

    assert by_id["ollama"].status == "not_connected"
    assert by_id["ollama"].missing_settings == ["OLLAMA_BASE_URL"]

    assert by_id["anthropic"].status == "coming_later"
    assert by_id["vertex"].status == "coming_later"


@pytest.mark.asyncio
async def test_inspect_providers_ollama_filters_to_downloaded_tags(tmp_path: Path) -> None:
    """When OLLAMA_BASE_URL responds to /api/tags, only downloaded catalog models appear."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "qwen3-vl:4b"},
                    {"name": "llama3.1:8b"},
                    {"name": "unlisted-local-model:latest"},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    settings = ApiSettings(
        profile="mock",
        storage_dir=str(tmp_path),
        ollama_base_url="http://127.0.0.1:11434",
    )

    async with httpx.AsyncClient(transport=transport) as client:
        resp = await inspect_providers_status(settings=settings, http_client=client)

    ollama = next(p for p in resp.providers if p.provider == "ollama")
    assert ollama.status == "connected"
    assert ollama.missing_settings == []
    assert [m.model for m in ollama.models] == ["qwen3-vl:4b", "llama3.1:8b"]
    assert all(m.governance_badge == "local" for m in ollama.models)


@pytest.mark.asyncio
async def test_inspect_providers_ollama_down_returns_not_connected(tmp_path: Path) -> None:
    """Connection error or timeout against Ollama yields not_connected cleanly."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    settings = ApiSettings(
        profile="mock",
        storage_dir=str(tmp_path),
        ollama_base_url="http://127.0.0.1:11434",
    )

    async with httpx.AsyncClient(transport=transport) as client:
        resp = await inspect_providers_status(settings=settings, http_client=client)

    ollama = next(p for p in resp.providers if p.provider == "ollama")
    assert ollama.status == "not_connected"
    assert ollama.models == []


@pytest.mark.asyncio
async def test_conversations_extract_has_no_qwen_default_and_uses_profile_extract_model(
    tmp_path: Path,
) -> None:
    """Addition (b): conversations.py avoids qwen3-vl:4b default and uses profile extract model."""
    sig = inspect.signature(conversations_route.extract_fields_from_document_file)
    assert sig.parameters["chat_model"].default != "qwen3-vl:4b"
    assert sig.parameters["vision_model"].default != "qwen3-vl:4b"

    sample_pdf = tmp_path / "fax.pdf"
    sample_pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF")

    captured_ports: list[GraphPorts] = []

    class FakeSubgraph:
        async def ainvoke(self, state, context=None):
            _ = state
            assert context is not None
            captured_ports.append(context.ports)
            context.extracted_fields["fields"] = []

    tenant_ctx = TenantContext(
        tenant_id=str(uuid4()),
        user_id=str(uuid4()),
        role=UserRole.STAFF,
        session_id="sess-ext",
    )

    with (
        patch.object(conversations_route, "create_document_parser", return_value=MagicMock()),
        patch.object(
            conversations_route, "create_model_provider", return_value=MagicMock()
        ) as mock_cmp,
        patch.object(conversations_route, "create_image_processor", return_value=MagicMock()),
        patch.object(
            conversations_route,
            "build_extraction_subgraph",
            return_value=MagicMock(compile=lambda: FakeSubgraph()),
        ),
    ):
        await conversations_route.extract_fields_from_document_file(
            file_path=sample_pdf,
            file_name="fax.pdf",
            profile="desktop-api",
            tenant_ctx=tenant_ctx,
            gemini_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            gemini_api_key="gm-test-key",  # pragma: allowlist secret
        )

    assert len(captured_ports) == 1
    ports = captured_ports[0]
    assert ports.vision_model == "gemini:gemini-3.5-flash"
    assert ports.chat_model == "gemini:gemini-3.5-flash"
    assert "qwen3-vl:4b" not in str(mock_cmp.call_args)


@pytest.mark.asyncio
async def test_upload_session_document_does_not_fallback_to_qwen_on_desktop_api(
    tmp_path: Path,
) -> None:
    """Addition (b): upload_session_document avoids qwen3-vl:4b fallback on desktop-api."""
    settings = ApiSettings(
        profile="desktop-api",
        storage_dir=str(tmp_path),
        session_upload_retention_hours=24,
        gemini_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        gemini_api_key=SecretStr("gm-test-key"),
    )
    assert settings.chat_model == ""
    assert settings.vision_model == ""

    pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"content-type", b"application/pdf"),
            (b"x-filename", b"fax.pdf"),
        ],
    }

    async def receive():
        return {"type": "http.request", "body": pdf_content, "more_body": False}

    req = Request(scope, receive)
    session_ctx = SessionContext(
        session_id="sess-1",
        user_id=str(uuid4()),
        tenant_id="",
        roles=[UserRole.STAFF],
    )
    conv_id = str(uuid4())
    asst_id = str(uuid4())
    tenant_id = str(uuid4())

    mock_extract = AsyncMock(return_value=[])
    mock_auth = MagicMock()
    mock_auth.roles = AsyncMock(return_value=[UserRole.STAFF])

    with (
        patch.object(
            conversations_route,
            "get_tenant_context_for_assistant",
            new=AsyncMock(
                return_value=TenantContext(
                    tenant_id=tenant_id,
                    user_id=session_ctx.user_id,
                    role=UserRole.STAFF,
                    session_id=session_ctx.session_id,
                )
            ),
        ),
        patch.object(conversations_route, "tenant_session") as mock_ts,
        patch.object(conversations_route, "extract_fields_from_document_file", mock_extract),
    ):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock(assistant_id=uuid4())
        mock_db.execute.return_value = mock_result
        mock_ts.return_value.__aenter__.return_value = mock_db

        await conversations_route.upload_session_document(
            conversation_id=conv_id,
            request=req,
            session=session_ctx,
            auth_provider=mock_auth,
            settings=settings,
            assistant_id=asst_id,
        )

    mock_extract.assert_awaited_once()
    call_kwargs = mock_extract.call_args.kwargs
    assert call_kwargs["chat_model"] is None
    assert call_kwargs["vision_model"] is None
    assert call_kwargs["profile"] == "desktop-api"
