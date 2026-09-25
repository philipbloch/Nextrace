"""Core trace and span primitives."""

from __future__ import annotations

import contextvars
import time
import traceback
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from nextrace.pricing import default_pricing_registry
from nextrace.storage import SQLiteStore
from nextrace.types import JsonDict, SpanRecord, TraceRecord


class TraceContextError(RuntimeError):
    """Raised when a trace-only operation is used outside a trace."""


def _now() -> float:
    return time.time()


def _duration_ms(started_at: float, ended_at: float | None = None) -> float:
    return round(((ended_at or _now()) - started_at) * 1000, 3)


@dataclass
class Span:
    """A timed operation inside a trace."""

    trace: Trace
    kind: str
    name: str
    provider: str | None = None
    model: str | None = None
    prompt: Any | None = None
    response: Any | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    retry_count: int = 0
    metadata: JsonDict = field(default_factory=dict)
    status: str = "ok"
    error: str | None = None
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    started_at: float = field(default_factory=_now)
    ended_at: float | None = None

    def __enter__(self) -> Span:
        self.started_at = _now()
        self.parent_id = self.trace.current_span_id
        self.trace.current_span_id = self.span_id
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> bool:
        if exc is not None:
            self.status = "error"
            self.error = f"{type(exc).__name__}: {exc}"
            self.metadata.setdefault("traceback", "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            self.finish()
        finally:
            self.trace.current_span_id = self.parent_id
        return False

    def finish(self) -> None:
        if self.ended_at is not None:
            return
        self.ended_at = _now()
        if self.total_tokens is None:
            input_tokens = self.input_tokens or 0
            output_tokens = self.output_tokens or 0
            self.total_tokens = input_tokens + output_tokens if input_tokens or output_tokens else None
        if self.cost_usd is None and self.input_tokens is not None and self.output_tokens is not None:
            self.cost_usd = default_pricing_registry.estimate(
                provider=self.provider,
                model=self.model,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
            )
        self.trace.store.record_span(self.to_record())

    def set_result(
        self,
        *,
        response: Any | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cost_usd: float | None = None,
        metadata: JsonDict | None = None,
    ) -> Span:
        if response is not None:
            self.response = response
        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens
        if total_tokens is not None:
            self.total_tokens = total_tokens
        if cost_usd is not None:
            self.cost_usd = cost_usd
        if metadata:
            self.metadata.update(metadata)
        return self

    def add_retry(self, error: BaseException | str | None = None) -> Span:
        self.retry_count += 1
        if error is not None:
            self.metadata.setdefault("retry_errors", []).append(str(error))
        return self

    def fail(self, error: BaseException | str) -> Span:
        self.status = "error"
        self.error = str(error)
        return self

    def score(
        self,
        name: str,
        value: float,
        *,
        comment: str | None = None,
        metadata: JsonDict | None = None,
    ) -> Span:
        self.trace.score(name, value, comment=comment, span_id=self.span_id, metadata=metadata)
        return self

    def score_accuracy(
        self,
        value: float,
        *,
        comment: str | None = None,
        metadata: JsonDict | None = None,
    ) -> Span:
        return self.score("accuracy", value, comment=comment, metadata=metadata)

    def to_record(self) -> SpanRecord:
        ended_at = self.ended_at or _now()
        return SpanRecord(
            id=self.span_id,
            trace_id=self.trace.trace_id,
            parent_id=self.parent_id,
            kind=self.kind,
            name=self.name,
            provider=self.provider,
            model=self.model,
            started_at=self.started_at,
            ended_at=ended_at,
            duration_ms=_duration_ms(self.started_at, ended_at),
            prompt=self.prompt,
            response=self.response,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.total_tokens,
            cost_usd=self.cost_usd,
            retry_count=self.retry_count,
            status=self.status,
            error=self.error,
            metadata=self.metadata,
        )


@dataclass
class Trace:
    """A complete AI workflow trace."""

    application: str
    name: str | None = None
    trace_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    tags: list[str] | None = None
    metadata: JsonDict | None = None
    store: SQLiteStore | None = None
    active_trace_var: contextvars.ContextVar[Trace | None] | None = None
    started_at: float = field(default_factory=_now)
    ended_at: float | None = None
    status: str = "ok"
    error: str | None = None
    current_span_id: str | None = None
    _active_token: contextvars.Token | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.trace_id is None:
            self.trace_id = uuid.uuid4().hex
        if self.session_id is None:
            self.session_id = uuid.uuid4().hex
        if self.name is None:
            self.name = self.application
        if self.tags is None:
            self.tags = []
        if self.metadata is None:
            self.metadata = {}
        if self.store is None:
            from nextrace.context import default_store

            self.store = default_store()

    def __enter__(self) -> Trace:
        self.started_at = _now()
        if self.active_trace_var is not None:
            self._active_token = self.active_trace_var.set(self)
        try:
            self.store.start_trace(self.to_record())
        except BaseException:
            self._reset_active_trace()
            raise
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> bool:
        if exc is not None:
            self.status = "error"
            self.error = f"{type(exc).__name__}: {exc}"
            self.metadata.setdefault("traceback", "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            self.finish()
        finally:
            self._reset_active_trace()
        return False

    def finish(self) -> None:
        if self.ended_at is not None:
            return
        self.ended_at = _now()
        self.store.finish_trace(self.to_record())

    def _reset_active_trace(self) -> None:
        if self.active_trace_var is None or self._active_token is None:
            return
        token = self._active_token
        self._active_token = None
        self.active_trace_var.reset(token)

    @contextmanager
    def step(
        self,
        kind: str,
        name: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        prompt: Any | None = None,
        metadata: JsonDict | None = None,
    ) -> Iterator[Span]:
        span = Span(
            trace=self,
            kind=kind,
            name=name,
            provider=provider,
            model=model,
            prompt=prompt,
            metadata=metadata or {},
        )
        with span:
            yield span

    def model_call(
        self,
        *,
        provider: str,
        model: str,
        prompt: Any,
        response: Any,
        name: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        retry_count: int = 0,
        metadata: JsonDict | None = None,
        error: BaseException | str | None = None,
    ) -> Span:
        return self._instant_span(
            kind="model",
            name=name or f"{provider}:{model}",
            provider=provider,
            model=model,
            prompt=prompt,
            response=response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            retry_count=retry_count,
            metadata=metadata,
            error=error,
        )

    def tool_call(
        self,
        *,
        name: str,
        arguments: Any | None = None,
        result: Any | None = None,
        accuracy: float | None = None,
        success: bool | None = None,
        latency_ms: float | None = None,
        retry_count: int = 0,
        metadata: JsonDict | None = None,
        error: BaseException | str | None = None,
    ) -> Span:
        metadata = dict(metadata or {})
        if accuracy is not None:
            metadata["accuracy"] = accuracy
        if success is not None:
            metadata["success"] = success
        return self._instant_span(
            kind="tool",
            name=name,
            prompt=arguments,
            response=result,
            latency_ms=latency_ms,
            retry_count=retry_count,
            metadata=metadata,
            error=error,
        )

    def retrieval(
        self,
        *,
        query: str,
        contexts: Any,
        name: str = "retrieval",
        provider: str | None = None,
        latency_ms: float | None = None,
        metadata: JsonDict | None = None,
        error: BaseException | str | None = None,
    ) -> Span:
        return self._instant_span(
            kind="retrieval",
            name=name,
            provider=provider,
            prompt=query,
            response=contexts,
            latency_ms=latency_ms,
            metadata=metadata or {},
            error=error,
        )

    def handoff(
        self,
        *,
        from_agent: str,
        to_agent: str,
        reason: str | None = None,
        payload: Any | None = None,
        latency_ms: float | None = None,
        metadata: JsonDict | None = None,
    ) -> Span:
        metadata = dict(metadata or {})
        metadata.update({"from_agent": from_agent, "to_agent": to_agent, "reason": reason})
        return self._instant_span(
            kind="handoff",
            name=f"{from_agent} -> {to_agent}",
            prompt=payload,
            response={"to_agent": to_agent, "reason": reason},
            latency_ms=latency_ms,
            metadata=metadata,
        )

    def score(
        self,
        name: str,
        value: float,
        *,
        comment: str | None = None,
        span_id: str | None = None,
        metadata: JsonDict | None = None,
    ) -> None:
        self.store.record_score(
            trace_id=self.trace_id,
            span_id=span_id,
            name=name,
            value=value,
            comment=comment,
            metadata=metadata or {},
        )

    def feedback(
        self,
        *,
        rating: int | None = None,
        comment: str | None = None,
        user_id: str | None = None,
        metadata: JsonDict | None = None,
    ) -> None:
        self.store.record_feedback(
            trace_id=self.trace_id,
            rating=rating,
            comment=comment,
            user_id=user_id or self.user_id,
            metadata=metadata or {},
        )

    def retry(self, name: str, *, error: BaseException | str | None = None, metadata: JsonDict | None = None) -> None:
        self.store.record_event(
            trace_id=self.trace_id,
            span_id=self.current_span_id,
            kind="retry",
            name=name,
            payload={"error": str(error) if error else None, **(metadata or {})},
        )

    def event(self, name: str, payload: JsonDict | None = None, *, kind: str = "event") -> None:
        self.store.record_event(
            trace_id=self.trace_id,
            span_id=self.current_span_id,
            kind=kind,
            name=name,
            payload=payload or {},
        )

    def to_record(self) -> TraceRecord:
        ended_at = self.ended_at or _now()
        return TraceRecord(
            id=self.trace_id,
            session_id=self.session_id,
            application=self.application,
            name=self.name or self.application,
            user_id=self.user_id,
            started_at=self.started_at,
            ended_at=self.ended_at,
            duration_ms=_duration_ms(self.started_at, ended_at),
            status=self.status,
            error=self.error,
            tags=self.tags or [],
            metadata=self.metadata or {},
        )

    def _instant_span(
        self,
        *,
        kind: str,
        name: str,
        provider: str | None = None,
        model: str | None = None,
        prompt: Any | None = None,
        response: Any | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        retry_count: int = 0,
        metadata: JsonDict | None = None,
        error: BaseException | str | None = None,
    ) -> Span:
        started_at = _now()
        duration = latency_ms if latency_ms is not None else 0
        span = Span(
            trace=self,
            kind=kind,
            name=name,
            provider=provider,
            model=model,
            prompt=prompt,
            response=response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            retry_count=retry_count,
            metadata=metadata or {},
            parent_id=self.current_span_id,
            started_at=started_at - (duration / 1000),
        )
        if error is not None:
            span.fail(error)
        span.finish()
        return span
