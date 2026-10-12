"""Unit tests for wiring profile guards (Task 2.5 Step 2).

Verifies:
1. create_model_provider("mock") returns MockModelProvider.
2. Non-mock profiles (office-cpu, desktop-gpu, cloud) refuse MockModelProvider
   and raise ConfigurationError until real providers are implemented.
3. Unknown profiles raise ConfigurationError.
4. create_image_processor returns PillowImageProcessor.
"""

from __future__ import annotations

import pytest

from app.adapters.images.pillow import PillowImageProcessor
from app.adapters.models.mock import MockModelProvider
from app.ports.errors import ConfigurationError
from app.wiring import create_image_processor, create_model_provider


class TestModelProviderWiringGuard:
    """Verifies that model provider instantiation respects runtime profile allowlist."""

    def test_create_model_provider_mock_profile(self) -> None:
        """Mock profile wires MockModelProvider."""
        provider = create_model_provider("mock")
        assert isinstance(provider, MockModelProvider)

    @pytest.mark.parametrize(
        "profile",
        ["office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "cloud"],
    )
    def test_create_model_provider_non_mock_refuses_mock(self, profile: str) -> None:
        """Non-mock profiles never return MockModelProvider; raise ConfigurationError."""
        with pytest.raises(ConfigurationError) as exc_info:
            create_model_provider(profile)
        # Verify it refuses mock and signals implementation pending
        assert "MockModelProvider" not in str(exc_info.value) or "forbidden" in str(exc_info.value)
        assert exc_info.value.error_code in (
            "MODEL_PROVIDER_NOT_IMPLEMENTED",
            "UNAUTHORIZED_MODEL_PROVIDER_PROFILE",
        )

    def test_create_model_provider_unknown_profile_raises(self) -> None:
        """Unknown or unauthorized profiles raise ConfigurationError."""
        with pytest.raises(ConfigurationError) as exc_info:
            create_model_provider("unrecognized_test_profile")
        assert exc_info.value.error_code == "UNAUTHORIZED_MODEL_PROVIDER_PROFILE"

    def test_create_image_processor(self) -> None:
        """Image processor factory returns PillowImageProcessor."""
        processor = create_image_processor("mock")
        assert isinstance(processor, PillowImageProcessor)
