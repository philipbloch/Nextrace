from __future__ import annotations

import asyncio

import pytest

from nextrace import SQLiteStore, ai_trace, current_trace
from nextrace.integrations.local import traced_model_call


def test_parallel_async_spans_keep_their_common_parent(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")

    async def run(trace):
        both_started = asyncio.Event()
        started = 0

        async def child(name):
            nonlocal started
            with trace.step("tool", name):
                started += 1
                if started == 2:
                    both_started.set()
                await both_started.wait()

        with trace.step("workflow", "parent") as parent:
            await asyncio.gather(child("first"), child("second"))
            assert trace.current_span_id == parent.span_id
        assert trace.current_span_id is None
        return parent.span_id

    with ai_trace("parallel", store=store) as trace:
        parent_id = asyncio.run(run(trace))
    children = [
        span for span in store.get_trace(trace.trace_id)["spans"] if span["name"] != "parent"
    ]
    assert len(children) == 2
    assert {span["parent_id"] for span in children} == {parent_id}


def test_trace_records_core_ai_workflow(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")

    with ai_trace("support-agent", session_id="s-1", store=store) as trace:
        assert current_trace() is trace
        trace.retrieval(query="refund policy", contexts=[{"id": "doc-1", "text": "30 days"}])
        model_span = trace.model_call(
            provider="openai",
            model="gpt-test",
            prompt="Can I return this?",
            response="Yes.",
            input_tokens=10,
            output_tokens=3,
            latency_ms=25,
        )
        trace.tool_call(
            name="lookup_order",
            arguments={"order_id": "1"},
            result={"status": "delivered"},
            success=True,
            accuracy=0.95,
        )
        trace.handoff(from_agent="frontline", to_agent="returns", reason="Return requested")
        trace.score("helpfulness", 0.92, span_id=model_span.span_id)
        trace.feedback(rating=5, comment="Great")

    traces = store.list_traces()
    assert len(traces) == 1
    assert traces[0]["application"] == "support-agent"
    assert traces[0]["span_count"] == 4

    detail = store.get_trace(traces[0]["id"])
    assert detail is not None
    assert {span["kind"] for span in detail["spans"]} == {"retrieval", "model", "tool", "handoff"}
    assert detail["scores"][0]["name"] == "helpfulness"
    assert detail["feedback"][0]["rating"] == 5


def test_trace_records_errors(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")

    with pytest.raises(ValueError):
        with ai_trace("failing-agent", store=store):
            raise ValueError("bad prompt")

    trace = store.list_traces()[0]
    assert trace["status"] == "error"
    assert "ValueError" in trace["error"]


def test_record_span_preserves_timing_parent_and_error(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "traces.db")
    monkeypatch.setattr("nextrace.core.time.time", lambda: 1000.0)

    with ai_trace("proxy", store=store) as trace:
        with trace.step("request", "parent") as parent:
            span = trace.record_span(
                kind="transport",
                name="GET",
                latency_ms=125,
                error=TimeoutError("timed out"),
            )

    saved = next(
        row for row in store.get_trace(trace.trace_id)["spans"] if row["id"] == span.span_id
    )
    assert saved["kind"] == "transport"
    assert saved["parent_id"] == parent.span_id
    assert saved["started_at"] == 999.875
    assert saved["ended_at"] == 1000.0
    assert saved["duration_ms"] == 125
    assert saved["status"] == "error"
    assert saved["error"] == "timed out"


def test_local_decorator_records_model_span(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")

    @traced_model_call(provider="local", model="classifier")
    def classify(message: str) -> dict[str, str]:
        return {"label": "returns", "message": message}

    with ai_trace("decorator-agent", store=store):
        classify("I need to return this")

    detail = store.get_trace(store.list_traces()[0]["id"])
    assert detail is not None
    assert detail["spans"][0]["kind"] == "model"
    assert detail["spans"][0]["provider"] == "local"
    assert detail["spans"][0]["model"] == "classifier"


def test_trace_context_is_reset_when_finishing_the_trace_fails(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "traces.db")

    def fail_to_finish(_record):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(store, "finish_trace", fail_to_finish)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        with ai_trace("failing-store", store=store):
            pass

    assert current_trace() is None
