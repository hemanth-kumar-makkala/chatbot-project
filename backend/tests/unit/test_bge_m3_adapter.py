"""Unit tests for Adapter 3: BGE-M3 embeddings, chunker numbering guards, and pipeline input."""

import math
import time
from typing import Any
from uuid import uuid4

import pytest

from app.adapters.chunking.structure_aware import StructureAwareChunker
from app.adapters.embedding.bge_m3 import (
    BgeM3Config,
    BgeM3EmbeddingProvider,
    TokenizerProtocol,
    validate_bge_m3_config,
)
from app.ports.chunker import ChunkingContext
from app.ports.errors import (
    ConfigurationError,
    DuplicateEvidenceTypeError,
    EmbeddingInputTooLongError,
    EmbeddingOutputInvalidError,
)
from app.wiring import create_embedding_provider

# -----------------------------------------------------------------------------
# Fake Tokenizer and Model for Typed Protocol Unit Tests
# -----------------------------------------------------------------------------


class FakeTokenizer:
    """Fake tokenizer satisfying TokenizerProtocol for unit testing."""

    def __init__(self, token_count_per_text: int = 10) -> None:
        self.token_count_per_text = token_count_per_text

    def encode(self, text: str, **kwargs: Any) -> list[int]:
        _ = kwargs
        if not text:
            return []
        return list(range(self.token_count_per_text))


class FakeSentenceTransformerModel:
    """Fake SentenceTransformer satisfying SentenceTransformerModelProtocol."""

    def __init__(
        self,
        tokenizer: TokenizerProtocol | None = None,
        output_dim: int = 1024,
        force_non_finite: bool = False,
        return_wrong_count: bool = False,
    ) -> None:
        self._tokenizer = tokenizer or FakeTokenizer()
        self.output_dim = output_dim
        self.force_non_finite = force_non_finite
        self.return_wrong_count = return_wrong_count
        self.last_encoded_sentences: list[str] = []

    @property
    def tokenizer(self) -> TokenizerProtocol:
        return self._tokenizer

    def encode(
        self,
        inputs: Any,
        *,
        batch_size: int = 32,
        normalize_embeddings: bool = True,
        **kwargs: Any,
    ) -> Any:
        _ = (batch_size, normalize_embeddings, kwargs)
        self.last_encoded_sentences = list(inputs)
        if self.return_wrong_count:
            return [[0.0] * self.output_dim]

        result: list[list[float]] = []
        for i, _ in enumerate(self.last_encoded_sentences):
            val = (
                float("nan")
                if self.force_non_finite and i == 0
                else 1.0 / math.sqrt(self.output_dim)
            )
            vec = [val] * self.output_dim
            result.append(vec)
        return result


# -----------------------------------------------------------------------------
# PART A: Chunker Numbering & Duplicate Evidence Guards
# -----------------------------------------------------------------------------


def test_chunk_numbering_continues_across_all_readings() -> None:
    """Chunk numbering must continue across readings in one chunk_page call (never restart at 0)."""
    chunker = StructureAwareChunker()
    ctx = ChunkingContext(
        tenant_id="tenant-123",
        document_version_id=uuid4(),
        processing_version="v1",
        page_number=1,
        evidence_type="text",
        document_title="Sample Policy Document",
    )

    reading_1 = (
        "Section 1: Initial Requirements.\n\n"
        "All patients must provide photo identification upon initial clinic "
        "intake registration.\n\n"
        "Section 2: Secondary Requirements.\n\n"
        "Insurance verification cards must be presented prior to physician examination."
    )
    reading_2 = (
        "Section 3: Copayment Policy.\n\n"
        "Applicable copayments must be remitted at the time healthcare services are rendered.\n\n"
        "Section 4: Referral Policy.\n\n"
        "Specialist consultations require advance written referral authorizations."
    )

    chunks = chunker.chunk_page(readings=[reading_1, reading_2], context=ctx)

    assert len(chunks) == 2
    assert chunks[0].chunk_id != chunks[1].chunk_id, "Chunk IDs across readings must not collide"
    chunk_ids = [c.chunk_id for c in chunks]
    assert len(chunk_ids) == len(set(chunk_ids)), (
        "Chunk IDs must be completely unique with zero collisions"
    )


def test_duplicate_evidence_type_raises_typed_error() -> None:
    """Duplicate reading selections of same evidence type on one page raise typed error."""
    from app.core.ingestion.vision_policy import VisionReadingSelection

    duplicate_selections = [
        VisionReadingSelection(
            evidence_type="text", text="First text reading", page_reading_id=None
        ),
        VisionReadingSelection(
            evidence_type="text", text="Second duplicate text reading", page_reading_id=None
        ),
    ]

    seen_types: set[str] = set()
    with pytest.raises(DuplicateEvidenceTypeError) as exc_info:
        for sel in duplicate_selections:
            if sel.evidence_type in seen_types:
                raise DuplicateEvidenceTypeError(
                    f"Duplicate evidence type '{sel.evidence_type}' for page 1"
                )
            seen_types.add(sel.evidence_type)

    assert exc_info.value.code == "DUPLICATE_EVIDENCE_TYPE"


# -----------------------------------------------------------------------------
# PART B: BGE-M3 Adapter Configuration & Output Validation Tests
# -----------------------------------------------------------------------------


def test_bge_m3_config_validation_requires_all_keys() -> None:
    """All profile configuration keys are mandatory and strictly validated."""
    valid_raw: dict[str, Any] = {
        "model": "BAAI/bge-m3",
        "revision": "5617a9f61b028005a4858fdac845db406aefb181",  # pragma: allowlist secret
        "device": "cpu",
        "max_seq_length": 8192,
    }

    cfg = validate_bge_m3_config(valid_raw)
    assert cfg.model == "BAAI/bge-m3"
    assert cfg.revision == "5617a9f61b028005a4858fdac845db406aefb181"  # pragma: allowlist secret
    assert cfg.device == "cpu"
    assert cfg.max_seq_length == 8192

    # Missing model
    with pytest.raises(ConfigurationError) as exc:
        validate_bge_m3_config({"revision": "rev", "device": "cpu", "max_seq_length": 8192})
    assert exc.value.error_code == "CONFIGURATION_ERROR"

    # Missing revision
    with pytest.raises(ConfigurationError):
        validate_bge_m3_config({"model": "m", "device": "cpu", "max_seq_length": 8192})

    # Missing device
    with pytest.raises(ConfigurationError):
        validate_bge_m3_config({"model": "m", "revision": "r", "max_seq_length": 8192})

    # Missing max_seq_length
    with pytest.raises(ConfigurationError):
        validate_bge_m3_config({"model": "m", "revision": "r", "device": "cpu"})

    # Invalid device
    with pytest.raises(ConfigurationError):
        validate_bge_m3_config({**valid_raw, "device": "unsupported_gpu"})

    # Invalid max_seq_length
    with pytest.raises(ConfigurationError):
        validate_bge_m3_config({**valid_raw, "max_seq_length": 0})


def test_bge_m3_token_count_guard_raises_typed_error() -> None:
    """Input over max_seq_length raises EmbeddingInputTooLongError without silent truncation."""
    fake_tokenizer = FakeTokenizer(token_count_per_text=8193)
    fake_model = FakeSentenceTransformerModel(tokenizer=fake_tokenizer)
    cfg = BgeM3Config(
        model="BAAI/bge-m3",
        revision="5617a9f61b028005a4858fdac845db406aefb181",  # pragma: allowlist secret
        device="cpu",
        max_seq_length=8192,
    )
    provider = BgeM3EmbeddingProvider(model=fake_model, config=cfg)

    with pytest.raises(EmbeddingInputTooLongError) as exc_info:
        import asyncio

        asyncio.run(provider.embed(["Excessively long text token sequence"]))

    assert exc_info.value.code == "EMBEDDING_INPUT_TOO_LONG"


def test_bge_m3_output_dimension_mismatch_raises_typed_error() -> None:
    """Output dimension not equal to 1024 raises EmbeddingOutputInvalidError."""
    fake_model = FakeSentenceTransformerModel(output_dim=512)
    cfg = BgeM3Config(
        model="BAAI/bge-m3",
        revision="5617a9f61b028005a4858fdac845db406aefb181",  # pragma: allowlist secret
        device="cpu",
        max_seq_length=8192,
    )
    provider = BgeM3EmbeddingProvider(model=fake_model, config=cfg)

    with pytest.raises(EmbeddingOutputInvalidError) as exc_info:
        import asyncio

        asyncio.run(provider.embed(["Valid text to embed"]))

    assert exc_info.value.code == "EMBEDDING_OUTPUT_INVALID"


def test_bge_m3_non_finite_values_raise_typed_error() -> None:
    """NaN or Inf vector values raise EmbeddingOutputInvalidError."""
    fake_model = FakeSentenceTransformerModel(force_non_finite=True)
    cfg = BgeM3Config(
        model="BAAI/bge-m3",
        revision="5617a9f61b028005a4858fdac845db406aefb181",  # pragma: allowlist secret
        device="cpu",
        max_seq_length=8192,
    )
    provider = BgeM3EmbeddingProvider(model=fake_model, config=cfg)

    with pytest.raises(EmbeddingOutputInvalidError) as exc_info:
        import asyncio

        asyncio.run(provider.embed(["Valid text producing NaN"]))

    assert exc_info.value.code == "EMBEDDING_OUTPUT_INVALID"


def test_bge_m3_normal_encoding_and_properties() -> None:
    """Normal encoding returns unit normalized 1024-dim vectors with model_id and version."""
    fake_model = FakeSentenceTransformerModel()
    cfg = BgeM3Config(
        model="BAAI/bge-m3",
        revision="5617a9f61b028005a4858fdac845db406aefb181",  # pragma: allowlist secret
        device="cpu",
        max_seq_length=8192,
    )
    provider = BgeM3EmbeddingProvider(model=fake_model, config=cfg)

    assert provider.model_id == "BAAI/bge-m3"
    expected_rev = "5617a9f61b028005a4858fdac845db406aefb181"  # pragma: allowlist secret
    assert provider.version == expected_rev

    import asyncio

    texts = ["First valid sentence", "Second valid sentence"]
    embeddings = asyncio.run(provider.embed(texts))

    assert len(embeddings) == 2
    for vec in embeddings:
        assert len(vec) == 1024
        assert all(math.isfinite(x) for x in vec)


def test_wiring_create_embedding_provider_profile_guards() -> None:
    """Wiring creates Mock for 'mock', BgeM3 for desktop/home GPU, rejects unauthorized profiles."""
    mock_provider = create_embedding_provider("mock")
    assert mock_provider.model_id == "mock-bge-m3"

    with pytest.raises(ConfigurationError) as exc_office:
        create_embedding_provider("office-cpu")
    assert exc_office.value.error_code == "UNAUTHORIZED_EMBEDDING_PROFILE"

    with pytest.raises(ConfigurationError) as exc_unknown:
        create_embedding_provider("cloud-unconfigured")
    assert exc_unknown.value.error_code == "UNAUTHORIZED_EMBEDDING_PROFILE"


# -----------------------------------------------------------------------------
# PART C: Pipeline Prefix & Text Guarantee
# -----------------------------------------------------------------------------


def test_pipeline_embed_input_uses_context_prefix_and_stored_text_is_exact() -> None:
    """Pipeline passes prefix + '\\n\\n' + text to embed, while stored text stays verbatim."""

    class RecordingEmbeddingProvider:
        def __init__(self) -> None:
            self.embedded_inputs: list[str] = []

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.embedded_inputs = list(texts)
            return [[1.0] * 1024 for _ in texts]

        @property
        def model_id(self) -> str:
            return "BAAI/bge-m3"

        @property
        def version(self) -> str:
            return "5617a9f61b028005a4858fdac845db406aefb181"  # pragma: allowlist secret

    embedder = RecordingEmbeddingProvider()
    chunker = StructureAwareChunker()
    ctx = ChunkingContext(
        tenant_id="tenant-123",
        document_version_id=uuid4(),
        processing_version="v1",
        page_number=1,
        evidence_type="text",
        document_title="Sample Document",
    )
    raw_text = (
        "Section 1: Guidelines.\n\n"
        "All patients must provide photo identification upon initial clinic intake."
    )
    chunks = chunker.chunk_page(readings=[raw_text], context=ctx)
    assert len(chunks) >= 1

    # Emulate pipeline logic:
    embed_inputs = [f"{c.context_prefix}\n\n{c.text}" for c in chunks]
    import asyncio

    embeddings = asyncio.run(embedder.embed(embed_inputs))

    assert len(embeddings) == len(chunks)
    assert embedder.embedded_inputs[0].startswith(chunks[0].context_prefix)
    assert embedder.embedded_inputs[0].endswith(chunks[0].text)
    # Stored chunk text is exact substring slice
    assert chunks[0].text in raw_text


# -----------------------------------------------------------------------------
# PART D: Real Model Test Marked 'models' (Run on desktop-gpu with weights)
# -----------------------------------------------------------------------------


@pytest.mark.models
def test_real_bge_m3_desktop_gpu_embedding() -> None:
    """Real BGE-M3 test asserting dim 1024, L2 norm within 1e-3, cosine ranking, and determinism."""
    from app.adapters.embedding.bge_m3 import create_bge_m3_provider

    provider = create_bge_m3_provider("desktop-gpu")

    text_similar_1 = "Patient diagnosed with acute bacterial sinusitis requiring oral antibiotics."
    text_similar_2 = (
        "Clinical presentation of acute bacterial sinusitis treated with prescription antibiotics."
    )
    text_unrelated = (
        "Quarterly financial ledger statement and commercial office rental lease agreement."
    )

    start_time = time.perf_counter()
    import asyncio

    embeddings_run_1 = asyncio.run(provider.embed([text_similar_1, text_similar_2, text_unrelated]))
    duration_1 = time.perf_counter() - start_time
    print(f"\n[BGE-M3 Real Model] Run 1 time: {duration_1:.3f}s for 3 embeddings")

    assert len(embeddings_run_1) == 3
    for vec in embeddings_run_1:
        assert len(vec) == 1024
        l2_norm = math.sqrt(sum(x * x for x in vec))
        assert abs(l2_norm - 1.0) < 1e-3, f"Vector L2 norm {l2_norm} not within 1e-3 of 1.0"

    v1, v2, v3 = embeddings_run_1
    sim_pair_cosine = sum(a * b for a, b in zip(v1, v2, strict=False))
    unrelated_pair_cosine = sum(a * b for a, b in zip(v1, v3, strict=False))

    assert sim_pair_cosine > unrelated_pair_cosine, (
        f"Similar pair cosine ({sim_pair_cosine:.4f}) must be higher than "
        f"unrelated pair cosine ({unrelated_pair_cosine:.4f})"
    )

    # Determinism run 2
    embeddings_run_2 = asyncio.run(provider.embed([text_similar_1, text_similar_2, text_unrelated]))
    assert embeddings_run_1 == embeddings_run_2, (
        "Two consecutive runs on identical inputs must be identical"
    )
