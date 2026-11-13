"""Role router for independent model providers across all roles."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from app.ports.errors import ConfigurationError
from app.ports.model_provider import ModelCapabilities, ModelProvider
from app.ports.types import (
    ChatMessage,
    ExtractedFields,
    ModelResponse,
    ModelRole,
    PageReading,
)


class ModelProviderRouter(ModelProvider):
    """Route each functional ModelRole to its configured ModelProvider."""

    def __init__(
        self,
        providers: dict[ModelRole, ModelProvider] | None = None,
        *,
        capabilities_map: Mapping[tuple[str, str], ModelCapabilities] | None = None,
        answer_provider: ModelProvider | None = None,
        vision_provider: ModelProvider | None = None,
        enforce_distinct_judge: bool = False,
    ) -> None:
        self.capabilities_map: Mapping[tuple[str, str], ModelCapabilities] = capabilities_map or {}
        if providers is not None:
            self._providers = dict(providers)
        else:
            if answer_provider is None or vision_provider is None:
                raise ConfigurationError(
                    message=(
                        "ModelProviderRouter requires either providers mapping "
                        "or both answer and vision providers"
                    ),
                    error_code="ROUTER_CONFIGURATION_MISSING",
                )
            self._providers = {
                ModelRole.ROUTER: answer_provider,
                ModelRole.ANSWER: answer_provider,
                ModelRole.JUDGE: answer_provider,
                ModelRole.VISION: vision_provider,
                ModelRole.EXTRACT: vision_provider,
            }

        if enforce_distinct_judge:
            ans_p = self._providers.get(ModelRole.ANSWER)
            jdg_p = self._providers.get(ModelRole.JUDGE)
            if ans_p is not None and jdg_p is not None:
                ans_model = getattr(ans_p, "_model_name", None) or getattr(
                    ans_p, "model_name", None
                )
                jdg_model = getattr(jdg_p, "_model_name", None) or getattr(
                    jdg_p, "model_name", None
                )
                if ans_p is jdg_p or (ans_model and jdg_model and ans_model == jdg_model):
                    raise ConfigurationError(
                        message="Judge model must differ from answer model in desktop-api profile",
                        error_code="JUDGE_MODEL_SAME_AS_ANSWER",
                    )

    def get_provider(self, role: ModelRole) -> ModelProvider:
        provider = self._providers.get(role)
        if provider is not None:
            return provider
        if ModelRole.ANSWER in self._providers:
            return self._providers[ModelRole.ANSWER]
        raise ConfigurationError(
            message=f"No provider configured for role '{role.value}'",
            error_code="ROLE_PROVIDER_MISSING",
        )

    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        provider = self.get_provider(role)
        return await provider.generate(role, messages, schema=schema, max_tokens=max_tokens)

    async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReading:
        provider = self.get_provider(ModelRole.VISION)
        return await provider.transcribe_page(role, image)

    async def read_page(self, role: ModelRole, image: bytes, question: str) -> PageReading:
        provider = self.get_provider(ModelRole.VISION)
        return await provider.read_page(role, image, question)

    async def extract_fields(
        self, role: ModelRole, image: bytes, schema: type[BaseModel]
    ) -> ExtractedFields:
        provider = self.get_provider(ModelRole.EXTRACT)
        return await provider.extract_fields(role, image, schema)
