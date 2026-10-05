"""In-memory rate limiter for login attempts.

Note: In-memory state is valid for local Phase 1 development on single-process servers.
Per ADR and spec, this must move to a shared store (e.g., Redis/Valkey) before multi-instance
cloud deployment in Phase 6.
"""

import time
from collections import defaultdict


class LoginRateLimiter:
    """Sliding-window in-memory rate limiter tracking failed attempts per IP or key."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = defaultdict(list)

    def is_rate_limited(self, key: str) -> bool:
        """Check if key has exceeded allowed failed attempts within the window."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        attempts = [t for t in self._failures[key] if t > cutoff]
        self._failures[key] = attempts
        return len(attempts) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        """Record a failed login attempt timestamp for key."""
        now = time.monotonic()
        self._failures[key].append(now)

    def reset(self, key: str) -> None:
        """Reset failed attempt history for key upon successful authentication."""
        self._failures.pop(key, None)
