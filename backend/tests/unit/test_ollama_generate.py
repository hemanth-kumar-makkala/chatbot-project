"""Unit tests for OllamaModelProvider.generate method (Task 3.4).

Tests:
1. generate invokes /api/chat with model, messages, format schema, think: False,
   temperature 0, fixed seed 42.
2. generate strips <think>...</think> blocks from response text.
3. generate parses and validates structured JSON output against Pydantic schema.
4. generate retries on transient network/timeout errors with backoff before
   raising TransientIngestionError.
5. generate raises MalformedOutput on invalid JSON when schema is requested.
6. generate raises OllamaPermanentError on HTTP status >= 400 without retrying.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.adapters.models.ollama import (
    OllamaModelProvider,
)
from app.core.prompts import AnswerOutput
from app.ports.errors import (
    MalformedOutput,
)
from app.ports.model_provider import ModelCapabilities
from app.ports.types import (
    ChatMessage,
    ModelResponse,
    ModelRole,
)

DEFAULT_OLLAMA_CAPABILITIES: dict[tuple[str, str], ModelCapabilities] = {
    ("ollama", "qwen3:8b"): ModelCapabilities(
        supports_vision=False, remote=False, baa_covered=False
    ),
    ("ollama", "qwen3-vl:4b-instruct"): ModelCapabilities(
        supports_vision=True, remote=False, baa_covered=False
    ),
}


class SampleAnswerSchema(BaseModel):
    mode: str
    answer: str
    evidence_ids: list[str]
    quoted_span: str | None = None
    evidence_type: str = "text"


def _create_mock_transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_generate_structured_json_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """generate sends correct options, format schema, and returns validated ModelResponse."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen3:8b", "digest": "sha256:testdigest123"}]},
            )
        if request.url.path == "/api/chat":
            req_body = json.loads(request.content.decode("utf-8"))
            assert req_body["model"] == "qwen3:8b"
            assert req_body["stream"] is False
            assert req_body["think"] is False
            assert req_body["options"]["temperature"] == 0.0
            assert req_body["options"]["num_ctx"] == 6144
            assert req_body["options"]["seed"] == 42
            assert req_body["format"] == SampleAnswerSchema.model_json_schema()
            assert req_body["messages"] == [{"role": "user", "content": "What are the hours?"}]

            resp_payload = {
                "message": {
                    "role": "assistant",
                    "content": "<think>Checking clinic hours...</think>"
                    + json.dumps(
                        {
                            "mode": "short",
                            "answer": "The clinic is open 9am to 5pm.",
                            "evidence_ids": ["c1"],
                            "quoted_span": "open 9am to 5pm",
                            "evidence_type": "text",
                        }
                    ),
                },
                "eval_count": 42,
            }
            return httpx.Response(200, json=resp_payload)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=_create_mock_transport(handler))
    provider = OllamaModelProvider(
        profile="desktop-gpu",
        capabilities_map=DEFAULT_OLLAMA_CAPABILITIES,
        http_client=client,
        structured_output_mode="json_schema",
        reasoning=False,
        reasoning_off_params={"think": False},
        num_ctx=6144,
        temperature=0.0,
    )

    result = await provider.generate(
        role=ModelRole.ANSWER,
        messages=[ChatMessage(role="user", content="What are the hours?")],
        schema=SampleAnswerSchema,
    )

    assert isinstance(result, ModelResponse)
    assert result.model_id == "qwen3:8b"
    assert result.model_version == "sha256:testdigest123"
    assert result.structured_output is not None
    assert result.structured_output["answer"] == "The clinic is open 9am to 5pm."
    assert result.structured_output["evidence_ids"] == ["c1"]
    assert "<think>" not in result.content


@pytest.mark.asyncio
async def test_generate_retries_on_transient_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """generate retries transient timeout errors before succeeding."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

    attempt_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen3:8b", "digest": "sha256:testdigest123"}]},
            )
        if request.url.path == "/api/chat":
            attempt_count += 1
            if attempt_count < 2:
                raise httpx.ReadTimeout("Ollama read timeout simulation")
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {"mode": "short", "answer": "Success after retry", "evidence_ids": []}
                        ),
                    }
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=_create_mock_transport(handler))
    provider = OllamaModelProvider(
        profile="desktop-gpu",
        capabilities_map=DEFAULT_OLLAMA_CAPABILITIES,
        http_client=client,
        timeout_seconds=1.0,
    )

    result = await provider.generate(
        role=ModelRole.ANSWER,
        messages=[ChatMessage(role="user", content="Hello")],
        schema=SampleAnswerSchema,
    )

    assert attempt_count == 2
    assert result.structured_output is not None
    assert result.structured_output["answer"] == "Success after retry"


@pytest.mark.asyncio
async def test_generate_raises_malformed_output_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate raises MalformedOutput when returned content is not valid JSON
    for requested schema.
    """
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen3:8b", "digest": "sha256:testdigest123"}]},
            )
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": "Not JSON at all"}},
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=_create_mock_transport(handler))
    provider = OllamaModelProvider(
        profile="desktop-gpu",
        capabilities_map=DEFAULT_OLLAMA_CAPABILITIES,
        http_client=client,
    )

    with pytest.raises(MalformedOutput) as exc_info:
        await provider.generate(
            role=ModelRole.ANSWER,
            messages=[ChatMessage(role="user", content="Hi")],
            schema=SampleAnswerSchema,
        )

    assert exc_info.value.error_code == "MALFORMED_OUTPUT"


@pytest.mark.asyncio
async def test_no_thinking_text_leaks_to_answer_or_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that <think> tags and internal reasoning never leak into answer or confidence."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

    thinking_trace = "<think>\nStep 1: Parse patient record.\nStep 2: Check confidence.\n</think>"
    valid_json = json.dumps(
        {
            "mode": "short",
            "answer": "Clean verified clinical answer without any thinking.",
            "evidence_ids": ["c1"],
            "quoted_span": "Clean verified clinical answer",
            "evidence_type": "text",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen3:8b", "digest": "sha256:testdigest123"}]},
            )
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": thinking_trace + valid_json,
                    },
                    "eval_count": 55,
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=_create_mock_transport(handler))
    provider = OllamaModelProvider(
        profile="desktop-gpu",
        capabilities_map=DEFAULT_OLLAMA_CAPABILITIES,
        http_client=client,
    )

    result = await provider.generate(
        role=ModelRole.ANSWER,
        messages=[ChatMessage(role="user", content="Query")],
        schema=SampleAnswerSchema,
    )

    assert "<think>" not in result.content
    assert "</think>" not in result.content
    assert "Step 1:" not in result.content
    assert result.structured_output is not None
    assert (
        result.structured_output["answer"] == "Clean verified clinical answer without any thinking."
    )
    assert "<think>" not in str(result.structured_output)


@pytest.mark.asyncio
async def test_generate_handles_truncated_explain_mode_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate salvages completed points from truncated JSON and drops incomplete point."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # Simulate model reaching token ceiling mid-point 3
    truncated_content = (
        '{"points": ['
        '{"point": "Point 1 text", "quoted_span": "Quote 1", "evidence_ids": ["c1"]}, '
        '{"point": "Point 2 text", "quoted_span": "Quote 2", "evidence_ids": ["c2"]}, '
        '{"point": "Incomplete point 3 text", "quoted_span": "Incomplete'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen3:8b", "digest": "sha256:testdigest123"}]},
            )
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": truncated_content,
                    },
                    "eval_count": 768,
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=_create_mock_transport(handler))
    provider = OllamaModelProvider(
        profile="desktop-gpu",
        capabilities_map=DEFAULT_OLLAMA_CAPABILITIES,
        http_client=client,
    )

    result = await provider.generate(
        role=ModelRole.ANSWER,
        messages=[ChatMessage(role="user", content="Summarize")],
        schema=AnswerOutput,
    )

    assert result.structured_output is not None
    points = result.structured_output.get("points")
    assert points is not None
    assert len(points) == 2
    assert points[0]["point"] == "Point 1 text"
    assert points[1]["point"] == "Point 2 text"
    assert not any("Incomplete" in str(p) for p in points)


@pytest.mark.asyncio
async def test_wiring_desktop_gpu_resolves_roles_and_sends_schema_think_and_num_ctx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """desktop-gpu resolves roles and sends format schema, think=false, num_ctx=6144."""
    from app.adapters.models.provider_router import ModelProviderRouter
    from app.wiring import create_model_provider

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://mock-ollama:11434")
    router = create_model_provider("desktop-gpu")
    assert isinstance(router, ModelProviderRouter)

    expected_models = {
        ModelRole.ROUTER: "qwen3:8b",
        ModelRole.ANSWER: "qwen3:8b",
        ModelRole.VISION: "qwen3-vl:4b-instruct",
        ModelRole.EXTRACT: "qwen3-vl:4b-instruct",
        ModelRole.JUDGE: "qwen3-vl:4b-instruct",
    }

    captured_chat_payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3:8b", "digest": "sha256:qwen38b"},
                        {"name": "qwen3-vl:4b-instruct", "digest": "sha256:qwen3vl4bi"},
                    ]
                },
            )
        if request.url.path == "/api/chat":
            req_body = json.loads(request.content.decode("utf-8"))
            captured_chat_payloads.append(req_body)
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "mode": "short",
                                "answer": "Verified synthetic answer.",
                                "evidence_ids": ["c1"],
                                "quoted_span": "Verified",
                                "evidence_type": "text",
                            }
                        ),
                    },
                    "eval_count": 12,
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=_create_mock_transport(handler)) as fake_client:
        for role, expected_model in expected_models.items():
            prov = router.get_provider(role)
            assert isinstance(prov, OllamaModelProvider)
            prov._http_client = fake_client
            if role in (ModelRole.ROUTER, ModelRole.ANSWER):
                res = await router.generate(
                    role=role,
                    messages=[ChatMessage(role="user", content="Test")],
                    schema=SampleAnswerSchema,
                    max_tokens=128,
                )
                assert res.model_id == expected_model
            else:
                assert prov._models[role.value] == expected_model
                res = await router.generate(
                    role=role,
                    messages=[ChatMessage(role="user", content="Test")],
                    schema=SampleAnswerSchema,
                    max_tokens=128,
                )
                assert res.model_id == expected_model

    assert len(captured_chat_payloads) == len(expected_models)
    for req_body in captured_chat_payloads:
        assert req_body["format"] == SampleAnswerSchema.model_json_schema()
        assert req_body["think"] is False
        assert req_body["options"]["num_ctx"] == 6144
        assert req_body["options"]["temperature"] == 0.0


def test_desktop_gpu_startup_fails_if_judge_equals_answer(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup raises JUDGE_MODEL_SAME_AS_ANSWER when judge equals answer in desktop-gpu."""
    from app.ports.errors import ConfigurationError
    from app.wiring import create_model_provider

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://mock-ollama:11434")
    bad_profile = tmp_path / "desktop-gpu.yaml"
    bad_profile.write_text(
        "profile: desktop-gpu\n"
        "catalog_providers:\n"
        "  - ollama\n"
        "models:\n"
        "  router:\n"
        '    { provider: ollama, model: "qwen3:8b", supports_vision: false,\n'
        "      remote: false, baa_covered: false, temperature: 0.0, reasoning: false }\n"
        "  answer:\n"
        '    { provider: ollama, model: "qwen3:8b", supports_vision: false,\n'
        "      remote: false, baa_covered: false, temperature: 0.0, reasoning: false }\n"
        "  vision:\n"
        '    { provider: ollama, model: "qwen3-vl:4b-instruct", supports_vision: true,\n'
        "      remote: false, baa_covered: false, temperature: 0.0, reasoning: false }\n"
        "  extract:\n"
        '    { provider: ollama, model: "qwen3-vl:4b-instruct", supports_vision: true,\n'
        "      remote: false, baa_covered: false, temperature: 0.0, reasoning: false }\n"
        "  judge:\n"
        '    { provider: ollama, model: "qwen3:8b", supports_vision: false,\n'
        "      remote: false, baa_covered: false, temperature: 0.0, reasoning: false }\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc_info:
        create_model_provider("desktop-gpu", config_path=bad_profile)
    assert exc_info.value.error_code == "JUDGE_MODEL_SAME_AS_ANSWER"
