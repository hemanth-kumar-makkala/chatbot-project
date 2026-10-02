"""Port interface for JobQueue.

Provides background job scheduling and execution with strict idempotency keys
to ensure document ingestion and vision jobs are resumable and never duplicate.
"""

from abc import ABC, abstractmethod

from app.ports.types import JobStatus, TaskPayload


class JobQueue(ABC):
    """Abstract port for background task scheduling."""

    @abstractmethod
    async def enqueue(
        self,
        task: TaskPayload,
        idempotency_key: str,
    ) -> str:
        """Enqueue an asynchronous task identified by a unique idempotency key."""
        ...

    @abstractmethod
    async def status(
        self,
        idempotency_key: str,
    ) -> JobStatus:
        """Retrieve current execution status of a task by its idempotency key."""
        ...
