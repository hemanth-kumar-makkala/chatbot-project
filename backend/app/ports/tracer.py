"""Port interface for Tracer.

Records execution steps, node durations, token metrics, and typed error references
into the run_steps telemetry store.

Contract Invariant:
A tracing failure must never fail the request. If the tracer encounters a database
or network error while recording telemetry, it must fail silently or log an operational
warning. It must NEVER propagate exceptions back to the active query graph or abort user requests.
"""

from abc import ABC, abstractmethod

from app.ports.types import (
    StepEndPayload,
    StepErrorPayload,
    StepStartPayload,
)


class Tracer(ABC):
    """Abstract port for step-level execution telemetry (run_steps).

    Contract Invariant:
    A tracing failure must never fail the request. Implementations must handle internal logging
    exceptions gracefully. Callers must treat telemetry collection as fail-safe.
    """

    @abstractmethod
    async def step_start(
        self,
        payload: StepStartPayload,
    ) -> str:
        """Record beginning of an execution step and return generated step_id."""
        ...

    @abstractmethod
    async def step_end(
        self,
        step_id: str,
        payload: StepEndPayload,
    ) -> None:
        """Record successful completion of an execution step."""
        ...

    @abstractmethod
    async def step_error(
        self,
        step_id: str,
        payload: StepErrorPayload,
    ) -> None:
        """Record step failure with structured error code and error type (no raw message)."""
        ...
