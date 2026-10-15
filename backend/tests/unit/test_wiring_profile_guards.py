"""Unit tests for wiring profile guards (Task 2.5 Step 2).

Verifies:
1. create_model_provider("mock") returns MockModelProvider.
2. Non-mock profiles (office-cpu, desktop-gpu, cloud) refuse MockModelProvider
   and raise ConfigurationError until real providers are implemented.
3. Unknown profiles raise ConfigurationError.
4. create_image_processor returns PillowImageProcessor.
"""

from pathlib import Path

import pytest

from app.adapters.images.pillow import PillowImageProcessor
from app.adapters.models.mock import MockModelProvider
from app.adapters.parsing.docling import DoclingDocumentParser
from app.adapters.parsing.mock import MockDocumentParser
from app.ports.errors import ConfigurationError
from app.wiring import (
    create_document_parser,
    create_image_processor,
    create_model_provider,
)


class TestModelProviderWiringGuard:
    """Verifies that model provider instantiation respects runtime profile allowlist."""

    def test_create_model_provider_mock_profile(self) -> None:
        """Mock profile wires MockModelProvider."""
        provider = create_model_provider("mock")
        assert isinstance(provider, MockModelProvider)

    @pytest.mark.parametrize(
        "profile",
        ["office-cpu", "dev-free-api", "cloud"],
    )
    def test_create_model_provider_unimplemented_refuses_mock(self, profile: str) -> None:
        """Unimplemented profiles never return MockModelProvider; raise ConfigurationError."""
        with pytest.raises(ConfigurationError) as exc_info:
            create_model_provider(profile)
        # Verify it refuses mock and signals implementation pending
        assert "MockModelProvider" not in str(exc_info.value) or "forbidden" in str(exc_info.value)
        assert exc_info.value.error_code in (
            "MODEL_PROVIDER_NOT_IMPLEMENTED",
            "UNAUTHORIZED_MODEL_PROVIDER_PROFILE",
        )

    @pytest.mark.parametrize(
        "profile",
        ["desktop-gpu", "home-gpu"],
    )
    def test_create_model_provider_gpu_wires_ollama_provider(
        self, profile: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """desktop-gpu and home-gpu return OllamaModelProvider when OLLAMA_BASE_URL is set."""
        from app.adapters.models.ollama import OllamaModelProvider

        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
        provider = create_model_provider(profile)
        assert isinstance(provider, OllamaModelProvider)

    def test_create_model_provider_unknown_profile_raises(self) -> None:
        """Unknown or unauthorized profiles raise ConfigurationError."""
        with pytest.raises(ConfigurationError) as exc_info:
            create_model_provider("unrecognized_test_profile")
        assert exc_info.value.error_code == "UNAUTHORIZED_MODEL_PROVIDER_PROFILE"

    def test_create_image_processor(self) -> None:
        """Image processor factory returns PillowImageProcessor."""
        processor = create_image_processor("mock")
        assert isinstance(processor, PillowImageProcessor)


class TestPagePipelineWiringGuard:
    """Verifies that PagePipelineService wiring fails closed in non-mock profiles."""

    @pytest.mark.parametrize(
        "profile",
        ["office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "cloud", "unrecognized_profile"],
    )
    def test_page_pipeline_fails_closed_in_non_mock_profile(self, profile: str) -> None:
        """In non-mock profiles, building the pipeline without provider/processor
        raises ConfigurationError."""
        from unittest.mock import MagicMock

        from app.wiring import create_page_pipeline_service

        mock_session = MagicMock()
        with pytest.raises(ConfigurationError):
            create_page_pipeline_service(mock_session, profile=profile)

    def test_page_pipeline_fails_closed_without_model_provider_in_non_mock(self) -> None:
        """Building page pipeline with explicit model_provider=None in non-mock profile
        raises ConfigurationError."""
        from unittest.mock import MagicMock

        from app.wiring import create_page_pipeline_service

        mock_session = MagicMock()
        with pytest.raises(ConfigurationError, match="model provider"):
            create_page_pipeline_service(
                mock_session,
                profile="office-cpu",
                parser=MagicMock(),
                chunker=MagicMock(),
                embedding_provider=MagicMock(),
                model_provider=None,
                image_processor=MagicMock(),
                storage=MagicMock(),
            )

    def test_page_pipeline_fails_closed_without_image_processor_in_non_mock(self) -> None:
        """Building page pipeline with explicit image_processor=None in non-mock profile
        raises ConfigurationError."""
        from unittest.mock import MagicMock

        from app.wiring import create_page_pipeline_service

        mock_session = MagicMock()
        with pytest.raises(ConfigurationError, match="image processor"):
            create_page_pipeline_service(
                mock_session,
                profile="office-cpu",
                parser=MagicMock(),
                chunker=MagicMock(),
                embedding_provider=MagicMock(),
                model_provider=MagicMock(),
                image_processor=None,
                storage=MagicMock(),
            )


class TestDocumentParserWiringGuard:
    """Verifies create_document_parser profile rules and DOCLING_ARTIFACTS_PATH guard."""

    def test_mock_profile_does_not_need_artifacts_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mock profile returns MockDocumentParser without needing DOCLING_ARTIFACTS_PATH."""
        monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
        parser = create_document_parser("mock")
        assert isinstance(parser, MockDocumentParser)

    @pytest.mark.parametrize(
        "profile",
        ["office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "cloud"],
    )
    def test_non_mock_profile_missing_artifacts_path_raises(
        self, profile: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-mock profiles raise ConfigurationError when DOCLING_ARTIFACTS_PATH is missing."""
        monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
        with pytest.raises(ConfigurationError) as exc_info:
            create_document_parser(profile)
        assert exc_info.value.message == "DOCLING_ARTIFACTS_PATH environment variable is missing."

    @pytest.mark.parametrize(
        "profile",
        ["office-cpu", "desktop-gpu", "home-gpu", "dev-free-api", "cloud"],
    )
    def test_non_mock_profile_non_existent_artifacts_path_raises(
        self, profile: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Non-mock profiles raise ConfigurationError when artifacts folder does not exist."""
        non_existent = tmp_path / "missing_models_folder"
        monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(non_existent))
        with pytest.raises(ConfigurationError) as exc_info:
            create_document_parser(profile)
        assert exc_info.value.message == "DOCLING_ARTIFACTS_PATH directory does not exist."

    @pytest.mark.parametrize(
        "profile,expected_device",
        [
            ("office-cpu", "cpu"),
            ("desktop-gpu", "cuda"),
            ("home-gpu", "cuda"),
            ("dev-free-api", "cpu"),
            ("cloud", "cpu"),
        ],
    )
    def test_non_mock_profile_existing_artifacts_path_succeeds(
        self,
        profile: str,
        expected_device: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Non-mock profiles instantiate DoclingDocumentParser with configured device."""
        models_folder = tmp_path / "models"
        models_folder.mkdir()
        monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(models_folder))
        parser = create_document_parser(profile)
        assert isinstance(parser, DoclingDocumentParser)
        assert parser.artifacts_path == models_folder
        assert parser.device == expected_device


def test_docling_module_has_no_top_level_docling_imports() -> None:
    """Verify app.adapters.parsing.docling contains zero top-level docling imports.

    Parsing group dependencies must be lazy-imported inside methods/functions
    so the module remains importable without the parsing dependency group.
    """
    import ast

    import app.adapters.parsing.docling as docling_module

    source_path = Path(docling_module.__file__)
    source_code = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source_code, filename=str(source_path))

    top_level_docling_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "docling" or alias.name.startswith("docling."):
                    top_level_docling_imports.append(alias.name)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "docling" or node.module.startswith("docling."))
        ):
            top_level_docling_imports.append(node.module)

    assert top_level_docling_imports == []
