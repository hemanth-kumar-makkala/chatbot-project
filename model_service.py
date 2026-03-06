"""
model_service.py — Framework-agnostic service for dynamic model discovery.

For each provider, attempts to fetch available models from the API using the
user's key. Falls back to a curated hardcoded list if the fetch fails.

No Streamlit imports — designed for easy migration to FastAPI.
"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fallback model lists (curated flagship models per provider)
# ---------------------------------------------------------------------------

FALLBACK_MODELS: dict[str, list[str]] = {
    "google": [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo",
        "o3-mini",
    ],
    "anthropic": [
        "claude-sonnet-4-20250514",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ],
}

# Provider display names
PROVIDER_LABELS: dict[str, str] = {
    "google":    "Google Gemini",
    "openai":    "OpenAI",
    "anthropic": "Anthropic",
}


# ---------------------------------------------------------------------------
# Dynamic model fetching per provider
# ---------------------------------------------------------------------------

def fetch_models(provider: str, api_key: str) -> list[str]:
    """
    Attempt to fetch available models from the provider API.
    Returns a list of model ID strings. Falls back to FALLBACK_MODELS on any error.

    Args:
        provider: 'google', 'openai', or 'anthropic'
        api_key:  The user's decrypted API key

    Returns:
        List of model name strings, never empty.
    """
    provider = provider.lower()
    try:
        if provider == "google":
            return _fetch_google_models(api_key)
        elif provider == "openai":
            return _fetch_openai_models(api_key)
        elif provider == "anthropic":
            return _fetch_anthropic_models(api_key)
    except Exception as exc:
        logger.warning(f"Failed to fetch models for {provider}: {exc}. Using fallback list.")

    return FALLBACK_MODELS.get(provider, [])


def validate_key(provider: str, api_key: str) -> tuple[bool, str]:
    """
    Validate an API key by attempting a lightweight API call.
    Returns (is_valid, message).
    """
    provider = provider.lower()
    try:
        models = fetch_models(provider, api_key)
        if models:
            return True, f"Key validated — {len(models)} models available."
        return False, "Key returned no models."
    except Exception as exc:
        return False, f"Validation failed: {exc}"


# ---------------------------------------------------------------------------
# Provider-specific fetchers
# ---------------------------------------------------------------------------

def _fetch_google_models(api_key: str) -> list[str]:
    """Fetch models from Google Generative AI that support generateContent."""
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    models = genai.list_models()

    chat_models = []
    for m in models:
        # Filter for models that support content generation
        if "generateContent" in (m.supported_generation_methods or []):
            chat_models.append(m.name.replace("models/", ""))

    if not chat_models:
        return FALLBACK_MODELS["google"]

    # Sort to put latest models first
    chat_models.sort(reverse=True)
    return chat_models


def _fetch_openai_models(api_key: str) -> list[str]:
    """Fetch models from OpenAI that are GPT / o-series chat models."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.models.list()

    chat_models = []
    for m in response.data:
        model_id = m.id.lower()
        # Filter for chat-capable models (GPT-3.5+, GPT-4, o-series)
        if any(prefix in model_id for prefix in ["gpt-4", "gpt-3.5", "o1", "o3", "o4-mini"]):
            # Skip internal/fine-tuned variants
            if "instruct" not in model_id and ":" not in m.id:
                chat_models.append(m.id)

    if not chat_models:
        return FALLBACK_MODELS["openai"]

    chat_models.sort(reverse=True)
    return chat_models


def _fetch_anthropic_models(api_key: str) -> list[str]:
    """Fetch models from Anthropic using their Models API."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.models.list()
        models = [m.id for m in response.data]
        if models:
            models.sort(reverse=True)
            return models
    except Exception as exc:
        logger.warning(f"Anthropic models.list() failed: {exc}. Using fallback.")

    return FALLBACK_MODELS["anthropic"]
