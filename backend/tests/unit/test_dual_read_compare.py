"""Unit tests for Task 4.4: Extraction Subgraph & Dual Read Compare.

Enforces:
- Dual read comparison across independent OCR and Vision readings using normalized values.
- Reconciles matching and conflicting readings into explicit field states.
- The extraction subgraph is invoked when Node 2 routes to extraction, reusing Node 1 and Node 9.
"""

from __future__ import annotations

import pytest

from app.core.extraction.dual_read import (
    dual_read_compare,
)
from app.core.extraction.field_state import FieldState
from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    invoke_graph,
)


class TestDualReadCompare:
    """Test reconciling dual independent readings."""

    def test_dual_read_compare_matching_values(self) -> None:
        """Readings that agree on normalized values produce FILLED_HIGH field."""
        ocr_reading = {
            "patient_name": "Alex Morgan",
            "rx_date": "2026-02-14",
            "dosage": "500mg",
        }
        vision_reading = {
            "patient_name": "Alex Morgan",
            "rx_date": "Feb 14, 2026",
            "dosage": "500 mg",
        }
        confidences = {"patient_name": 0.98, "rx_date": 0.95, "dosage": 0.96}

        fields = dual_read_compare(
            read1_ocr=ocr_reading,
            read2_vision=vision_reading,
            confidences=confidences,
            document_title="skewed_fax.pdf",
            page_number=1,
        )

        by_name = {f.name: f for f in fields}
        assert by_name["patient_name"].state == FieldState.FILLED_HIGH
        assert by_name["patient_name"].value == "Alex Morgan"

        assert by_name["rx_date"].state == FieldState.FILLED_HIGH
        assert by_name["rx_date"].value == "2026-02-14"

        assert by_name["dosage"].state == FieldState.FILLED_HIGH
        assert "500" in str(by_name["dosage"].value)

    def test_dual_read_compare_conflicting_readings(self) -> None:
        """Disagreement between readings produces CONFLICTING state with value=None."""
        ocr_reading = {"medication": "Amoxicillin 500mg"}
        vision_reading = {"medication": "Amoxillin 500mg"}
        confidences = {"medication": 0.74}

        fields = dual_read_compare(
            read1_ocr=ocr_reading,
            read2_vision=vision_reading,
            confidences=confidences,
            document_title="skewed_fax.pdf",
            page_number=1,
        )

        assert len(fields) == 1
        f = fields[0]
        assert f.state == FieldState.CONFLICTING
        assert f.value is None
        assert f.read1_value == "Amoxicillin 500mg"
        assert f.read2_value == "Amoxillin 500mg"

    def test_dual_read_compare_unreadable_field(self) -> None:
        """Unreadable field produces UNREADABLE state, value=None, never guessed (Invariant 6)."""
        ocr_reading = {"prescriber_signature": "[unreadable]"}
        vision_reading = {"prescriber_signature": None}
        confidences = {"prescriber_signature": 0.35}

        fields = dual_read_compare(
            read1_ocr=ocr_reading,
            read2_vision=vision_reading,
            confidences=confidences,
            document_title="skewed_fax.pdf",
            page_number=1,
        )

        assert len(fields) == 1
        f = fields[0]
        assert f.state == FieldState.UNREADABLE
        assert f.value is None

    def test_dual_read_compare_invalid_validator_result(self) -> None:
        """Field that fails validator produces INVALID state."""
        ocr_reading = {"npi": "1982736451"}
        vision_reading = {"npi": "1982736451"}
        confidences = {"npi": 0.95}

        fields = dual_read_compare(
            read1_ocr=ocr_reading,
            read2_vision=vision_reading,
            confidences=confidences,
            document_title="skewed_fax.pdf",
            page_number=1,
        )

        assert len(fields) == 1
        f = fields[0]
        assert f.state == FieldState.INVALID
        assert f.value == "1982736451"
        assert f.validator_error is not None


@pytest.mark.asyncio
class TestExtractionSubgraphInvocation:
    """Test the extraction subgraph end-to-end within the knowledge graph."""

    async def test_route_to_extraction_executes_subgraph_and_reaches_respond(self) -> None:
        """When Node 2 routes to extraction, it runs the extraction subgraph and ends at respond."""
        ctx = GraphRuntimeContext(
            ports=GraphPorts(is_mock_profile=True, mock_forced_route="extraction"),
            question_text="Upload patient fax and extract all fields",
            evidence_text={
                "ocr_reading": "Alex Morgan Amoxicillin 500mg 2026-02-14",
                "vision_reading": "Alex Morgan Amoxicillin 500mg 2026-02-14",
            },
            raw_document_bytes=b"%PDF-1.4FakeFaxBytes",
            page_image_bytes=b"\x89PNGFakeImageBytes",
        )
        state: GraphState = {
            "request_id": "req-ext-001",
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "route": "extraction",
        }

        final_state = await invoke_graph(state, ctx)
        assert final_state.get("status") in ("answered", "completed", "extracted")
        assert len(ctx.extracted_fields) > 0
        assert "Patient Name" in ctx.extracted_fields or "patient_name" in ctx.extracted_fields
