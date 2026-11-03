"""Unit tests for reranker model offline loading, pinned revision,
and question payload validation.
"""

from uuid import UUID

import pytest

from app.adapters.reranking.bge_reranker import (
    BgeRerankerConfig,
    load_bge_reranker_model,
    validate_bge_reranker_config,
)
from app.api.routes.questions import QuestionRequest
from app.ports.errors import ConfigurationError
from app.wiring import create_reranker

PINNED_RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"  # pragma: allowlist secret


def test_bge_reranker_config_requires_revision() -> None:
    """Config without revision raises ConfigurationError."""
    raw = {
        "model": "BAAI/bge-reranker-v2-m3",
        "device": "cpu",
    }
    with pytest.raises(ConfigurationError) as exc_info:
        validate_bge_reranker_config(raw)
    assert "revision" in str(exc_info.value).lower()
    assert exc_info.value.error_code == "CONFIGURATION_ERROR"


def test_bge_reranker_config_valid_with_revision() -> None:
    """Config with valid revision passes validation."""
    raw = {
        "model": "BAAI/bge-reranker-v2-m3",
        "revision": PINNED_RERANKER_REVISION,
        "device": "cpu",
    }
    cfg = validate_bge_reranker_config(raw)
    assert cfg.model == "BAAI/bge-reranker-v2-m3"
    assert cfg.revision == PINNED_RERANKER_REVISION
    assert cfg.device == "cpu"


def test_create_reranker_wiring_profile_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reranker is wired only for desktop-gpu and home-gpu;
    other real profiles raise ConfigurationError.
    """
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    # Mock works
    mock_reranker = create_reranker("mock")
    assert mock_reranker is not None

    # office-cpu and cloud must raise ConfigurationError
    for prof in ("office-cpu", "cloud", "dev-free-api"):
        with pytest.raises(ConfigurationError) as exc_info:
            create_reranker(prof)
        assert exc_info.value.error_code in ("UNAUTHORIZED_RERANKER_PROFILE", "CONFIGURATION_ERROR")


def test_create_reranker_refuses_without_hf_hub_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """desktop-gpu and home-gpu raise ConfigurationError if HF_HUB_OFFLINE != '1'."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    for prof in ("desktop-gpu", "home-gpu"):
        with pytest.raises(ConfigurationError) as exc_info:
            create_reranker(prof)
        assert exc_info.value.error_code == "HF_HUB_OFFLINE_REQUIRED"


def test_snapshot_mismatch_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wrong revision hash raises ConfigurationError at startup."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    cfg = BgeRerankerConfig(
        model="BAAI/bge-reranker-v2-m3",
        revision="0000000000000000000000000000000000000000",
        device="cpu",
    )
    with pytest.raises(ConfigurationError) as exc_info:
        load_bge_reranker_model(cfg)
    assert exc_info.value.error_code == "CONFIGURATION_ERROR"


@pytest.mark.models
def test_real_bge_reranker_loads_and_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real model in HF_HOME loads strictly offline and scores pairs correctly."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    cfg = BgeRerankerConfig(
        model="BAAI/bge-reranker-v2-m3",
        revision=PINNED_RERANKER_REVISION,
        device="cpu",
    )
    model = load_bge_reranker_model(cfg)
    assert model is not None

    # Predict score for query and passage
    pairs = [
        ["prior auth fax number", "The prior authorization department fax number is 555-0199."],
        ["prior auth fax number", "Apples and oranges are common fruits in the market."],
    ]
    scores = model.predict(pairs)
    assert len(scores) == 2
    # Relevant passage must score higher than irrelevant passage
    assert float(scores[0]) > float(scores[1])


def test_question_request_conversation_id_uuid_validation() -> None:
    """QuestionRequest validates conversation_id as a UUID; invalid values raise ValueError."""
    # Valid None
    req1 = QuestionRequest(assistant_id="asst-1", question_text="hello")
    assert req1.conversation_id is None

    # Valid UUID string
    valid_uuid_str = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    valid_uuid = UUID(valid_uuid_str)
    req2 = QuestionRequest(
        assistant_id="asst-1",
        question_text="hello",
        conversation_id=valid_uuid,
    )
    assert req2.conversation_id == valid_uuid

    # Invalid UUID string raises ValidationError
    with pytest.raises(Exception) as exc_info:
        QuestionRequest.model_validate(
            {
                "assistant_id": "asst-1",
                "question_text": "hello",
                "conversation_id": "not-a-valid-uuid",
            }
        )
    assert "INVALID_CONVERSATION_ID" in str(exc_info.value) or "uuid" in str(exc_info.value).lower()
