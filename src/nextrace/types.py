"""Shared data records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class TraceRecord:
    id: str
    session_id: str
    application: str
    name: str
    user_id: str | None
    started_at: float
    ended_at: float | None
    duration_ms: float
    status: str
    error: str | None
    tags: list[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class SpanRecord:
    id: str
    trace_id: str
    parent_id: str | None
    kind: str
    name: str
    provider: str | None
    model: str | None
    started_at: float
    ended_at: float
    duration_ms: float
    prompt: Any | None
    response: Any | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    retry_count: int
    status: str
    error: str | None
    metadata: JsonDict = field(default_factory=dict)

