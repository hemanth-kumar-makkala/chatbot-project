"""Runtime guard enforcing LangSmith and third-party tracing disabled.

Protects against outbound telemetry and unauthorized tracing by ensuring
tracing variables are forced to 'false' at application startup.
"""

from __future__ import annotations

import os

TRACING_ENV_VARS: tuple[str, ...] = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_TRACING",
)


def enforce_langsmith_tracing_disabled() -> None:
    """Enforce LangSmith and LangChain tracing disabled at runtime.

    Forces LANGSMITH_TRACING, LANGCHAIN_TRACING_V2, and equivalent variables to 'false'
    regardless of external environment settings, ensuring no telemetry leaves the machine
    unless a later approved ADR explicitly overrides this invariant.
    """
    for var in TRACING_ENV_VARS:
        os.environ[var] = "false"
