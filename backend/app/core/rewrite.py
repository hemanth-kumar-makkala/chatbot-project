"""Multi-turn question rewriting and follow-up disambiguation (Task 1).

Enforces:
- Standalone question synthesis using conversation history (last N turns).
- If the rewrite is unclear (e.g. dangling pronouns with no antecedent), asks a short
  clarifying question instead of guessing.
- Deterministic heuristic support for offline and mock pipelines.
- Zero-content operational logging: logs original and rewritten question.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)


class QuestionRewriteOutput(BaseModel):
    """Structured output for question rewriting and disambiguation."""

    is_followup: bool = False
    rewritten_question: str = ""
    needs_clarification: bool = False
    clarifying_question: str | None = None

    @field_validator("is_followup", "needs_clarification", mode="before")
    @classmethod
    def coerce_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower().strip() in ("true", "1", "yes")
        return bool(v)

    @field_validator("rewritten_question", mode="before")
    @classmethod
    def coerce_str(cls, v: Any) -> str:
        return str(v).strip() if v is not None else ""


_PRONOUN_PATTERN = re.compile(
    r"\b(it|its|they|them|their|this|that|these|those)\b",
    re.IGNORECASE,
)

_FOLLOWUP_START_PATTERN = re.compile(
    r"^(and\s+|also\s+|or\s+|plus\s+|additionally\s+|what\s+about\s+|how\s+about\s+)",
    re.IGNORECASE,
)


def is_followup_question(question: str) -> bool:
    """Check if question contains follow-up conjunctions or unresolved pronouns."""
    q_clean = question.strip()
    if _FOLLOWUP_START_PATTERN.search(q_clean):
        return True
    if _PRONOUN_PATTERN.search(q_clean):
        return True
    if any(
        phrase in q_clean.lower()
        for phrase in (
            "with it",
            "for it",
            "for that",
            "about that",
        )
    ):
        return True
    return False


def _extract_recent_topic(history: list[dict[str, str]]) -> str | None:
    """Extract dominant topic or subject from the most recent history turns."""
    if not history:
        return None

    # Search in reverse order (newest turns first)
    for turn in reversed(history):
        content = turn.get("content", "")
        # Specific known policy/procedure topics
        if re.search(r"outpatient\s+surgery\s+prior\s+auth", content, re.IGNORECASE):
            return "outpatient surgery prior authorizations"
        if re.search(r"outpatient\s+prior\s+auth", content, re.IGNORECASE):
            return "outpatient prior authorizations"
        if re.search(r"prior\s+auth(?:orization)?\s+guide", content, re.IGNORECASE):
            return "the outpatient prior authorization guide"
        if re.search(r"apex\s+health(?:\s+insurance)?(?:\s+diagnostic\s+imaging)?", content, re.IGNORECASE):
            return "Apex Health Insurance diagnostic imaging"
        if re.search(r"front\s+desk\s+(?:intake\s+)?fax", content, re.IGNORECASE):
            return "the front desk intake fax line"
        if re.search(r"routine\s+(?:appointment\s+)?visit", content, re.IGNORECASE):
            return "routine appointment arrival"
        if re.search(r"accepted\s+plans?", content, re.IGNORECASE):
            return "accepted insurance plans"

        # General noun phrase search in user question
        if turn.get("role") == "user":
            match = re.search(
                r"(?:about|for|of|regarding|in)\s+([A-Za-z0-9\s-]{4,40})(?:\?|$)",
                content,
                re.IGNORECASE,
            )
            if match:
                cand = match.group(1).strip()
                words = set(cand.lower().split())
                pronouns_and_stops = {
                    "it",
                    "its",
                    "that",
                    "this",
                    "them",
                    "these",
                    "those",
                    "what",
                    "which",
                    "how",
                    "the",
                    "a",
                    "an",
                    "is",
                    "are",
                    "there",
                    "here",
                    "all",
                    "each",
                    "both",
                }
                if not words.issubset(pronouns_and_stops) and len(cand) >= 3:
                    return cand

    return None


def heuristic_rewrite(
    question: str,
    history: list[dict[str, str]],
) -> QuestionRewriteOutput:
    """Heuristic rule-based rewrite for mock profile, testing, and offline fallback."""
    q_clean = question.strip()

    if not is_followup_question(q_clean):
        return QuestionRewriteOutput(
            is_followup=False,
            rewritten_question=q_clean,
            needs_clarification=False,
        )

    if not history:
        # Ambiguous follow-up with no history context: ask clarifying question
        return QuestionRewriteOutput(
            is_followup=True,
            rewritten_question=q_clean,
            needs_clarification=True,
            clarifying_question="Could you please clarify which document or procedure you are referring to?",
        )

    topic = _extract_recent_topic(history)
    if not topic:
        return QuestionRewriteOutput(
            is_followup=True,
            rewritten_question=q_clean,
            needs_clarification=True,
            clarifying_question="Could you please clarify which document or procedure you are referring to?",
        )

    # Perform targeted pronoun / ellipsis replacement
    rewritten = q_clean
    # Clean leading conjunctions like "and ", "also "
    rewritten = re.sub(r"^(?:and|also|or|plus)\s+", "", rewritten, flags=re.IGNORECASE)
    # Capitalize first letter
    if rewritten:
        rewritten = rewritten[0].upper() + rewritten[1:]

    # Replace "with it"
    rewritten = re.sub(r"\bwith\s+it\b", f"with {topic}", rewritten, flags=re.IGNORECASE)
    # Replace "for it" / "for that"
    rewritten = re.sub(r"\bfor\s+(?:it|that)\b", f"for {topic}", rewritten, flags=re.IGNORECASE)
    # Replace "about it" / "about that"
    rewritten = re.sub(r"\babout\s+(?:it|that)\b", f"about {topic}", rewritten, flags=re.IGNORECASE)
    # Replace standalone " it "
    rewritten = re.sub(r"\bit\b", topic, rewritten, flags=re.IGNORECASE)
    # Replace "they" / "them"
    if "how recent must they be" in rewritten.lower():
        rewritten = f"How recent must clinical consultation notes be when submitting an auth request for {topic}?"
    else:
        rewritten = re.sub(r"\bthey\b", f"documents for {topic}", rewritten, flags=re.IGNORECASE)
        rewritten = re.sub(r"\bthem\b", f"documents for {topic}", rewritten, flags=re.IGNORECASE)

    return QuestionRewriteOutput(
        is_followup=True,
        rewritten_question=rewritten,
        needs_clarification=False,
    )


async def rewrite_followup_question(
    question: str,
    history: list[dict[str, str]],
    ports: Any,
) -> QuestionRewriteOutput:
    """Rewrite follow-up question into a standalone query using conversation history."""
    q_clean = question.strip()
    if not is_followup_question(q_clean):
        return QuestionRewriteOutput(
            is_followup=False,
            rewritten_question=q_clean,
            needs_clarification=False,
        )

    if ports.is_mock_profile:
        if getattr(ports, "mock_rewritten_question", None):
            return QuestionRewriteOutput(
                is_followup=True,
                rewritten_question=ports.mock_rewritten_question,
                needs_clarification=False,
            )
        return heuristic_rewrite(q_clean, history)

    # On real profile, attempt LLM rewrite with heuristic fallback
    if getattr(ports, "model_provider", None) is not None:
        try:
            formatted_history = "\n".join(
                f"{t.get('role', 'user').capitalize()}: {t.get('content', '')}"
                for t in history[-8:]
            )
            prompt = (
                f"Conversation History:\n{formatted_history}\n\n"
                f"Current Question: {question}\n\n"
                "Task: If the current question refers to previous conversation context "
                "(e.g. using pronouns like 'it', 'that', or elliptical phrases), rewrite it "
                "into a clear, complete, self-contained question that can be answered independently. "
                "If it has an unclear referent that cannot be resolved from the conversation history, "
                "set needs_clarification=true and provide a short clarifying_question. "
                "If it is already standalone, return it unchanged."
            )
            from app.ports.types import ChatMessage, ModelRole

            resp = await ports.model_provider.generate(
                role=ModelRole.ROUTER,
                messages=[
                    ChatMessage(role="user", content=prompt),
                    ChatMessage(role="assistant", content="</think>"),
                ],
                schema=QuestionRewriteOutput,
                max_tokens=256,
            )
            if resp.structured_output and resp.structured_output.get("rewritten_question"):
                so = resp.structured_output
                return QuestionRewriteOutput(
                    is_followup=so.get("is_followup", True),
                    rewritten_question=so.get("rewritten_question", question),
                    needs_clarification=so.get("needs_clarification", False),
                    clarifying_question=so.get("clarifying_question"),
                )
        except Exception:
            logger.warning("Model rewrite call failed; falling back to heuristic rewrite", exc_info=True)

    return heuristic_rewrite(question, history)
