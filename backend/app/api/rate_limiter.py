"""Rate limiters for login attempts and per-user/per-tenant question queries.

Note: LoginRateLimiter uses in-memory state for local single-process login throttling.
Question rate limiting (check_question_rate_limit) queries PostgreSQL audit_events so
per-user and per-tenant limits persist across API restarts and multiple worker processes.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.guards.input_guards import (
    QUESTION_RATE_LIMIT_MESSAGE,
    QUESTION_RATE_LIMIT_WINDOW_SECONDS,
    TENANT_QUESTION_RATE_LIMIT_MAX_REQUESTS,
    USER_QUESTION_RATE_LIMIT_MAX_REQUESTS,
)
from app.db.models import AuditEvent


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


async def check_question_rate_limit(
    db_session: Any,
    tenant_uuid: UUID,
    user_uuid: UUID,
    user_max_requests: int = USER_QUESTION_RATE_LIMIT_MAX_REQUESTS,
    tenant_max_requests: int = TENANT_QUESTION_RATE_LIMIT_MAX_REQUESTS,
    window_seconds: int = QUESTION_RATE_LIMIT_WINDOW_SECONDS,
) -> None:
    """Enforce sliding-window per-user and per-tenant question rate limits via PostgreSQL.

    Queries the append-only audit_events table (where event_type == 'question') so limits
    survive process restarts and are shared across multiple API processes.
    Raises HTTP 429 with QUESTION_RATE_LIMIT_MESSAGE when either ceiling is reached.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)

    user_count_stmt = select(func.count(AuditEvent.id)).where(
        AuditEvent.tenant_id == tenant_uuid,
        AuditEvent.user_id == user_uuid,
        AuditEvent.event_type == "question",
        AuditEvent.created_at >= cutoff,
    )
    user_count = (await db_session.execute(user_count_stmt)).scalar() or 0
    if user_count >= user_max_requests:
        raise HTTPException(status_code=429, detail=QUESTION_RATE_LIMIT_MESSAGE)

    tenant_count_stmt = select(func.count(AuditEvent.id)).where(
        AuditEvent.tenant_id == tenant_uuid,
        AuditEvent.event_type == "question",
        AuditEvent.created_at >= cutoff,
    )
    tenant_count = (await db_session.execute(tenant_count_stmt)).scalar() or 0
    if tenant_count >= tenant_max_requests:
        raise HTTPException(status_code=429, detail=QUESTION_RATE_LIMIT_MESSAGE)
