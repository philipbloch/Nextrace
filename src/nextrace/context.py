"""Context helpers for active AI traces."""

from __future__ import annotations

import contextvars
import os
from pathlib import Path
from typing import Any

from nextrace.core import Trace
from nextrace.storage import SQLiteStore

_active_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "nextrace_active_trace", default=None
)
_default_store: SQLiteStore | None = None


def default_db_path() -> Path:
    """Return Nextrace's default SQLite path."""
    configured = os.getenv("NEXTRACE_DB")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".nextrace" / "traces.db"


def default_store() -> SQLiteStore:
    """Return the process-wide default SQLite store."""
    global _default_store
    if _default_store is None:
        _default_store = SQLiteStore(default_db_path())
    return _default_store


def current_trace(required: bool = False) -> Trace | None:
    """Return the currently active trace, if any."""
    trace = _active_trace.get()
    if required and trace is None:
        from nextrace.core import TraceContextError

        raise TraceContextError("No active ai_trace context is available.")
    return trace


def ai_trace(
    application: str,
    *,
    name: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    store: SQLiteStore | None = None,
) -> Trace:
    """Create a trace context manager for an AI workflow.

    Parameters are intentionally provider-neutral so the same trace can contain
    LLM calls, retrieval, tools, handoffs, and feedback.
    """
    return Trace(
        application=application,
        name=name,
        trace_id=trace_id,
        session_id=session_id,
        user_id=user_id,
        tags=tags,
        metadata=metadata,
        store=store or default_store(),
        active_trace_var=_active_trace,
    )
