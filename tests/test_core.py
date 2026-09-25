from __future__ import annotations

import pytest

from nextrace import SQLiteStore, ai_trace, current_trace
from nextrace.integrations.local import traced_model_call


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
            cost_usd=0.01,
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
    assert traces[0]["cost_usd"] == 0.01

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
