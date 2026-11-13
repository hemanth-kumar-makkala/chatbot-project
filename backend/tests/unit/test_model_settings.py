"""Configuration tests for independent answer, vision, and hosted model settings."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.api.config import ApiSettings
from app.ports.errors import ConfigurationError
from app.wiring import create_model_provider


def test_deprecated_model_env_vars_fail_startup(monkeypatch, tmp_path) -> None:
    for var in ("ANSWER_MODEL", "VISION_MODEL", "CHAT_MODEL"):
        monkeypatch.setenv(var, "deprecated-override")
        with pytest.raises(ConfigurationError) as exc_info:
            ApiSettings(profile="mock", storage_dir=str(tmp_path))
        assert exc_info.value.error_code == "DEPRECATED_MODEL_ENV_VAR"
        monkeypatch.delenv(var, raising=False)


def test_leftover_provider_env_vars_are_ignored_silently(monkeypatch, tmp_path) -> None:
    """Leftover NVIDIA_*, INCEPTION_*, or OPENAI_* variables must be silently ignored."""
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("NVIDIA_API_KEY", "not-a-real-key")
    monkeypatch.setenv("INCEPTION_BASE_URL", "https://api.inception/v1")
    monkeypatch.setenv("INCEPTION_API_KEY", "not-a-real-key-2")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key-3")

    settings = ApiSettings(profile="mock", storage_dir=str(tmp_path))
    assert hasattr(settings, "nvidia_base_url") is False
    assert hasattr(settings, "inception_base_url") is False
    assert hasattr(settings, "openai_base_url") is False


def test_gemini_credentials_loaded_from_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-gemini-key")

    settings = ApiSettings(profile="mock", storage_dir=str(tmp_path))
    assert settings.gemini_base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert isinstance(settings.gemini_api_key, SecretStr)
    assert settings.gemini_api_key.get_secret_value() == "unit-test-gemini-key"


def test_remote_model_selection_requires_provider_credentials() -> None:
    with pytest.raises(ConfigurationError):
        create_model_provider(
            profile="desktop-gpu",
            chat_model="gemini:gemini-3.5-flash",
            vision_model="qwen3-vl:4b-instruct",
            gemini_base_url=None,
            gemini_api_key=None,
        )


def test_remote_answer_can_use_local_vision_provider(monkeypatch) -> None:
    from app.adapters.models.provider_router import ModelProviderRouter

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    provider = create_model_provider(
        profile="desktop-gpu",
        chat_model="gemini:gemini-3.5-flash",
        vision_model="qwen3-vl:4b-instruct",
        gemini_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        gemini_api_key="unit-test-gemini-key",  # pragma: allowlist secret
    )

    assert isinstance(provider, ModelProviderRouter)


def test_model_limits_load_from_profile_for_hosted_selector(monkeypatch, tmp_path) -> None:
    from app.adapters.models.openai_compatible import OpenAICompatibleModelProvider

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    profile_yaml = tmp_path / "desktop-gpu.yaml"
    profile_yaml.write_text(
        "profile: desktop-gpu\n"
        "catalog_providers: [gemini]\n"
        "models:\n"
        "  router:\n"
        '    { provider: ollama, model: "qwen3:8b", supports_vision: false,\n'
        "      remote: false, baa_covered: false }\n"
        "  answer:\n"
        '    { provider: gemini, model: "gemini-3.5-flash", supports_vision: true,\n'
        "      remote: true, baa_covered: false, temperature: 0.0, reasoning: false }\n"
        "  vision:\n"
        '    { provider: ollama, model: "qwen3-vl:4b-instruct", supports_vision: true,\n'
        "      remote: false, baa_covered: false }\n"
        "  extract:\n"
        '    { provider: ollama, model: "qwen3-vl:4b-instruct", supports_vision: true,\n'
        "      remote: false, baa_covered: false }\n"
        "  judge:\n"
        '    { provider: gemini, model: "gemini-3.5-flash-lite", supports_vision: false,\n'
        "      remote: true, baa_covered: false, temperature: 0.0, reasoning: false }\n"
        "generation_limits:\n"
        '  "gemini:gemini-3.5-flash":\n'
        "    router: 1024\n"
        "    answer: 4096\n"
        "    vision: 4096\n"
        "    extract: 4096\n"
        "    judge: 1024\n",
        encoding="utf-8",
    )

    provider = create_model_provider(
        profile="desktop-gpu",
        chat_model="gemini:gemini-3.5-flash",
        vision_model="qwen3-vl:4b-instruct",
        gemini_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        gemini_api_key="unit-test-gemini-key",  # pragma: allowlist secret
        config_path=profile_yaml,
    )

    from app.adapters.models.provider_router import ModelProviderRouter
    from app.ports.types import ModelRole

    assert isinstance(provider, ModelProviderRouter)
    answer_prov = provider.get_provider(ModelRole.ANSWER)
    assert isinstance(answer_prov, OpenAICompatibleModelProvider)
    assert answer_prov._output_token_limits["answer"] == 4096
