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
    configured = os.getenv("NEXTRACE_DB")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".nextrace" / "traces.db"


def default_store() -> SQLiteStore:
    global _default_store
    if _default_store is None:
        _default_store = SQLiteStore(default_db_path())
    return _default_store


def current_trace(required: bool = False) -> Trace | None:
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
    turn_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    store: SQLiteStore | None = None,
) -> Trace:
    return Trace(
        application=application,
        name=name,
        trace_id=trace_id,
        session_id=session_id,
        turn_id=turn_id,
        user_id=user_id,
        tags=tags,
        metadata=metadata,
        store=store or default_store(),
        active_trace_var=_active_trace,
    )
