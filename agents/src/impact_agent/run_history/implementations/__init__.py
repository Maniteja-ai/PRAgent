"""Run history persistence implementations."""

from impact_agent.run_history.implementations.sqlite_run_history_store import (
    SQLiteRunHistoryStore,
)

__all__ = ["SQLiteRunHistoryStore"]
