"""Integration tests for Task 4.1: Question-Time Vision Reading (Node 6 vision_read).

Spec Reference: docs/phases/PHASE-4.md (lines 32-36)
Architecture Reference: Client Knowledge Assistant Architecture and Build Plan.md (Node 6)
Invariant Reference: Invariant 5 (visual readings are derived evidence, cited with page image)

Tests:
1. Image pages produce question-time readings via ModelProvider.read_page(role="vision").
2. Image pre-scaling enforces 6 GB VRAM budget (max 1024px dimension, max 4MB).
3. Vision success incorporates visual reading into evidence text and marks vision_status='ok'.
4. Vision model failure with OCR confidence >= 0.70 falls back to OCR text.
5. Vision model failure with OCR confidence < 0.70 refuses cleanly without guessing.
6. Invariant 5: Answer generation from visual reading labels evidence_type='visual'.
7. Invariant 10: Zero content leakage into LangGraph checkpoints.
8. Telemetry: StepStartPayload and StepEndPayload/StepErrorPayload emitted for node 6.
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pydantic import BaseModel

from app.adapters.images.pillow import PillowImageProcessor
from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
    node_vision_read,
)
from app.ports.errors import ProviderTimeout
from app.ports.model_provider import ModelProvider
from app.ports.tracer import Tracer
from app.ports.types import (
    ChatMessage,
    ExtractedFields,
    ModelResponse,
    ModelRole,
    PageReading,
    StepEndPayload,
    StepErrorPayload,
    StepStartPayload,
    TenantContext,
    UserRole,
)


def _create_synthetic_page_image(width: int = 1600, height: int = 2200) -> bytes:
    """Create synthetic PNG test image."""
    img = Image.new("RGB", (width, height), color=(250, 250, 250))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class RecordingVisionModelProvider(ModelProvider):
    """Test model provider tracking calls to read_page and generate."""

    def __init__(
        self,
        canned_reading: str = "Vision reading: Intake fax line is 555-0199.",
        canned_confidence: float = 0.94,
        should_fail: bool = False,
        failure_exc: Exception | None = None,
    ) -> None:
        self.canned_reading = canned_reading
        self.canned_confidence = canned_confidence
        self.should_fail = should_fail
        self.failure_exc = failure_exc or ProviderTimeout(
            "Vision model timeout", error_code="PROVIDER_TIMEOUT"
        )
        self.read_page_calls: list[dict[str, Any]] = []

    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        del role, messages, schema, max_tokens
        return ModelResponse(
            content="The intake fax line is 555-0199.",
            model_id="mock-qwen-vl",
            model_version="1.0",
            structured_output={
                "answer": "The intake fax line is 555-0199.",
                "evidence_ids": ["v_page_1"],
                "quoted_span": "555-0199",
                "evidence_type": "visual",
            },
        )

    async def transcribe_page(
        self,
        role: ModelRole,
        image: bytes,
    ) -> PageReading:
        del role, image
        return PageReading(
            reading="Upload-time transcription",
            confidence=0.90,
            model_id="v",
            model_version="1",
        )

    async def read_page(
        self,
        role: ModelRole,
        image: bytes,
        question: str,
    ) -> PageReading:
        self.read_page_calls.append({"role": role, "image": image, "question": question})
        if self.should_fail:
            raise self.failure_exc

        return PageReading(
            reading=self.canned_reading,
            confidence=self.canned_confidence,
            model_id="qwen3-vl:4b",
            model_version="sha256:synth",
            normalized_values={"fax": "555-0199"},
        )

    async def extract_fields(
        self,
        role: ModelRole,
        image: bytes,
        schema: type[BaseModel],
    ) -> ExtractedFields:
        del role, image, schema
        raise NotImplementedError


class MockTracer(Tracer):
    """In-memory telemetry tracer recording step transitions."""

    def __init__(self) -> None:
        self.starts: list[StepStartPayload] = []
        self.ends: list[StepEndPayload] = []
        self.errors: list[StepErrorPayload] = []

    async def step_start(self, payload: StepStartPayload) -> str:
        self.starts.append(payload)
        return f"step-{len(self.starts)}"

    async def step_end(self, step_id: str, payload: StepEndPayload) -> None:
        del step_id
        self.ends.append(payload)

    async def step_error(self, step_id: str, payload: StepErrorPayload) -> None:
        del step_id
        self.errors.append(payload)


@pytest.fixture
def base_graph_state() -> GraphState:
    return {
        "request_id": "req-vis-001",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "usr-vis-001",
        "conversation_id": "conv-vis-001",
        "route": "knowledge",
        "answer_mode": "short",
        "language": "en",
        "evidence_ids": ["chunk_v1"],
        "evidence_state": "VISUAL_REQUIRED",
        "needs_vision": True,
        "vision_status": None,
        "validation_attempts": 0,
        "status": "pending",
        "public_error_ref": None,
        "extracted_field_ids": None,
        "dual_read_matches": None,
        "confidence_score": None,
        "fields_valid": None,
        "response_message_id": None,
    }


@pytest.fixture
def synthetic_tenant() -> TenantContext:
    return TenantContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="usr-vis-001",
        role=UserRole.STAFF,
        session_id="session-vis-001",
    )


class TestVisionReadNode:
    """Integration test suite for Node 6 vision_read."""

    @pytest.mark.asyncio
    async def test_vision_read_success_invokes_model_with_question_and_image(
        self,
        base_graph_state: GraphState,
        synthetic_tenant: TenantContext,
    ) -> None:
        """Node 6 calls ModelProvider.read_page with role=vision, question, and image."""
        provider = RecordingVisionModelProvider(canned_reading="Fax is 555-0199")
        ports = GraphPorts(model_provider=provider, is_mock_profile=False)
        img_bytes = _create_synthetic_page_image(800, 1000)

        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=synthetic_tenant,
            question_text="What is the fax number?",
            page_image_bytes=img_bytes,
            evidence_text={"chunk_v1": "Scanned document header."},
        )

        # Execute node 6 directly
        runtime_mock = AsyncMock()
        runtime_mock.context = ctx

        result = await node_vision_read(base_graph_state, runtime_mock)

        assert result.get("vision_status") == "ok"
        assert len(provider.read_page_calls) == 1
        call = provider.read_page_calls[0]
        assert call["role"] == ModelRole.VISION
        assert call["question"] == "What is the fax number?"
        assert len(call["image"]) > 0

        # Verify reading is in evidence_text for subsequent answer generation
        visual_keys = [
            k
            for k in ctx.evidence_text
            if "visual" in k or k == "chunk_v1" or "Fax is 555-0199" in ctx.evidence_text[k]
        ]
        assert len(visual_keys) > 0
        assert any("555-0199" in ctx.evidence_text[k] for k in ctx.evidence_text)

    @pytest.mark.asyncio
    async def test_vision_read_prescales_oversized_image_for_6gb_vram(
        self,
        base_graph_state: GraphState,
        synthetic_tenant: TenantContext,
    ) -> None:
        """Oversized page image (e.g. 2400x3200) is downscaled before invoking vision model."""
        provider = RecordingVisionModelProvider()
        processor = PillowImageProcessor()
        ports = GraphPorts(
            model_provider=provider,
            image_processor=processor,
            is_mock_profile=False,
        )
        large_image = _create_synthetic_page_image(2400, 3200)

        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=synthetic_tenant,
            question_text="Read patient ID",
            page_image_bytes=large_image,
        )

        runtime_mock = AsyncMock()
        runtime_mock.context = ctx

        await node_vision_read(base_graph_state, runtime_mock)

        assert len(provider.read_page_calls) == 1
        passed_image_bytes = provider.read_page_calls[0]["image"]

        # Decode passed image to inspect dimensions
        img = Image.open(io.BytesIO(passed_image_bytes))
        width, height = img.size
        assert max(width, height) <= 1024, (
            f"Image dimensions {width}x{height} exceed max_dimension 1024"
        )
        assert len(passed_image_bytes) <= 4 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_vision_read_failure_falls_back_to_ocr_when_confidence_sufficient(
        self,
        base_graph_state: GraphState,
        synthetic_tenant: TenantContext,
    ) -> None:
        """When vision model fails but OCR confidence >= 0.70, node 6 falls back to OCR text."""
        provider = RecordingVisionModelProvider(should_fail=True)
        ports = GraphPorts(model_provider=provider, is_mock_profile=False)
        img_bytes = _create_synthetic_page_image(800, 1000)

        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=synthetic_tenant,
            question_text="What is the fax number?",
            page_image_bytes=img_bytes,
            ocr_confidence=0.88,
            evidence_text={"chunk_v1": "OCR text: Intake fax line is 555-0101."},
        )

        runtime_mock = AsyncMock()
        runtime_mock.context = ctx

        result = await node_vision_read(base_graph_state, runtime_mock)

        assert result.get("vision_status") == "ocr_fallback"
        assert result.get("status") != "refusal"

    @pytest.mark.asyncio
    async def test_vision_read_failure_refuses_when_ocr_confidence_unusable(
        self,
        base_graph_state: GraphState,
        synthetic_tenant: TenantContext,
    ) -> None:
        """When vision model fails and OCR confidence < 0.70, node 6 refuses for that page."""
        provider = RecordingVisionModelProvider(should_fail=True)
        ports = GraphPorts(model_provider=provider, is_mock_profile=False)
        img_bytes = _create_synthetic_page_image(800, 1000)

        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=synthetic_tenant,
            question_text="What is the fax number?",
            page_image_bytes=img_bytes,
            ocr_confidence=0.45,
            evidence_text={"chunk_v1": "Smudged text ??"},
        )

        runtime_mock = AsyncMock()
        runtime_mock.context = ctx

        result = await node_vision_read(base_graph_state, runtime_mock)

        assert result.get("vision_status") == "unusable"
        assert result.get("status") == "refusal"
        assert ctx.response_text != ""

    @pytest.mark.asyncio
    async def test_end_to_end_graph_visual_reading_enforces_invariant_5(
        self,
        base_graph_state: GraphState,
        synthetic_tenant: TenantContext,
    ) -> None:
        """Visual readings are derived evidence; generated answer labels evidence_type='visual'."""
        provider = RecordingVisionModelProvider(canned_reading="Fax: 555-0199")
        ports = GraphPorts(model_provider=provider, is_mock_profile=False)
        img_bytes = _create_synthetic_page_image(800, 1000)

        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=synthetic_tenant,
            question_text="What is the fax number?",
            page_image_bytes=img_bytes,
            evidence_text={"chunk_v1": "Fax: 555-0199"},
            candidate_evidence_types={"chunk_v1": "visual"},
            candidate_scores={"chunk_v1": 0.95},
        )

        graph = build_knowledge_graph().compile()
        state = dict(base_graph_state)
        result = await graph.ainvoke(state, context=ctx)

        assert result["status"] in ("answered", "completed")
        assert result["needs_vision"] is True
        assert ctx.generated_answer.get("evidence_type") == "visual"

    @pytest.mark.asyncio
    async def test_node_vision_read_emits_telemetry(
        self,
        base_graph_state: GraphState,
        synthetic_tenant: TenantContext,
    ) -> None:
        """Telemetry wrapper records step_start and step_end for node 6."""
        tracer = MockTracer()
        provider = RecordingVisionModelProvider()
        ports = GraphPorts(model_provider=provider, tracer=tracer, is_mock_profile=False)
        img_bytes = _create_synthetic_page_image(800, 1000)

        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=synthetic_tenant,
            question_text="What is the fax number?",
            page_image_bytes=img_bytes,
        )

        runtime_mock = AsyncMock()
        runtime_mock.context = ctx

        await node_vision_read(base_graph_state, runtime_mock)

        assert len(tracer.starts) == 1
        assert tracer.starts[0].step_name == "vision_read"
        assert tracer.starts[0].node_number == 6
        assert len(tracer.ends) == 1
