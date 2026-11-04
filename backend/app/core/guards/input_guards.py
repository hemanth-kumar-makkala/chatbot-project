"""Input guardrails for Node 1 (guard_input) and question rate-limit constants (Task 5.1).

Enforces:
1. Question length limit (MAX_QUESTION_LENGTH = 4000).
2. Prompt-injection pattern detection on user input across 6 categories:
   - instruction_override
   - system_prompt_extraction
   - role_play_jailbreak
   - cross_tenant_probe
   - ungrounded_generation_override
   - encoded_or_spaced_evasion
3. Per-user and per-tenant question rate-limit constants and fixed message.
4. Language detection (returns 'en' while multilingual is deferred).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

# 1. Question Length Limit Constants
MAX_QUESTION_LENGTH: int = 4000
QUESTION_TOO_LONG_MESSAGE: str = (
    f"Your question exceeds the maximum allowed length of {MAX_QUESTION_LENGTH} characters. "
    "Please shorten your question and try again."
)

# 2. Prompt-Injection Refusal Message
PROMPT_INJECTION_MESSAGE: str = (
    "I can only answer questions using your clinic's validated documents and cannot "
    "override system rules or access restricted instructions."
)

# 3. Question Rate-Limit Constants (sliding window in seconds)
USER_QUESTION_RATE_LIMIT_MAX_REQUESTS: int = 30
TENANT_QUESTION_RATE_LIMIT_MAX_REQUESTS: int = 120
QUESTION_RATE_LIMIT_WINDOW_SECONDS: int = 60
QUESTION_RATE_LIMIT_MESSAGE: str = (
    "You've reached the query rate limit. Please wait a moment and try again."
)

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")

_DIRECT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(?:"
            r"(?:ignore|disregard|forget|bypass)\s+(?:all\s+|any\s+)?(?:(?:previous|prior|above|system|your|initial)\s+)+"
            r"(?:instructions|rules|prompts|directives|constraints|guidelines)"
            r"|override\s+(?:all\s+|any\s+)?(?:(?:system|your|safety|previous|prior)\s+)+"
            r"(?:instructions|rules|prompts|constraints|filters)"
            r"|do\s+not\s+follow\s+(?:your|the|any)\s+(?:(?:previous|prior|system)\s+)*(?:instructions|rules)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_extraction",
        re.compile(
            r"\b(?:"
            r"(?:reveal|show|print|display|output|dump|repeat|expose|give\s+me|what\s+is|what\s+are)\s+"
            r"(?:me\s+)?(?:your|the)\s+(?:exact\s+|full\s+|verbatim\s+|hidden\s+|initial\s+|developer\s+)*"
            r"(?:system\s+prompt|developer\s+instructions|developer\s+prompt)"
            r"|(?:reveal|show|print|display|output|dump|repeat|expose|give\s+me|what\s+is|what\s+are)\s+"
            r"(?:me\s+)?your\s+(?:exact\s+|full\s+|verbatim\s+|hidden\s+|initial\s+|developer\s+)*"
            r"(?:system\s+instructions|hidden\s+instructions|hidden\s+rules|system\s+message|initial\s+instructions)"
            r"|what\s+were\s+you\s+(?:told|instructed)\s+before\s+this"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_play_jailbreak",
        re.compile(
            r"\b(?:"
            r"(?:act\s+as|pretend\s+to\s+be|pretend\s+you\s+are|roleplay\s+as|simulate\s+being)\b"
            r"[^.?!]{0,80}\b(?:unrestricted|developer\s+mode|dan|jailbreak|no\s+safety|no\s+rules|"
            r"without\s+restrictions|unfiltered)"
            r"|you\s+are\s+now\s+dan\b"
            r"|do\s+anything\s+now\b"
            r"|enter\s+developer\s+mode\b"
            r"|(?:bypass|disable|turn\s+off)\s+(?:all\s+|your\s+)?safety\s+(?:filters|rules|guardrails)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cross_tenant_probe",
        re.compile(
            r"\b(?:"
            r"(?:other|another|all\s+other)\s+(?:client'?s?|tenant'?s?|clinic'?s?|organization'?s?)\s+"
            r"(?:data|documents|records|patients|files|information)"
            r"|(?:switch|change|override|set|forge)\s+tenant_id\b"
            r"|(?:reveal|dump|export|list|show)\s+all\s+(?:patient|tenant|client|database)\s+"
            r"(?:data|records|tables|rows)"
            r"|query\s+across\s+all\s+tenants"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ungrounded_generation_override",
        re.compile(
            r"\b(?:"
            r"(?:answer|respond|reply)\s+without\s+(?:using\s+|checking\s+|citing\s+)?(?:the\s+|any\s+)?documents"
            r"|(?:use|answer\s+from)\s+your\s+(?:own\s+)?general\s+knowledge"
            r"|(?:make\s+up|invent|fabricate|guess)\s+an?\s+answer"
            r"|bypass\s+(?:the\s+)?(?:evidence\s+gate|quote\s+validation|citation\s+check)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
)

_BASE64_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9+/]{20,}={0,2}\b")
_DOTTED_WORD_RE = re.compile(r"\b(?:[a-zA-Z][.\-_]){2,}[a-zA-Z]\b")
_SPACED_LETTERS_RE = re.compile(r"(?:\b[a-zA-Z]\s*[\s.\-_]\s*){4,}[a-zA-Z]\b")
_SPACELESS_EVASION_MARKERS: tuple[str, ...] = (
    "ignorepreviousinstructions",
    "disregardpreviousinstructions",
    "ignoreyourrules",
    "revealyoursystemprompt",
    "showyoursystemprompt",
    "switchtenantid",
    "answerwithoutdocuments",
)
_LEETSPEAK_TABLE = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
    }
)


def check_question_length(question: str) -> bool:
    """Return True when question length is within MAX_QUESTION_LENGTH."""
    return len(question) <= MAX_QUESTION_LENGTH


def _match_direct_patterns(text: str) -> str | None:
    """Return the first matched pattern category name on normalized text, or None."""
    for category, pattern in _DIRECT_INJECTION_PATTERNS:
        if pattern.search(text):
            return category
    return None


def _collapse_spaced_letters(text: str) -> str:
    """Collapse spaced, dotted, or hyphenated single-letter runs into contiguous words."""
    # First collapse dotted/hyphenated words like i.g.n.o.r.e -> ignore while preserving spaces
    dotted_collapsed = _DOTTED_WORD_RE.sub(
        lambda m: re.sub(r"[.\-_]+", "", m.group(0)),
        text,
    )

    def _replacer(match: re.Match[str]) -> str:
        raw = match.group(0)
        tokens = re.split(r"\s{2,}", raw)
        collapsed_words = [re.sub(r"[\s.\-_]+", "", tok) for tok in tokens]
        return " ".join(collapsed_words)

    return _SPACED_LETTERS_RE.sub(_replacer, dotted_collapsed)


def detect_prompt_injection(question: str) -> str | None:
    """Inspect user question for prompt-injection patterns.

    Returns the matched pattern category name (never user text) if detected, or None.
    """
    if not question or not question.strip():
        return None

    # Strip zero-width evasion characters first
    cleaned = _ZERO_WIDTH_RE.sub("", question)
    normalized_ws = re.sub(r"\s+", " ", cleaned).strip()

    # 1. Direct pattern check
    direct_match = _match_direct_patterns(normalized_ws)
    if direct_match is not None:
        return direct_match

    # 2. Spaced / dotted / hyphenated letter evasion check
    collapsed = _collapse_spaced_letters(cleaned)
    collapsed_ws = re.sub(r"\s+", " ", collapsed).strip()
    if collapsed_ws != normalized_ws:
        if _match_direct_patterns(collapsed_ws) is not None:
            return "encoded_or_spaced_evasion"
        collapsed_alnum = re.sub(r"[^a-z0-9]", "", collapsed_ws.lower())
        if any(marker in collapsed_alnum for marker in _SPACELESS_EVASION_MARKERS):
            return "encoded_or_spaced_evasion"

    # 3. Leetspeak evasion check
    leet_normalized = normalized_ws.translate(_LEETSPEAK_TABLE)
    if leet_normalized != normalized_ws and _match_direct_patterns(leet_normalized) is not None:
        return "encoded_or_spaced_evasion"

    # 4. Base64-encoded payload check
    for b64_match in _BASE64_CANDIDATE_RE.finditer(cleaned):
        token = b64_match.group(0)
        try:
            decoded_bytes = base64.b64decode(token, validate=True)
            decoded_str = decoded_bytes.decode("utf-8", errors="ignore")
        except Exception:
            continue
        if decoded_str and _match_direct_patterns(decoded_str) is not None:
            return "encoded_or_spaced_evasion"

    return None


def detect_input_language(question: str) -> str:
    """Return input language code.

    Multilingual support is deferred per docs/OPEN_QUESTIONS.md; all input is treated as 'en'.
    """
    _ = question
    return "en"


detect_supported_language = detect_input_language


@dataclass(frozen=True)
class InputGuardDecision:
    """Outcome of running Node 1 input guard checks on a user question."""

    allowed: bool
    refused: bool
    public_error_ref: str | None
    message: str | None
    matched_pattern: str | None
    language: str


def guard_question_input(question: str) -> InputGuardDecision:
    """Evaluate question length and prompt-injection rules for Node 1 (guard_input)."""
    lang = detect_input_language(question)
    if not check_question_length(question):
        return InputGuardDecision(
            allowed=False,
            refused=True,
            public_error_ref="ERR-INPUT-TOO-LONG",
            message=QUESTION_TOO_LONG_MESSAGE,
            matched_pattern=None,
            language=lang,
        )
    matched_pattern = detect_prompt_injection(question)
    if matched_pattern is not None:
        return InputGuardDecision(
            allowed=False,
            refused=True,
            public_error_ref="ERR-PROMPT-INJECTION",
            message=PROMPT_INJECTION_MESSAGE,
            matched_pattern=matched_pattern,
            language=lang,
        )
    return InputGuardDecision(
        allowed=True,
        refused=False,
        public_error_ref=None,
        message=None,
        matched_pattern=None,
        language=lang,
    )
