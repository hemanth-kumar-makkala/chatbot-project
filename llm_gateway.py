"""
llm_gateway.py — Multi-provider LLM adapter (Adapter Pattern).

Provides a single unified interface to call Google Gemini, OpenAI, or Anthropic
with a plain text prompt (or prompt + images for vision).

Resiliency:
  All provider calls are wrapped with tenacity exponential backoff
  (2→4→8→16→32s, 5 attempts) to survive transient rate-limit errors.
  Auth errors (401) are NOT retried — they re-raise immediately.

Error Handling:
  - 401 / AuthenticationError → raises KeyError (caller shows "update your API key")
  - 429 / RateLimitError      → retried automatically, then RuntimeError if exhausted
  - All other errors          → re-raised as-is
"""

from __future__ import annotations
import logging
from PIL.Image import Image as PILImage
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, before_sleep_log

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider constants
# ---------------------------------------------------------------------------

PROVIDER_GOOGLE    = "google"
PROVIDER_OPENAI    = "openai"
PROVIDER_ANTHROPIC = "anthropic"

# Default model IDs per provider (fallback if no model specified)
_DEFAULT_MODELS = {
    PROVIDER_GOOGLE:    "gemini-2.5-flash",
    PROVIDER_OPENAI:    "gpt-4o",
    PROVIDER_ANTHROPIC: "claude-sonnet-4-20250514",
}


# ---------------------------------------------------------------------------
# Auth error — never retry these
# ---------------------------------------------------------------------------

class _AuthError(Exception):
    """Sentinel for authentication failures — excluded from retry."""
    pass


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception should be retried (not an auth error)."""
    if isinstance(exc, (_AuthError, KeyError, ValueError)):
        return False
    msg = str(exc).lower()
    if any(k in msg for k in ["401", "unauthorized", "invalid api key", "api_key_invalid"]):
        return False
    return True


# ---------------------------------------------------------------------------
# Gateway class
# ---------------------------------------------------------------------------

class LLMGateway:
    """
    Unified adapter that routes LLM calls to the correct provider SDK.

    Usage:
        gw = LLMGateway()
        answer = gw.generate(
            provider="google",
            api_key=decrypted_key,
            prompt="Summarize this invoice",
            images=[pil_image1, pil_image2],  # optional
            model="gemini-2.5-flash",         # optional override
        )
    """

    def generate(
        self,
        provider: str,
        api_key: str,
        prompt: str,
        images: list[PILImage] | None = None,
        model: str | None = None,
    ) -> str:
        """
        Call the specified LLM provider and return the text response.

        Args:
            provider: One of "google", "openai", "anthropic".
            api_key:  The user's decrypted API key.
            prompt:   The full assembled prompt string.
            images:   Optional list of PIL Image objects for vision calls.
            model:    Optional model name override. If None, uses provider default.

        Returns:
            The LLM's text response as a string.

        Raises:
            KeyError:      On 401 / invalid API key.
            RuntimeError:  On 429 / rate limit (after retry exhaustion).
            ValueError:    On unknown provider.
        """
        model_name = model or _DEFAULT_MODELS.get(provider, "")

        if provider == PROVIDER_GOOGLE:
            return self._call_google(api_key, prompt, images or [], model_name)
        elif provider == PROVIDER_OPENAI:
            return self._call_openai(api_key, prompt, images or [], model_name)
        elif provider == PROVIDER_ANTHROPIC:
            return self._call_anthropic(api_key, prompt, images or [], model_name)
        else:
            raise ValueError(
                f"Unknown provider '{provider}'. "
                f"Must be one of: {PROVIDER_GOOGLE}, {PROVIDER_OPENAI}, {PROVIDER_ANTHROPIC}"
            )

    # ------------------------------------------------------------------
    # Google Gemini
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=32),
        retry=retry_if_exception(_is_retryable),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _call_google(self, api_key: str, prompt: str, images: list, model_name: str) -> str:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        content = [prompt] + images

        try:
            response = model.generate_content(content)
            return response.text.strip()
        except Exception as exc:
            self._handle_error(exc, PROVIDER_GOOGLE)

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=32),
        retry=retry_if_exception(_is_retryable),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _call_openai(self, api_key: str, prompt: str, images: list, model_name: str) -> str:
        import base64
        import io
        from openai import OpenAI, AuthenticationError, RateLimitError

        client = OpenAI(api_key=api_key)

        messages_content = [{"type": "text", "text": prompt}]

        # Encode PIL images as base64 for the OpenAI vision API
        for img in images:
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            messages_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })

        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": messages_content}],
            )
            return resp.choices[0].message.content.strip()
        except AuthenticationError as exc:
            raise KeyError("OpenAI API key is invalid or revoked.") from exc
        except RateLimitError as exc:
            raise RuntimeError("OpenAI rate limit reached. Please wait and retry.") from exc
        except Exception as exc:
            self._handle_error(exc, PROVIDER_OPENAI)

    # ------------------------------------------------------------------
    # Anthropic Claude
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=32),
        retry=retry_if_exception(_is_retryable),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _call_anthropic(self, api_key: str, prompt: str, images: list, model_name: str) -> str:
        import base64
        import io
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        content = [{"type": "text", "text": prompt}]

        # Encode PIL images for Anthropic's vision format
        for img in images:
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64,
                },
            })

        try:
            resp = client.messages.create(
                model=model_name,
                max_tokens=2048,
                messages=[{"role": "user", "content": content}],
            )
            return resp.content[0].text.strip()
        except anthropic.AuthenticationError as exc:
            raise KeyError("Anthropic API key is invalid or revoked.") from exc
        except anthropic.RateLimitError as exc:
            raise RuntimeError("Anthropic rate limit reached. Please wait and retry.") from exc
        except Exception as exc:
            self._handle_error(exc, PROVIDER_ANTHROPIC)

    # ------------------------------------------------------------------
    # Generic error handler
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_error(exc: Exception, provider: str) -> None:
        """Convert common SDK errors into our standard KeyError/RuntimeError contract."""
        msg = str(exc).lower()
        if any(k in msg for k in ["401", "unauthorized", "invalid api key", "api_key_invalid"]):
            raise KeyError(
                f"{provider.capitalize()} API key is invalid or revoked. "
                "Please update it in Settings."
            ) from exc
        if any(k in msg for k in ["429", "rate limit", "quota", "resource_exhausted"]):
            raise RuntimeError(
                f"{provider.capitalize()} rate limit hit. Please wait a moment and try again."
            ) from exc
        raise exc  # re-raise anything unexpected
