"""Source fidelity regression tests (Items 1 and 3).

Verifies:
1. Apex SLA: pa_turnaround_times.xlsx row 3 contains exact cell values:
   Payer Name: 'Apex Health Insurance', Service Category: 'Diagnostic Imaging',
   Urgency: 'Routine', Standard SLA: '48 Hours'.
2. Prior auth documentation checklist in prior_auth_guide.pdf specifies
   'Diagnostic imaging reports and relevant laboratory panels', and does NOT
   contain 'imaging requisition' (proving zero source mixing).
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pypdfium2  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = REPO_ROOT / "evals" / "golden" / "docs"


def test_apex_diagnostic_imaging_sla_exact_cells() -> None:
    """Verify pa_turnaround_times.xlsx has 48 Hours for Apex diagnostic imaging."""
    xlsx_path = DOCS_DIR / "pa_turnaround_times.xlsx"
    assert xlsx_path.is_file(), f"Missing {xlsx_path}"

    wb = openpyxl.load_workbook(xlsx_path)
    sheet = wb["Sheet1"]

    # Header is row 2, Apex Diagnostic Imaging is row 3
    apex_row = list(sheet.iter_rows(values_only=True))[2]
    payer, service, urgency, standard_sla, liaison = apex_row

    assert payer == "Apex Health Insurance"
    assert service == "Diagnostic Imaging"
    assert urgency == "Routine"
    assert standard_sla == "48 Hours"
    assert liaison == "555-0120"
    assert "5 days" not in str(standard_sla).lower()


def test_prior_auth_guide_source_fidelity_no_imaging_requisition() -> None:
    """Check prior auth guide wording for imaging reports, not requisitions."""
    pdf_path = DOCS_DIR / "prior_auth_guide.pdf"
    assert pdf_path.is_file(), f"Missing {pdf_path}"

    doc = pypdfium2.PdfDocument(pdf_path)
    full_text = "\n".join(page.get_textpage().get_text_range() for page in doc)

    # Must contain exact checklist items
    assert "Complete clinical consultation notes within past 30 days" in full_text
    assert "Diagnostic imaging reports and relevant laboratory panels" in full_text
    assert "Proposed CPT procedure codes and ICD-10 diagnostic indications" in full_text

    # Must not contain "imaging requisition" (which was an inaccurate test paraphrase)
    assert "imaging requisition" not in full_text.lower()


async def test_explain_mode_scopes_chunks_to_named_target_document() -> None:
    """When an explain-mode question names a specific document (e.g., 'Summarize the
    outpatient prior authorization guide'), node_generate scopes used_evidence to chunks
    from that target document so unrelated documents (e.g., front_desk_policy or faxes)
    are not mixed into the summary prompt.
    """
    from unittest.mock import MagicMock

    from app.core.graph import GraphPorts, GraphRuntimeContext, GraphState, node_generate
    from app.ports.model_provider import ModelProvider
    from app.ports.types import ModelResponse

    captured_prompts: list[str] = []

    class SpyModelProvider(ModelProvider):
        async def generate(self, role, messages, schema=None, temperature=0.0, max_tokens=None):
            _ = role, schema, temperature, max_tokens
            captured_prompts.append(messages[0].content)
            return ModelResponse(
                content=(
                    '{"answer": "- Submit 5 business days in advance.", "evidence_ids": ["c_pa_1"]}'
                ),
                model_id="spy",
                model_version="1.0",
                structured_output={
                    "answer": "- Submit 5 business days in advance.",
                    "points": [
                        {
                            "point": "Submit 5 business days in advance.",
                            "quoted_span": "at least 5 business days in advance",
                            "evidence_ids": ["c_pa_1"],
                        }
                    ],
                    "evidence_ids": ["c_pa_1"],
                },
            )

        async def read_page(self, role, image, question):
            raise NotImplementedError

        async def transcribe_page(self, role, image):
            raise NotImplementedError

        async def extract_fields(self, role, image, schema):
            raise NotImplementedError

    ctx = GraphRuntimeContext(
        ports=GraphPorts(model_provider=SpyModelProvider(), is_mock_profile=False),
        question_text="Summarize the outpatient prior authorization guide",
        evidence_text={
            "c_pa_1": (
                "[Document: prior_auth_guide.pdf, Version: 1, Page: 1, Section: Rules]\n"
                "Outpatient surgery prior authorizations must be submitted at least "
                "5 business days in advance."
            ),
            "c_fd_1": (
                "[Document: front_desk_policy_v2.pdf, Version: 2, Page: 1, Section: Intake]\n"
                "The front desk intake fax number is 555-0102."
            ),
            "c_fax_1": (
                "[Document: faint_fax_2page.pdf, Version: 1, Page: 1, Section: Cover]\n"
                "Attached: clinical consultation notes and imaging requisition."
            ),
        },
    )
    state: GraphState = {
        "request_id": "req-q4-scope",
        "route": "knowledge",
        "answer_mode": "explain",
        "language": "en",
        "evidence_ids": ["c_pa_1", "c_fd_1", "c_fax_1"],
        "status": "in_progress",
    }
    runtime = MagicMock()
    runtime.context = ctx

    await node_generate(state, runtime)

    assert len(captured_prompts) == 1
    prompt_sent = captured_prompts[0]
    assert "prior_auth_guide.pdf" in prompt_sent
    assert "front_desk_policy_v2.pdf" not in prompt_sent
    assert "faint_fax_2page.pdf" not in prompt_sent
