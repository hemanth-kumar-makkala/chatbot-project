"""Integration tests for LangGraph PostgreSQL checkpointer (Task 1.6).

Verifies:
- AsyncPostgresSaver connects to Docker PostgreSQL and creates checkpoint tables.
- Graph state is persisted to PostgreSQL across thread invocations.
- Checkpoints stored in PostgreSQL (both state channel values AND checkpoint metadata)
  contain ZERO content/prompt/text/document strings.
"""

from typing import Any, cast
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
)
from app.db.session import get_database_url


class TestLangGraphPostgresCheckpointer:
    """Verifies Postgres checkpointer state and zero-content persistence invariant."""

    async def test_postgres_checkpointer_zero_content_persistence(self) -> None:
        db_url = get_database_url(role="cka_admin")
        # AsyncPostgresSaver uses psycopg connection pool
        conn_string = db_url.replace("postgresql+psycopg://", "postgresql://")

        async with AsyncConnectionPool(
            conn_string,
            min_size=1,
            max_size=5,
            kwargs={"autocommit": True},
        ) as pool:
            checkpointer = AsyncPostgresSaver(cast(Any, pool))
            await checkpointer.setup()

            graph = build_knowledge_graph().compile(checkpointer=checkpointer)

            question_probe = f"SyntheticQuestion_{uuid4()}"
            evidence_probe = f"SyntheticDocumentEvidence_{uuid4()}"
            answer_probe = "Generated answer grounded in evidence"

            # Sensitive text lives strictly in GraphRuntimeContext, not in GraphState
            runtime_ctx = GraphRuntimeContext(
                ports=GraphPorts(),
                question_text=question_probe,
                evidence_text={"c1": evidence_probe},
            )

            thread_id = f"thread_{uuid4()}"
            initial_state: GraphState = {
                "request_id": f"req_{uuid4()}",
                "tenant_id": str(uuid4()),
                "user_id": str(uuid4()),
                "conversation_id": str(uuid4()),
                "route": "knowledge",
                "answer_mode": "short",
                "language": "en",
                "evidence_ids": ["c1"],
                "evidence_state": "RELIABLE_TEXT",
                "needs_vision": False,
                "validation_attempts": 0,
                "status": "pending",
                "public_error_ref": None,
            }

            config = RunnableConfig(configurable={"thread_id": thread_id})
            result = await graph.ainvoke(initial_state, config=config, context=runtime_ctx)
            assert result["status"] in ("answered", "completed")
            assert result.get("response_message_id") is not None
            assert "response_payload" not in result

            # Inspect all checkpoints written to PostgreSQL for this thread
            checkpoints_found = 0
            async for checkpoint_tuple in checkpointer.alist(config):
                checkpoints_found += 1
                checkpoint = checkpoint_tuple.checkpoint
                metadata = checkpoint_tuple.metadata

                # 1. State channels check
                channel_values = checkpoint.get("channel_values", {})
                assert "response_payload" not in channel_values, (
                    "response_payload must never be stored in Postgres checkpoint channels"
                )
                for key, val in channel_values.items():
                    val_str = str(val)
                    assert question_probe not in val_str, (
                        f"Secret question leaked into PostgreSQL checkpoint channel '{key}'"
                    )
                    assert evidence_probe not in val_str, (
                        f"Secret evidence leaked into PostgreSQL checkpoint channel '{key}'"
                    )
                    assert answer_probe not in val_str, (
                        f"Answer text leaked into PostgreSQL checkpoint channel '{key}'"
                    )

                # 2. Metadata check
                meta_str = str(metadata)
                assert question_probe not in meta_str, (
                    "Secret question leaked into PostgreSQL checkpoint metadata"
                )
                assert evidence_probe not in meta_str, (
                    "Secret evidence leaked into PostgreSQL checkpoint metadata"
                )
                assert answer_probe not in meta_str, (
                    "Answer text leaked into PostgreSQL checkpoint metadata"
                )

            assert checkpoints_found > 0, "Expected at least one checkpoint recorded in PostgreSQL"
