"""Unit tests for Cloud Model Safety Lock and Vision Capability Detection.

Enforces:
1. SAFETY LOCK: A cloud model may only be used when every active document
   in the assistant is from evals/golden/docs (synthetic). If any other
   document is active (for example Work_Tracker_FINAL.xlsx), refuse to start
   with a clear error naming the file.
2. VISION CAPABILITY: Detect whether a model supports multimodal vision.
   If a non-vision model is used, skip vision-only steps and fallback to OCR evidence.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.api.config import ApiSettings
from app.api.routes import questions as questions_route
from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_assess_evidence,
    node_guard_input,
)
from app.core.safety_lock import (
    GOLDEN_SYNTHETIC_DOCS,
    GOLDEN_SYNTHETIC_HASHES_BY_NAME,
    CloudModelSafetyLockError,
    is_cloud_model,
    is_vision_model,
    resolve_model_capabilities,
    verify_cloud_model_safety_lock,
)
from app.ports.errors import ConfigurationError
from app.ports.model_provider import ModelCapabilities
from app.ports.storage import StorageKey
from app.ports.types import TenantContext, UserRole

TEST_CAPS: dict[tuple[str, str], ModelCapabilities] = {
    ("beam", "beam:cloud"): ModelCapabilities(
        supports_vision=False, remote=True, baa_covered=False
    ),
    ("ollama", "qwen2.5:72b-cloud"): ModelCapabilities(
        supports_vision=False, remote=True, baa_covered=False
    ),
    ("ollama", "qwen3-vl:4b"): ModelCapabilities(
        supports_vision=True, remote=False, baa_covered=False
    ),
    ("ollama", "qwen2-vl:7b"): ModelCapabilities(
        supports_vision=True, remote=False, baa_covered=False
    ),
    ("ollama", "llama3.3:70b"): ModelCapabilities(
        supports_vision=False, remote=False, baa_covered=False
    ),
    ("local", "bge-m3"): ModelCapabilities(supports_vision=False, remote=False, baa_covered=False),
    ("gemini", "gemini-3.5-flash"): ModelCapabilities(
        supports_vision=True, remote=True, baa_covered=False
    ),
    ("gemini", "gemini-vision-cloud"): ModelCapabilities(
        supports_vision=True, remote=True, baa_covered=False
    ),
    ("mock", "mock-answer-v1"): ModelCapabilities(
        supports_vision=True, remote=False, baa_covered=False
    ),
}


def test_is_cloud_model_detection() -> None:
    """Verifies that cloud model capabilities are accurately identified from capabilities map."""
    assert is_cloud_model("beam:cloud", capabilities_map=TEST_CAPS) is True
    assert is_cloud_model("qwen2.5:72b-cloud", capabilities_map=TEST_CAPS) is True
    assert is_cloud_model("qwen3-vl:4b", capabilities_map=TEST_CAPS) is False
    assert is_cloud_model("bge-m3", capabilities_map=TEST_CAPS) is False
    assert is_cloud_model("", capabilities_map=TEST_CAPS) is False
    assert is_cloud_model(None, capabilities_map=TEST_CAPS) is False
    assert is_cloud_model("local-model", is_cloud_override=True, capabilities_map=TEST_CAPS) is True

    # Fail-closed check: undeclared model raises ConfigurationError
    with pytest.raises(ConfigurationError) as exc_info:
        is_cloud_model("unknown/undeclared-model", capabilities_map=TEST_CAPS)
    assert exc_info.value.error_code == "MODEL_CAPABILITIES_NOT_DECLARED"


def test_is_vision_model_detection() -> None:
    """Verifies that multimodal vision capabilities are detected from capabilities map."""
    assert is_vision_model("qwen3-vl:4b", capabilities_map=TEST_CAPS) is True
    assert is_vision_model("qwen2-vl:7b", capabilities_map=TEST_CAPS) is True
    assert is_vision_model("gemini-3.5-flash", capabilities_map=TEST_CAPS) is True
    assert is_vision_model("beam:cloud", capabilities_map=TEST_CAPS) is False
    assert is_vision_model("llama3.3:70b", capabilities_map=TEST_CAPS) is False
    assert is_vision_model("", capabilities_map=TEST_CAPS) is False
    assert is_vision_model(None, capabilities_map=TEST_CAPS) is False

    # Fail-closed check: undeclared model raises ConfigurationError
    with pytest.raises(ConfigurationError) as exc_info:
        is_vision_model("unknown/undeclared-model", capabilities_map=TEST_CAPS)
    assert exc_info.value.error_code == "MODEL_CAPABILITIES_NOT_DECLARED"


def test_safety_lock_allows_golden_synthetic_documents() -> None:
    """Cloud model succeeds when all active documents are from evals/golden/docs."""
    golden_docs = [
        "prior_auth_guide.pdf",
        "accepted_plans.csv",
        "pa_turnaround_times.xlsx",
        "front_desk_policy_v1.pdf",
        "front_desk_policy_v2.pdf",
    ]
    # Suffix check against GOLDEN_SYNTHETIC_DOCS
    for doc in golden_docs:
        assert doc in GOLDEN_SYNTHETIC_DOCS

    # Must not raise
    verify_cloud_model_safety_lock(
        model_name="beam:cloud",
        active_document_titles=golden_docs,
        active_document_hashes=[
            GOLDEN_SYNTHETIC_HASHES_BY_NAME[Path(doc).name.lower()] for doc in golden_docs
        ],
        capabilities_map=TEST_CAPS,
    )


def test_safety_lock_allows_non_golden_with_local_model() -> None:
    """Local models are never gated by the cloud safety lock."""
    non_golden_docs = ["Work_Tracker_FINAL.xlsx", "internal_salaries.pdf"]
    verify_cloud_model_safety_lock(
        model_name="qwen3-vl:4b",
        active_document_titles=non_golden_docs,
        capabilities_map=TEST_CAPS,
    )


def test_safety_lock_refuses_non_golden_document() -> None:
    """Cloud model refuses to start and names the non-synthetic file."""
    active_docs = [
        "prior_auth_guide.pdf",
        "Work_Tracker_FINAL.xlsx",
    ]
    with pytest.raises(CloudModelSafetyLockError) as exc_info:
        verify_cloud_model_safety_lock(
            model_name="beam:cloud",
            active_document_titles=active_docs,
            active_document_hashes=[
                GOLDEN_SYNTHETIC_HASHES_BY_NAME["prior_auth_guide.pdf"],
                sha256(b"non-golden document").hexdigest(),
            ],
            capabilities_map=TEST_CAPS,
        )

    err_msg = str(exc_info.value)
    assert "SAFETY LOCK REFUSAL" in err_msg
    assert "Work_Tracker_FINAL.xlsx" in err_msg
    assert "beam:cloud" in err_msg
    assert exc_info.value.filename == "Work_Tracker_FINAL.xlsx"
    assert exc_info.value.model_name == "beam:cloud"


def test_safety_lock_rejects_non_golden_bytes_even_when_renamed_like_golden() -> None:
    renamed_title = "prior_auth_guide.pdf"
    forged_file_hash = sha256(b"renamed real document content").hexdigest()

    with pytest.raises(CloudModelSafetyLockError):
        verify_cloud_model_safety_lock(
            model_name="beam:cloud",
            active_document_titles=[renamed_title],
            active_document_hashes=[forged_file_hash],
            capabilities_map=TEST_CAPS,
        )


def test_safety_lock_handles_paths_and_casing() -> None:
    """Handles directory prefixes and uppercase filenames gracefully."""
    active_docs = [
        "C:\\Users\\admin\\evals\\golden\\docs\\PRIOR_AUTH_GUIDE.PDF",
        "/data/uploads/ACCEPTED_PLANS.CSV",
    ]
    verify_cloud_model_safety_lock(
        model_name="beam:cloud",
        active_document_titles=active_docs,
        active_document_hashes=[
            GOLDEN_SYNTHETIC_HASHES_BY_NAME["prior_auth_guide.pdf"],
            GOLDEN_SYNTHETIC_HASHES_BY_NAME["accepted_plans.csv"],
        ],
        capabilities_map=TEST_CAPS,
    )

    bad_docs = [
        "/data/uploads/Patient_PHI_2026.pdf",
    ]
    with pytest.raises(CloudModelSafetyLockError) as exc_info:
        verify_cloud_model_safety_lock(
            model_name="beam:cloud",
            active_document_titles=bad_docs,
            capabilities_map=TEST_CAPS,
        )
    assert "Patient_PHI_2026.pdf" in str(exc_info.value)


@pytest.mark.asyncio
async def test_cloud_lock_hashes_original_bytes_from_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StubStorage:
        async def get(self, key: StorageKey) -> bytes:
            assert key.path == "documents/version-1/original.pdf"
            return b"stored synthetic bytes"

    monkeypatch.setattr(
        questions_route,
        "create_storage_backend",
        lambda **_kwargs: StubStorage(),
    )
    settings = ApiSettings(profile="mock", storage_dir=str(tmp_path))

    hashes = await questions_route._hash_active_stored_documents(
        settings,
        UUID("00000000-0000-0000-0000-000000000001"),
        [("renamed.pdf", "version-1", "pdf")],
        "beam:cloud",
    )

    assert hashes == [sha256(b"stored synthetic bytes").hexdigest()]


@pytest.mark.asyncio
async def test_verify_active_documents_fails_closed_when_capabilities_map_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_verify_active_documents_for_cloud_models fails closed when
    capabilities map is missing/empty.
    """
    empty_ports = GraphPorts(capabilities_map={})
    monkeypatch.setattr(questions_route, "get_engine_ports", lambda _settings: empty_ports)

    settings = ApiSettings(
        profile="mock",
        chat_model="gemini:gemini-3.5-flash",
        storage_dir=str(tmp_path),
    )

    with pytest.raises(ConfigurationError) as exc_info:
        await questions_route._verify_active_documents_for_cloud_models(
            settings,
            UUID("00000000-0000-0000-0000-000000000001"),
            [("doc.pdf", "version-1", "pdf")],
        )

    assert exc_info.value.error_code == "MODEL_CAPABILITIES_NOT_DECLARED"


@pytest.mark.asyncio
async def test_node_guard_enforces_cloud_safety_for_vision_model() -> None:
    ctx = GraphRuntimeContext(
        ports=GraphPorts(
            is_mock_profile=True,
            vision_model="gemini:gemini-vision-cloud",
            capabilities_map=TEST_CAPS,
        ),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="session-vision",
        ),
        question_text="Read this document image",
        active_document_titles=["non-golden.pdf"],
    )
    runtime = MagicMock()
    runtime.context = ctx

    result = await node_guard_input(
        {"request_id": "req-vision", "tenant_id": "tenant", "status": "pending"},
        runtime,
    )

    assert result["status"] == "refusal"
    assert result["public_error_ref"] == "ERR-CLOUD-SAFETY-LOCK"
    assert "non-golden.pdf" in ctx.response_text


@pytest.mark.asyncio
async def test_node_guard_input_enforces_cloud_safety_lock() -> None:
    """node_guard_input halts graph execution with ERR-CLOUD-SAFETY-LOCK
    when non-golden file is active.
    """
    ctx = GraphRuntimeContext(
        ports=GraphPorts(
            is_mock_profile=True,
            chat_model="beam:cloud",
            capabilities_map=TEST_CAPS,
        ),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="session-1",
        ),
        question_text="What are the turnaround times?",
        chat_model="beam:cloud",
        active_document_titles=["Work_Tracker_FINAL.xlsx"],
    )
    runtime = MagicMock()
    runtime.context = ctx

    state: GraphState = {
        "request_id": "req-1",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "language": "en",
        "status": "pending",
    }

    result = await node_guard_input(state, runtime)

    assert result["status"] == "refusal"
    assert result["public_error_ref"] == "ERR-CLOUD-SAFETY-LOCK"
    assert "Work_Tracker_FINAL.xlsx" in ctx.response_text
    assert "SAFETY LOCK REFUSAL" in ctx.response_text


@pytest.mark.asyncio
async def test_assess_evidence_uses_independent_vision_model() -> None:
    ctx = GraphRuntimeContext(
        ports=GraphPorts(
            is_mock_profile=True,
            chat_model="beam:cloud",
            vision_model="qwen3-vl:4b",
            mock_evidence_state="VISUAL_REQUIRED",
            capabilities_map=TEST_CAPS,
        ),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="vision-selection",
        ),
        question_text="Read the form",
        chat_model="beam:cloud",
    )
    runtime = MagicMock()
    runtime.context = ctx

    result = await node_assess_evidence(
        {"request_id": "req-vision-selection", "status": "in_progress", "evidence_ids": []},
        runtime,
    )

    assert result["needs_vision"] is True
    assert result["evidence_state"] == "VISUAL_REQUIRED"


@pytest.mark.asyncio
async def test_node_assess_evidence_skips_vision_for_non_vision_cloud_model() -> None:
    """When a non-vision model is used and visual evidence is requested,
    assess_evidence skips vision read.
    """
    ctx = GraphRuntimeContext(
        ports=GraphPorts(
            is_mock_profile=True,
            chat_model="beam:cloud",
            vision_model="beam:cloud",
            mock_evidence_state="VISUAL_REQUIRED",
            capabilities_map=TEST_CAPS,
        ),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="session-1",
        ),
        question_text="What medication is authorized on this fax?",
        chat_model="beam:cloud",
        active_document_titles=["skewed_fax.pdf"],
    )
    runtime = MagicMock()
    runtime.context = ctx

    state: GraphState = {
        "request_id": "req-2",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "language": "en",
        "status": "in_progress",
        "evidence_ids": ["c1"],
    }

    result = await node_assess_evidence(state, runtime)

    # Since beam:cloud has no vision, needs_vision must be False
    # and evidence_state falls back to RELIABLE_TEXT
    assert result["needs_vision"] is False
    assert result["evidence_state"] == "RELIABLE_TEXT"


def test_empty_capabilities_map_raises_model_capabilities_not_declared() -> None:
    """Empty capabilities map raises MODEL_CAPABILITIES_NOT_DECLARED fail-closed."""
    empty_caps: dict[tuple[str, str], ModelCapabilities] = {}
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_model_capabilities("qwen3-vl:4b", capabilities_map=empty_caps)
    assert exc_info.value.error_code == "MODEL_CAPABILITIES_NOT_DECLARED"

    with pytest.raises(ConfigurationError) as exc_info:
        is_cloud_model("qwen3-vl:4b", capabilities_map=empty_caps)
    assert exc_info.value.error_code == "MODEL_CAPABILITIES_NOT_DECLARED"

    with pytest.raises(ConfigurationError) as exc_info:
        is_vision_model("qwen3-vl:4b", capabilities_map=empty_caps)
    assert exc_info.value.error_code == "MODEL_CAPABILITIES_NOT_DECLARED"


@pytest.mark.asyncio
async def test_extract_fields_from_document_file_missing_tenant_raises_typed_error(
    tmp_path: Path,
) -> None:
    """Missing tenant context raises typed AuthorizationError (403) before reading file."""
    from app.api.routes.conversations import extract_fields_from_document_file
    from app.ports.errors import AuthorizationError

    dummy_file = tmp_path / "dummy.pdf"
    with pytest.raises(AuthorizationError) as exc_info:
        await extract_fields_from_document_file(
            file_path=dummy_file,
            file_name="dummy.pdf",
            tenant_ctx=None,
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "TENANT_CONTEXT_REQUIRED"
