"""
intent_router.py — Keyword-based query intent classifier.

Classifies a user message into one of three routing buckets:
  "verbatim"  → The user wants exact data extracted from documents
                 (tables, quotes, numbers, specific fields)
  "quick_qa"  → Standard RAG question-answering against documents (default)
  "external"  → The user is asking for general world knowledge
                 beyond the uploaded document scope

KNOWN LIMITATION (V2 Backlog):
  Keyword matching is brittle. Example failure mode:
    "Explain the table on page 4" → triggers "external" due to "explain" keyword,
    but should be a document-scoped verbatim query.
  V2 Fix: Replace with a single fast Gemini Flash classification call.
"""

# ---------------------------------------------------------------------------
# Keyword lists
# ---------------------------------------------------------------------------

_VERBATIM_KEYWORDS = [
    "exact", "exactly", "verbatim", "quote", "copy", "extract",
    "table", "column", "row", "cell", "number", "figure", "invoice",
    "list all", "give me all", "what does it say", "what is written",
    "show me the", "write out",
]

_EXTERNAL_KEYWORDS = [
    "in general", "generally speaking", "what is", "who is",
    "define ", "definition of", "explain to me", "how does",
    "what are the best", "outside of", "beyond this document",
    "from your knowledge", "internet", "search for", "look up",
    "what do you know about",
]


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify(query: str) -> str:
    """
    Classify a user query into 'verbatim', 'quick_qa', or 'external'.

    Args:
        query: The raw user message string.

    Returns:
        One of: "verbatim" | "quick_qa" | "external"
    """
    q = query.lower().strip()

    # Check verbatim intent first — highest priority
    if any(kw in q for kw in _VERBATIM_KEYWORDS):
        return "verbatim"

    # Check external intent
    if any(kw in q for kw in _EXTERNAL_KEYWORDS):
        return "external"

    # Default: standard RAG question-answering
    return "quick_qa"
