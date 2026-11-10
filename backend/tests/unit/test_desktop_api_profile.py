"""Unit tests for desktop-api profile, role capability flags, and safety lock integration."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.core.safety_lock import (
    CloudModelSafetyLockError,
    is_cloud_model,
    is_vision_model,
    verify_cloud_model_safety_lock,
)
from app.ports.errors import ConfigurationError
from app.ports.model_provider import ModelCapabilities
from app.wiring import (
    load_and_validate_profile_models,
)


def test_desktop_api_profile_resolves_all_roles_from_yaml() -> None:
    profile_data = load_and_validate_profile_models("desktop-api")
    assert "router" in profile_data
    assert "answer" in profile_data
    assert "vision" in profile_data
    assert "extract" in profile_data
    assert "judge" in profile_data

    # Check models and capability flags
    assert profile_data["answer"]["model"] == "nvidia/nemotron-3.5-super-vl-120b-a12b"
    assert profile_data["answer"]["remote"] is True
    assert profile_data["answer"]["baa_covered"] is False
    assert profile_data["judge"]["model"] == "mistralai/mistral-large-4-1t-a52b"
    assert profile_data["judge"]["remote"] is True
    assert profile_data["judge"]["baa_covered"] is False


def test_missing_role_fails_startup_with_typed_error(tmp_path: Path) -> None:
    bad_yaml = tmp_path / "bad_profile.yaml"
    bad_yaml.write_text(
        yaml.dump(
            {
                "profile": "bad_profile",
                "models": {
                    "router": {
                        "provider": "mock",
                        "model": "m-router",
                        "supports_vision": True,
                        "remote": False,
                        "baa_covered": False,
                    },
                    "answer": {
                        "provider": "mock",
                        "model": "m-answer",
                        "supports_vision": True,
                        "remote": False,
                        "baa_covered": False,
                    },
                    # Missing vision, extract, judge
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_and_validate_profile_models("bad_profile", config_path=bad_yaml)
    assert exc_info.value.error_code == "PROFILE_ROLE_MISSING"


def test_judge_cannot_equal_answer_in_desktop_api(tmp_path: Path) -> None:
    same_judge_yaml = tmp_path / "conflict_profile.yaml"
    same_judge_yaml.write_text(
        yaml.dump(
            {
                "profile": "conflict_profile",
                "models": {
                    "router": {
                        "provider": "nvidia",
                        "model": "shared-model",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "answer": {
                        "provider": "nvidia",
                        "model": "shared-model",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "vision": {
                        "provider": "nvidia",
                        "model": "shared-model",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "extract": {
                        "provider": "nvidia",
                        "model": "shared-model",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "judge": {
                        "provider": "nvidia",
                        "model": "shared-model",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_and_validate_profile_models("conflict_profile", config_path=same_judge_yaml)
    assert exc_info.value.error_code == "JUDGE_MODEL_SAME_AS_ANSWER"


def test_vision_or_extract_fails_if_supports_vision_false(tmp_path: Path) -> None:
    no_vision_yaml = tmp_path / "no_vision_profile.yaml"
    no_vision_yaml.write_text(
        yaml.dump(
            {
                "profile": "no_vision_profile",
                "models": {
                    "router": {
                        "provider": "nvidia",
                        "model": "text-model",
                        "supports_vision": False,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "answer": {
                        "provider": "nvidia",
                        "model": "text-model",
                        "supports_vision": False,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "vision": {
                        "provider": "nvidia",
                        "model": "text-model",
                        "supports_vision": False,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "extract": {
                        "provider": "nvidia",
                        "model": "text-model",
                        "supports_vision": False,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "judge": {
                        "provider": "nvidia",
                        "model": "judge-model",
                        "supports_vision": False,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_and_validate_profile_models("no_vision_profile", config_path=no_vision_yaml)
    assert exc_info.value.error_code == "MODEL_LACKS_VISION_CAPABILITY"


def test_capabilities_google_gemma_with_supports_vision_true() -> None:
    caps_map = {
        ("nvidia", "google/gemma-4-31b-it"): ModelCapabilities(
            supports_vision=True,
            remote=True,
            baa_covered=False,
        )
    }
    assert is_vision_model("google/gemma-4-31b-it", capabilities_map=caps_map) is True
    assert is_cloud_model("google/gemma-4-31b-it", capabilities_map=caps_map) is True


def test_remote_model_without_nvidia_prefix_triggers_safety_lock() -> None:
    caps_map = {
        ("custom", "custom-hosted-provider/medical-llm-70b"): ModelCapabilities(
            supports_vision=False,
            remote=True,
            baa_covered=False,
        )
    }
    assert (
        is_cloud_model("custom-hosted-provider/medical-llm-70b", capabilities_map=caps_map) is True
    )

    # Fails safety lock on non-golden document
    with pytest.raises(CloudModelSafetyLockError):
        verify_cloud_model_safety_lock(
            model_name="custom-hosted-provider/medical-llm-70b",
            active_document_titles=["real_patient_chart.pdf"],
            active_document_hashes=["a" * 64],
            capabilities_map=caps_map,
        )


def test_mock_and_local_models_do_not_trigger_safety_lock() -> None:
    caps_map = {
        ("ollama", "qwen3-vl:4b"): ModelCapabilities(
            supports_vision=True,
            remote=False,
            baa_covered=False,
        ),
        ("mock", "mock-answer-v1"): ModelCapabilities(
            supports_vision=True,
            remote=False,
            baa_covered=False,
        ),
    }

    assert is_cloud_model("qwen3-vl:4b", capabilities_map=caps_map) is False
    assert is_cloud_model("mock-answer-v1", capabilities_map=caps_map) is False

    # Does not raise even on non-golden doc
    verify_cloud_model_safety_lock(
        model_name="qwen3-vl:4b",
        active_document_titles=["real_patient_chart.pdf"],
        active_document_hashes=["a" * 64],
        capabilities_map=caps_map,
    )
    verify_cloud_model_safety_lock(
        model_name="mock-answer-v1",
        active_document_titles=["real_patient_chart.pdf"],
        active_document_hashes=["a" * 64],
        capabilities_map=caps_map,
    )


def test_moonshot_remote_non_baa_triggers_safety_lock() -> None:
    caps_map = {
        ("moonshot", "moonshotai/kimi-k2.6"): ModelCapabilities(
            supports_vision=False,
            remote=True,
            baa_covered=False,
        )
    }
    assert is_cloud_model("moonshotai/kimi-k2.6", capabilities_map=caps_map) is True
    with pytest.raises(CloudModelSafetyLockError):
        verify_cloud_model_safety_lock(
            model_name="moonshotai/kimi-k2.6",
            active_document_titles=["patient_record.pdf"],
            active_document_hashes=["a" * 64],
            capabilities_map=caps_map,
        )


def test_different_casing_or_prefix_does_not_share_capabilities() -> None:
    caps_map = {
        ("nvidia", "model-abc"): ModelCapabilities(
            supports_vision=True,
            remote=False,
            baa_covered=False,
        )
    }
    # Exact match succeeds
    assert is_vision_model("model-abc", capabilities_map=caps_map) is True

    # Differing case raises ConfigurationError
    with pytest.raises(ConfigurationError) as exc1:
        is_vision_model("Model-ABC", capabilities_map=caps_map)
    assert exc1.value.error_code == "MODEL_CAPABILITIES_NOT_DECLARED"

    # Differing prefix raises ConfigurationError
    with pytest.raises(ConfigurationError) as exc2:
        is_vision_model("nvidia/model-abc", capabilities_map=caps_map)
    assert exc2.value.error_code == "MODEL_CAPABILITIES_NOT_DECLARED"


def test_remote_model_with_missing_capabilities_fails_at_startup(tmp_path: Path) -> None:
    bad_yaml = tmp_path / "missing_caps.yaml"
    bad_yaml.write_text(
        yaml.dump(
            {
                "profile": "bad",
                "models": {
                    "router": {
                        "provider": "nvidia",
                        "model": "remote-m1",
                        "supports_vision": False,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "answer": {
                        "provider": "nvidia",
                        "model": "remote-m1",
                        "supports_vision": False,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "vision": {
                        "provider": "nvidia",
                        "model": "remote-m2",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "extract": {
                        "provider": "nvidia",
                        "model": "remote-m2",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "judge": {"provider": "nvidia", "model": "remote-m3"},
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc:
        load_and_validate_profile_models("bad", config_path=bad_yaml)
    assert exc.value.error_code == "CAPABILITY_FLAG_MISSING"


def test_deprecated_model_environment_variables_fail_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.api.config import ApiSettings

    for var_name in ("ANSWER_MODEL", "CHAT_MODEL", "VISION_MODEL"):
        monkeypatch.setenv(var_name, "test-override-model")
        with pytest.raises(ConfigurationError) as exc:
            ApiSettings(profile="mock", storage_dir=str(tmp_path))
        assert exc.value.error_code == "DEPRECATED_MODEL_ENV_VAR"
        assert f"Environment variable '{var_name}' is no longer supported" in str(exc.value)

        with pytest.raises(ConfigurationError) as exc2:
            load_and_validate_profile_models("mock")
        assert exc2.value.error_code == "DEPRECATED_MODEL_ENV_VAR"
        monkeypatch.delenv(var_name, raising=False)


def test_model_id_substitution_is_config_only(tmp_path: Path) -> None:
    """Proves swapping to google/gemma-4-31b-it for any role is a config-only change."""
    substituted_yaml = tmp_path / "custom_api.yaml"
    substituted_yaml.write_text(
        yaml.dump(
            {
                "profile": "custom_api",
                "models": {
                    "router": {
                        "provider": "nvidia",
                        "model": "google/gemma-4-31b-it",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "answer": {
                        "provider": "nvidia",
                        "model": "google/gemma-4-31b-it",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "vision": {
                        "provider": "nvidia",
                        "model": "google/gemma-4-31b-it",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "extract": {
                        "provider": "nvidia",
                        "model": "google/gemma-4-31b-it",
                        "supports_vision": True,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                    "judge": {
                        "provider": "nvidia",
                        "model": "mistralai/mistral-large-4-1t-a52b",
                        "supports_vision": False,
                        "remote": True,
                        "baa_covered": False,
                        "temperature": 0.0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    resolved = load_and_validate_profile_models("custom_api", config_path=substituted_yaml)
    assert resolved["answer"]["model"] == "google/gemma-4-31b-it"
    assert resolved["vision"]["model"] == "google/gemma-4-31b-it"
    assert (
        is_vision_model("google/gemma-4-31b-it", capabilities_map=resolved["_capabilities_map"])
        is True
    )
