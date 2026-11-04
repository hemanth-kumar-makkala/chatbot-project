"""Output guardrails enforced in Node 8 (validate) before Node 9 (respond) (Task 5.1).

Enforces:
1. Every citation references an allowed evidence chunk/row belonging to the requesting tenant.
2. Answer token ceilings per mode (MAX_TOKENS_EXACT, MAX_TOKENS_SHORT, MAX_TOKENS_EXPLAIN,
   MAX_TOKENS_TABLE, MAX_TOKENS_GENERAL).
3. The general route never states client facts (phone/fax numbers, dollar amounts/copays,
   MRNs/chart IDs, policy codes, or specific clinic coverage claims).
"""

from __future__ import annotations

import logging
import re
from collections import Counter

from app.core.guards.input_guards import detect_prompt_injection

logger = logging.getLogger(__name__)

MAX_TOKENS_EXACT: int = 64
MAX_TOKENS_SHORT: int = 150
MAX_TOKENS_EXPLAIN: int = 768
MAX_TOKENS_TABLE: int = 512
MAX_TOKENS_GENERAL: int = 150

_MODE_TOKEN_CEILINGS: dict[str, int] = {
    "exact": MAX_TOKENS_EXACT,
    "short": MAX_TOKENS_SHORT,
    "explain": MAX_TOKENS_EXPLAIN,
    "table": MAX_TOKENS_TABLE,
    "general": MAX_TOKENS_GENERAL,
}

# Patterns that indicate specific client/clinic facts which must never appear on the general route
_GENERAL_ROUTE_CLIENT_FACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Phone or fax numbers (e.g. 555-0142, (555) 234-5678, 800-555-0199)
    re.compile(r"\b(?:\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}|\d{3}[\s.\-]\d{4})\b"),
    # Dollar amounts / copays / deductibles (e.g. $25, $25.00, $1,500)
    re.compile(r"\$\d+(?:,\d{3})*(?:\.\d{2})?\b"),
    # Medical Record Numbers / Chart IDs (e.g. MRN-10482, MRN 10482, CHART-991)
    re.compile(r"\b(?:MRN|CHART)[-\s#:]*\d+[A-Za-z0-9\-]*\b", re.IGNORECASE),
    # Policy / Reference / Prior-Auth codes (e.g. Policy 2026-PA-01, PA-2026-04)
    re.compile(r"\b(?:\d{4}-[A-Z]{2,}-\d+|[A-Z]{2,}-\d{4}-\d+)\b"),
    # Specific clinic / payer coverage or turnaround claims on general route
    re.compile(
        r"\b(?:"
        r"(?:medicare|medicaid|aetna|cigna|united|blue\s+cross|humana|hmo|ppo)\s+"
        r"(?:is|are)\s+(?:accepted|not\s+accepted|covered|excluded)"
        r"|prior\s+authorization\s+(?:takes|requires)\s+\d+\s+(?:hours|days|business\s+days)"
        r"|(?:clinic|front\s+desk)\s+fax\s+number\s+is"
        r")\b",
        re.IGNORECASE,
    ),
)


def verify_citations_tenant_scope(
    cited_ids: list[str],
    allowed_evidence_ids: set[str],
    evidence_tenant_ids: dict[str, str],
    requesting_tenant_id: str,
) -> bool:
    """Verify every cited ID belongs to allowed_evidence_ids and requesting_tenant_id."""
    for raw_cid in cited_ids:
        cid = str(raw_cid).strip()
        if not cid:
            continue
        if cid not in allowed_evidence_ids:
            return False
        if cid in evidence_tenant_ids:
            owner_tenant = str(evidence_tenant_ids[cid]).strip()
            if owner_tenant and owner_tenant != str(requesting_tenant_id).strip():
                return False
    return True


def check_answer_token_ceiling(answer_text: str, mode: str) -> bool:
    """Verify answer word/token count does not exceed the mode's token ceiling."""
    if not answer_text:
        return True
    ceiling = _MODE_TOKEN_CEILINGS.get(mode, MAX_TOKENS_SHORT)
    words = answer_text.split()
    return len(words) <= ceiling


def validate_general_route_no_client_facts(answer_text: str) -> bool:
    """Verify a general-route response does not state specific client or clinic facts."""
    if not answer_text or not answer_text.strip():
        return True
    return all(not pattern.search(answer_text) for pattern in _GENERAL_ROUTE_CLIENT_FACT_PATTERNS)


DOCUMENT_INJECTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "doc_rule_override",
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:your|all|previous|prior|above|all\s+other)\s+"
            r"(?:rules|instructions|lines|prompts|directives|documents)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "doc_forced_user_reply",
        re.compile(
            r"\b(?:"
            r"(?:tell|reply\s+to|inform)\s+the\s+user\s+(?:that\s+|the\s+)?"
            r"|reply\s+that\s+every\s+procedure\s+is\s+covered"
            r"|answer\s+['\"]?approved['\"]?\s+to\s+every\s+question"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "doc_system_directive_header",
        re.compile(
            r"\b(?:"
            r"system\s+override|important\s+ai\s+instruction|ai\s+instruction|"
            r"assistant\s+instruction|developer\s+override"
            r")\b",
            re.IGNORECASE,
        ),
    ),
)


def match_document_injection_rule(sentence: str) -> str | None:
    """Return the matched rule name if sentence is an embedded injection directive, or None."""
    if not sentence or not sentence.strip():
        return None
    input_rule = detect_prompt_injection(sentence)
    if input_rule is not None:
        return input_rule
    for rule_name, pattern in DOCUMENT_INJECTION_RULES:
        if pattern.search(sentence):
            return rule_name
    return None


def is_document_injection_sentence(sentence: str) -> bool:
    """Return True if a sentence or clause is an embedded prompt-injection directive."""
    return match_document_injection_rule(sentence) is not None


def sanitize_evidence_text(chunk_text: str) -> str:
    """Strip embedded prompt-injection sentences from chunk_text while preserving genuine facts.

    Logs each removal to the operational log as a count and rule name only (zero text).
    """
    if not chunk_text:
        return ""
    lines_out: list[str] = []
    removed_by_rule: Counter[str] = Counter()
    for line in chunk_text.splitlines():
        if not line.strip():
            lines_out.append(line)
            continue
        # Split line into sentences so an injected sentence on the same line as a genuine
        # sentence is removed without losing the genuine sentence.
        sentences = re.split(r"(?<=[.!?])\s+", line)
        kept: list[str] = []
        for s in sentences:
            rule_name = match_document_injection_rule(s)
            if rule_name is None:
                kept.append(s)
            else:
                removed_by_rule[rule_name] += 1
        if kept:
            lines_out.append(" ".join(kept))

    for rule_name, count in removed_by_rule.items():
        logger.warning(
            "Document injection sentence removed",
            extra={
                "node": "sanitize_evidence_text",
                "status": "sanitized",
                "error_code": rule_name,
                "error_type": "DocumentInjectionStripped",
                "counts": {"removed_sentences": count},
            },
        )
    return "\n".join(lines_out)
