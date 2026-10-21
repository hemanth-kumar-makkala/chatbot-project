# ruff: noqa: E501
"""Prompt templates and structured output schemas for LangGraph engine.

Enforces:
- Prompts and output schemas live in prompts/registry.json and are loaded from there.
- Clean architecture: pure core with no external dependencies (only stdlib and pydantic).
- Structured Pydantic models for router, answer generation, explain mode points, and conflict detection.
- Exact evidence passing: only question and evidence chunks with their IDs.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, field_validator

_REGISTRY_CACHE: dict[str, Any] | None = None


def set_prompt_registry(registry: dict[str, Any]) -> None:
    """Explicitly inject parsed prompt registry into core."""
    global _REGISTRY_CACHE
    _REGISTRY_CACHE = registry


def get_prompt_registry() -> dict[str, Any]:
    """Return the active prompt registry.

    Must be injected via set_prompt_registry() from wiring/adapters (e.g. from prompts/registry.yaml).
    """
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    raise RuntimeError(
        "Prompt registry has not been initialized. "
        "Load prompts/registry.yaml outside app/core and inject using set_prompt_registry()."
    )


def load_prompt_template(prompt_id: str, version: str | None = None) -> str:
    """Load prompt template string by prompt ID and version from registry."""
    registry = get_prompt_registry()
    prompts = registry.get("prompts", {})
    p_entry = prompts.get(prompt_id)
    if not p_entry:
        raise KeyError(f"Prompt ID '{prompt_id}' not found in registry")
    v = version or p_entry.get("default_version", "1.0.0")
    versions = p_entry.get("versions", {})
    v_entry = versions.get(v)
    if not v_entry or "template" not in v_entry:
        raise KeyError(f"Version '{v}' for prompt '{prompt_id}' not found in registry")
    return str(v_entry["template"])


def load_response_schema(prompt_id: str, version: str | None = None) -> dict[str, Any]:
    """Load JSON response schema by prompt ID and version from registry."""
    registry = get_prompt_registry()
    prompts = registry.get("prompts", {})
    p_entry = prompts.get(prompt_id)
    if not p_entry:
        raise KeyError(f"Prompt ID '{prompt_id}' not found in registry")
    v = version or p_entry.get("default_version", "1.0.0")
    versions = p_entry.get("versions", {})
    v_entry = versions.get(v)
    if not v_entry or "response_schema" not in v_entry:
        raise KeyError(f"Response schema for version '{v}' of prompt '{prompt_id}' not found")
    return cast(dict[str, Any], v_entry["response_schema"])


class RouterOutput(BaseModel):
    """Structured classification output from router model."""

    route: str = "knowledge"
    answer_mode: str = "short"
    language: str = "en"

    @field_validator("route", mode="before")
    @classmethod
    def coerce_route(cls, v: Any) -> str:
        s = str(v).lower().strip() if v else "knowledge"
        if s in ("knowledge", "general", "clarify", "extraction"):
            return s
        return "knowledge"

    @field_validator("answer_mode", mode="before")
    @classmethod
    def coerce_answer_mode(cls, v: Any) -> str:
        s = str(v).lower().strip() if v else "short"
        if s in ("exact", "short", "explain", "table"):
            return s
        return "short"

    @field_validator("language", mode="before")
    @classmethod
    def coerce_language(cls, v: Any) -> str:
        return str(v).strip() if v else "en"


class ExplainPoint(BaseModel):
    """Individual point in explain mode with its own quoted span and citation."""

    point: str = ""
    quoted_span: str = ""
    evidence_ids: list[str] = []

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def coerce_evidence_ids(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str):
            cleaned = v.replace("[", "").replace("]", "").replace("'", "").replace('"', "")
            parts = [p.strip() for p in cleaned.split(",") if p.strip()]
            if len(parts) == 1 and " " in parts[0]:
                parts = [p.strip() for p in parts[0].split() if p.strip()]
            return parts
        return []

    @field_validator("point", mode="before")
    @classmethod
    def coerce_point(cls, v: Any) -> str:
        return str(v).strip() if v is not None else ""

    @field_validator("quoted_span", mode="before")
    @classmethod
    def coerce_quoted_span(cls, v: Any) -> str:
        return str(v).strip() if v is not None else ""


class ConflictDetectionOutput(BaseModel):
    """Structured output for cross-document conflict detection."""

    has_conflict: bool = False
    value1: str = ""
    quote1: str = ""
    doc1_id: str = ""
    value2: str = ""
    quote2: str = ""
    doc2_id: str = ""

    @field_validator("has_conflict", mode="before")
    @classmethod
    def coerce_has_conflict(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower().strip() in ("true", "1", "yes")
        return bool(v)

    @field_validator("value1", "quote1", "doc1_id", "value2", "quote2", "doc2_id", mode="before")
    @classmethod
    def coerce_strings(cls, v: Any) -> str:
        return str(v).strip() if v is not None else ""


class AnswerOutput(BaseModel):
    """Structured answer synthesis output from generation model."""

    mode: str = "short"
    answer: str = ""
    evidence_ids: str = ""
    quoted_span: str | None = None
    evidence_type: str = "text"
    points: list[ExplainPoint] | None = None

    @field_validator("points", mode="before")
    @classmethod
    def coerce_points(cls, v: Any) -> list[ExplainPoint] | None:
        if not v:
            return None
        if isinstance(v, list):
            parsed: list[ExplainPoint] = []
            for item in v:
                if isinstance(item, ExplainPoint):
                    parsed.append(item)
                elif isinstance(item, dict):
                    parsed.append(
                        ExplainPoint(
                            point=item.get("point", ""),
                            quoted_span=item.get("quoted_span", ""),
                            evidence_ids=item.get("evidence_ids", []),
                        )
                    )
            return parsed
        return None

    @field_validator("quoted_span", mode="before")
    @classmethod
    def coerce_quoted_span(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("answer", mode="before")
    @classmethod
    def coerce_answer(cls, v: Any) -> str:
        return str(v) if v is not None else ""

    @field_validator("mode", mode="before")
    @classmethod
    def coerce_mode(cls, v: Any) -> str:
        return str(v) if v is not None else "short"

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def coerce_evidence_ids(cls, v: Any) -> str:
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return str(v) if v is not None else ""

    @classmethod
    def parse_evidence_ids(cls, val: Any) -> list[str]:
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        if isinstance(val, str):
            cleaned = val.replace("[", "").replace("]", "").replace("'", "").replace('"', "")
            parts = [p.strip() for p in cleaned.split(",") if p.strip()]
            if len(parts) == 1 and " " in parts[0]:
                parts = [p.strip() for p in parts[0].split() if p.strip()]
            return parts
        return []


def format_router_prompt(question: str) -> str:
    """Format prompt for Node 2 query routing from registry."""
    tmpl = load_prompt_template("router_classify")
    return tmpl.format(question=question)


def format_answer_prompt(
    question: str,
    evidence_text: dict[str, str],
    mode: str = "short",
    language: str = "en",
) -> str:
    """Format prompt for Node 7 answer synthesis strictly with given evidence from registry."""
    lines: list[str] = []
    for eid, text in evidence_text.items():
        lines.append(f"[{eid}]: {text}")
    evidence_context = "\n\n".join(lines) if lines else "No evidence provided."

    mode_description = mode
    if mode == "table":
        mode_description = (
            "table (Answer citing sheet and row for each row/fact, e.g. '[Sheet: Sheet1, Row: 2]', "
            "listing the exact values from the table)"
        )
    elif mode == "exact":
        mode_description = "exact (Provide the verbatim stored span or direct code/identifier from evidence with minimal framing)"
    elif mode == "explain":
        mode_description = (
            "explain (Provide a structured summary as a list of 'points'. Output must populate 'points' with objects "
            "containing 'point' (the factual sentence), 'quoted_span' (exact verbatim substring copied from evidence text), "
            "and 'evidence_ids' (the chunk ID string where the quote appears). Copy quotes exactly with no alterations.)"
        )

    tmpl = load_prompt_template("answer_synthesis")
    return tmpl.format(
        mode=mode_description,
        language=language,
        evidence_context=evidence_context,
        question=question,
    )


def format_general_prompt(question: str) -> str:
    """Format prompt for general conversational/greeting reply from registry."""
    tmpl = load_prompt_template("general_reply")
    return tmpl.format(question=question)


def format_conflict_prompt(question: str, evidence_text: dict[str, str]) -> str:
    """Format prompt for detecting conflicting evidence between documents."""
    lines: list[str] = []
    for eid, text in evidence_text.items():
        lines.append(f"[{eid}]: {text}")
    evidence_context = "\n\n".join(lines) if lines else "No evidence provided."

    tmpl = load_prompt_template("conflict_detect")
    return tmpl.format(
        question=question,
        evidence_context=evidence_context,
    )
