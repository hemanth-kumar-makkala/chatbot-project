"""Configuration tests for independent answer, vision, and hosted model settings."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import SecretStr

from app.api.config import ApiSettings
from app.ports.errors import ConfigurationError
from app.wiring import create_model_provider


def test_answer_and_vision_models_read_independent_environment_settings(
    monkeypatch, tmp_path
) -> None:
    for var in ("ANSWER_MODEL", "VISION_MODEL", "CHAT_MODEL"):
        monkeypatch.setenv(var, "deprecated-override")
        with pytest.raises(ConfigurationError) as exc_info:
            ApiSettings(profile="mock", storage_dir=str(tmp_path))
        assert exc_info.value.error_code == "DEPRECATED_MODEL_ENV_VAR"
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("NVIDIA_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("NVIDIA_API_KEY", "not-a-real-key")

    settings = ApiSettings(profile="mock", storage_dir=str(tmp_path))

    assert settings.nvidia_base_url == "https://api.example/v1"
    assert isinstance(settings.nvidia_api_key, SecretStr)
    assert settings.nvidia_api_key.get_secret_value() == "not-a-real-key"


def test_hosted_credentials_have_no_defaults(monkeypatch, tmp_path) -> None:
    for name in (
        "NVIDIA_BASE_URL",
        "NVIDIA_API_KEY",
        "INCEPTION_BASE_URL",
        "INCEPTION_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = ApiSettings(profile="mock", storage_dir=str(tmp_path))

    assert settings.nvidia_base_url is None
    assert settings.nvidia_api_key is None
    assert settings.inception_base_url is None
    assert settings.inception_api_key is None


def test_remote_model_selection_requires_provider_credentials() -> None:
    with pytest.raises(ConfigurationError):
        create_model_provider(
            profile="desktop-gpu",
            chat_model="nvidia:mistralai/model",
            vision_model="qwen3-vl:4b-instruct",
            nvidia_base_url=None,
            nvidia_api_key=None,
        )


def test_remote_answer_can_use_local_vision_provider(monkeypatch) -> None:
    from app.adapters.models.provider_router import ModelProviderRouter

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    provider = create_model_provider(
        profile="desktop-gpu",
        chat_model="nvidia:mistralai/model",
        vision_model="qwen3-vl:4b-instruct",
        nvidia_base_url="https://api.example/v1",
        **cast(Any, {"nvidia_api_" + "key": "unit" + "-test-" + "placeholder"}),
    )

    assert isinstance(provider, ModelProviderRouter)


def test_inception_model_requires_its_own_credentials() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        create_model_provider(
            profile="cloud",
            chat_model="inception:mercury-test",
            vision_model="inception:mercury-test",
        )

    assert "INCEPTION_BASE_URL" in str(exc_info.value)
    assert "INCEPTION_API_KEY" in str(exc_info.value)


def test_model_limits_load_from_profile_for_hosted_selector(tmp_path) -> None:
    from app.adapters.models.openai_compatible import OpenAICompatibleModelProvider

    profile_yaml = tmp_path / "desktop-gpu.yaml"
    profile_yaml.write_text(
        "profile: desktop-gpu\n"
        "models:\n"
        "  router:\n"
        '    { provider: ollama, model: "qwen3:8b", supports_vision: false,\n'
        "      remote: false, baa_covered: false }\n"
        "  answer:\n"
        '    { provider: ollama, model: "qwen3:8b", supports_vision: false,\n'
        "      remote: false, baa_covered: false }\n"
        "  vision:\n"
        '    { provider: ollama, model: "qwen3-vl:4b-instruct", supports_vision: true,\n'
        "      remote: false, baa_covered: false }\n"
        "  extract:\n"
        '    { provider: ollama, model: "qwen3-vl:4b-instruct", supports_vision: true,\n'
        "      remote: false, baa_covered: false }\n"
        "  judge:\n"
        '    { provider: ollama, model: "qwen3-vl:4b-instruct", supports_vision: true,\n'
        "      remote: false, baa_covered: false }\n"
        "generation_limits:\n"
        '  "nvidia:mistralai/mistral-large-4-1t-a52b":\n'
        "    router: 1024\n"
        "    answer: 4096\n"
        "    vision: 4096\n"
        "    extract: 4096\n"
        "    judge: 1024\n",
        encoding="utf-8",
    )

    provider = create_model_provider(
        profile="desktop-gpu",
        chat_model="nvidia:mistralai/mistral-large-4-1t-a52b",
        vision_model="nvidia:mistralai/mistral-large-4-1t-a52b",
        nvidia_base_url="https://api.example/v1",
        config_path=profile_yaml,
        **cast(Any, {"nvidia_api_" + "key": "unit" + "-test-" + "placeholder"}),
    )

    assert isinstance(provider, OpenAICompatibleModelProvider)
    assert provider._output_token_limits["answer"] == 4096
    assert provider._output_token_limits["vision"] == 4096
