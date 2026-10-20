"""Prompt templates and structured output schemas for LangGraph engine.

Enforces:
- Clean architecture: pure core with no external dependencies (only stdlib and pydantic).
- Structured Pydantic models for router and answer generation.
- Exact evidence passing: only question and evidence chunks with their IDs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator


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


class AnswerOutput(BaseModel):
    """Structured answer synthesis output from generation model."""

    mode: str = "short"
    answer: str = ""
    evidence_ids: str = ""
    quoted_span: str | None = None
    evidence_type: str = "text"

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


ROUTER_PROMPT_TEMPLATE: str = (
    "You are the query router for a client knowledge assistant.\n"
    "Classify the user question into one of the following routes:\n"
    "- 'knowledge': The user is asking a factual question, seeking information from documents, "
    "policies, procedures, contacts, guides, codes, fees, or tables. "
    "Default for all factual questions.\n"
    "- 'general': Greetings (e.g. 'hello', 'hi'), polite pleasantries, or general system status.\n"
    "- 'clarify': Only if the user prompt is completely ambiguous, gibberish, or a single "
    "vague word with multiple interpretations.\n\n"
    "Question: {question}\n"
    "Output JSON strictly matching the schema.\n"
)

ANSWER_PROMPT_TEMPLATE: str = (
    "You are a knowledge assistant answering strictly from the provided evidence.\n"
    "Instructions:\n"
    "1. Answer the question using ONLY the facts stated in the provided evidence passages.\n"
    "2. 'answer': concise, accurate response based solely on the evidence.\n"
    "3. 'evidence_ids': comma-separated list of passage IDs used to construct the answer "
    "(e.g. 'id1, id2').\n"
    "4. 'quoted_span': an EXACT verbatim character-for-character substring quote from one of the "
    "cited passages supporting the answer.\n"
    "5. 'evidence_type': 'text' (or 'visual' if page reading).\n"
    "6. Do not include extraneous commentary. Do not invent facts.\n\n"
    "Evidence:\n"
    "{evidence_context}\n\n"
    "Question: {question}\n"
    "Answer format: {mode}\n"
)

GENERAL_PROMPT_TEMPLATE: str = (
    "You are a helpful client knowledge assistant.\n"
    "Respond politely and concisely to the greeting or general query.\n"
    "You must NEVER state or assume any client facts, policies, or clinic data.\n"
    "Question: {question}\n"
    "Output strictly as JSON matching the schema.\n"
)


def format_router_prompt(question: str) -> str:
    """Format prompt for Node 2 query routing."""
    return ROUTER_PROMPT_TEMPLATE.format(question=question)


def format_answer_prompt(
    question: str,
    evidence_text: dict[str, str],
    mode: str = "short",
    language: str = "en",
) -> str:
    """Format prompt for Node 7 answer synthesis strictly with given evidence."""
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

    return ANSWER_PROMPT_TEMPLATE.format(
        mode=mode_description,
        language=language,
        evidence_context=evidence_context,
        question=question,
    )



def format_general_prompt(question: str) -> str:
    """Format prompt for general conversational/greeting reply."""
    return GENERAL_PROMPT_TEMPLATE.format(question=question)
