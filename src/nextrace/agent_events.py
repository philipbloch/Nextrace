from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from nextrace.storage import SQLiteStore
from nextrace.types import SpanRecord, TraceRecord
from nextrace.usage import stable_id


class AgentRecording:
    def __init__(self, source: str, session_id: str, application: str, file_path: str):
        self.source = source
        self.session_id = session_id
        self.application = application
        self.file_path = file_path
        self.turns: dict[str, TraceRecord] = {}
        self.spans: dict[str, SpanRecord] = {}
        self.turn_id: str | None = None
        self.pending: dict[str, str] = {}
        self.cwd: str | None = None

    def begin(self, turn_id: str, timestamp: float | None) -> None:
        if self.turn_id and self.turn_id != turn_id and timestamp is not None:
            previous_id = stable_id(self.source, self.session_id, self.turn_id)
            previous = self.turns.get(previous_id)
            if previous and previous.ended_at is None:
                self.turns[previous_id] = replace(
                    previous,
                    status="interrupted",
                    metadata={
                        **previous.metadata,
                        "next_turn_started_at": timestamp,
                        "interruption_reason": "Next turn observed without a completion event",
                    },
                )
        self.turn_id = turn_id
        if timestamp is not None:
            self._create_turn(
                turn_id, timestamp, boundary_known=not turn_id.startswith("unattributed-")
            )

    def _create_turn(self, turn_id: str, timestamp: float, *, boundary_known: bool) -> TraceRecord:
        trace_id = stable_id(self.source, self.session_id, turn_id)
        existing = self.turns.get(trace_id)
        if existing is not None:
            return existing
        trace = TraceRecord(
            id=trace_id,
            session_id=self.session_id,
            turn_id=turn_id,
            application=self.application,
            name=f"{self.source}:turn:{turn_id}",
            user_id=None,
            started_at=timestamp,
            ended_at=None,
            duration_ms=0,
            status="running",
            error=None,
            tags=[self.source, "agent-turn"],
            metadata={
                "source": self.source,
                "import_file": self.file_path,
                "cwd": str(Path(self.cwd).resolve()) if self.cwd else None,
                "turn_boundary_known": boundary_known,
                "last_observed_at": timestamp,
            },
        )
        self.turns[trace_id] = trace
        return trace

    def current(self, timestamp: float, fallback: str) -> TraceRecord:
        self.turn_id = self.turn_id or f"unattributed-{fallback}"
        return self._create_turn(self.turn_id, timestamp, boundary_known=False)

    def observe(self, trace_id: str, timestamp: float) -> None:
        trace = self.turns[trace_id]
        self.turns[trace_id] = replace(
            trace,
            metadata={
                **trace.metadata,
                "cwd": str(Path(self.cwd).resolve()) if self.cwd else None,
                "last_observed_at": max(timestamp, trace.metadata["last_observed_at"]),
            },
        )

    def finish(self, timestamp: float | None, status: str = "ok") -> None:
        if timestamp is None or self.turn_id is None:
            return
        trace_id = stable_id(self.source, self.session_id, self.turn_id)
        trace = self.turns.get(trace_id)
        if trace is None:
            return
        self.turns[trace_id] = replace(
            trace,
            ended_at=max(timestamp, trace.started_at),
            duration_ms=max(0, (timestamp - trace.started_at) * 1000),
            status=status,
            metadata={**trace.metadata, "completion_observed": True},
        )

    def model(
        self,
        *,
        span_id: str,
        timestamp: float,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        metadata: dict[str, Any],
        error: str | None = None,
        status: str = "ok",
    ) -> str:
        trace = self.current(timestamp, span_id)
        self.observe(trace.id, timestamp)
        self.spans[span_id] = SpanRecord(
            id=span_id,
            trace_id=trace.id,
            parent_id=None,
            kind="model",
            name=f"{provider}:{model}",
            provider=provider,
            model=model,
            started_at=timestamp,
            ended_at=timestamp,
            duration_ms=0,
            prompt={"redacted": True, "source": self.source},
            response={"redacted": True, "source": self.source},
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            retry_count=0,
            status=status,
            error=error,
            metadata={"source": self.source, "timing": "unknown", **metadata},
        )
        return span_id

    def tool(self, call_id: str, name: str, timestamp: float, parent_id: str | None = None) -> None:
        trace = self.current(timestamp, call_id)
        self.observe(trace.id, timestamp)
        span_id = stable_id(self.source, self.session_id, "tool", call_id)
        if span_id in self.spans:
            return
        self.pending[call_id] = span_id
        self.spans[span_id] = SpanRecord(
            id=span_id,
            trace_id=trace.id,
            parent_id=parent_id,
            kind="tool",
            name=name,
            provider=None,
            model=None,
            started_at=timestamp,
            ended_at=timestamp,
            duration_ms=0,
            prompt={"redacted": True},
            response={"redacted": True},
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            retry_count=0,
            status="running",
            error=None,
            metadata={"source": self.source, "call_id": call_id, "timing": "unknown"},
        )

    def tool_result(self, call_id: str, timestamp: float, failed: bool = False) -> None:
        span_id = self.pending.pop(call_id, None)
        if span_id is None:
            return
        span = self.spans[span_id]
        ordered = timestamp >= span.started_at
        self.spans[span_id] = replace(
            span,
            ended_at=max(timestamp, span.started_at),
            duration_ms=max(0, (timestamp - span.started_at) * 1000),
            status="error" if failed else "ok",
            error="Tool result reported an error" if failed else None,
            metadata={
                **span.metadata,
                "success": not failed,
                "timing": "log_interval" if ordered else "unknown",
            },
        )
        self.observe(span.trace_id, timestamp)

    def persist(self, store: SQLiteStore) -> int:
        # A finished turn with a missing tool result is incomplete, not a successful tool call.
        for span_id in self.pending.values():
            span = self.spans[span_id]
            if self.turns[span.trace_id].ended_at is not None:
                self.spans[span_id] = replace(span, status="interrupted")
        statuses_by_trace: dict[str, set[str]] = {}
        for span in self.spans.values():
            statuses_by_trace.setdefault(span.trace_id, set()).add(span.status)
        for trace_id, trace in self.turns.items():
            if trace.ended_at is None:
                continue
            statuses = statuses_by_trace.get(trace_id, set())
            status = "error" if "error" in statuses else trace.status
            if status == "ok" and "interrupted" in statuses:
                status = "interrupted"
            self.turns[trace_id] = replace(trace, status=status)
        store.record_agent_snapshot(list(self.turns.values()), list(self.spans.values()))
        return sum(span.kind == "model" for span in self.spans.values())
