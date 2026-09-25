from __future__ import annotations

from nextrace import SQLiteStore, ai_trace


def test_summary_metrics(tmp_path):
    store = SQLiteStore(tmp_path / "summary.db")

    with ai_trace("app-a", store=store) as trace:
        trace.model_call(
            provider="shopify-proxy",
            model="gpt-test",
            prompt="p",
            response="r",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.05,
            latency_ms=200,
        )
        trace.tool_call(name="search", arguments={}, result={}, success=True, accuracy=0.8)

    with ai_trace("app-b", store=store) as trace:
        trace.model_call(
            provider="local",
            model="llama-test",
            prompt="p",
            response="r",
            input_tokens=50,
            output_tokens=20,
            cost_usd=0,
            latency_ms=900,
        )

    summary = store.summary()
    assert summary["totals"]["traces"] == 2
    assert summary["totals"]["applications"] == 2
    assert summary["cost_totals"]["estimated_cost_usd"] == 0.05
    assert summary["cost_totals"]["shopify_proxy_cost_usd"] == 0.05
    assert summary["cost_by_application"][0]["application"] == "app-a"
    assert summary["shopify_cost_by_application"] == [{"application": "app-a", "cost_usd": 0.05}]
    assert summary["tool_call_accuracy"][0]["name"] == "search"
    assert summary["prompt_model_comparisons"][0]["calls"] == 1

    filtered = store.summary(application="app-a")
    assert filtered["totals"]["traces"] == 1
    assert filtered["totals"]["applications"] == 1
    assert filtered["cost_by_application"] == [{"application": "app-a", "cost_usd": 0.05}]
    assert filtered["failure_rates"][0]["application"] == "app-a"
    assert filtered["slowest_steps"][0]["model"] == "gpt-test"
    assert filtered["tool_call_accuracy"][0]["name"] == "search"
    assert len(filtered["prompt_model_comparisons"]) == 1
    model_summary = filtered["prompt_model_comparisons"][0]
    assert model_summary["provider"] == "shopify-proxy"
    assert model_summary["model"] == "gpt-test"
    assert model_summary["calls"] == 1
    assert model_summary["cost_usd"] == 0.05
    assert model_summary["avg_tokens"] == 150.0


def test_list_traces_filters_by_status(tmp_path):
    store = SQLiteStore(tmp_path / "summary.db")

    with ai_trace("app-a", name="happy-path", store=store):
        pass

    try:
        with ai_trace("app-a", name="failed-path", store=store):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    ok_traces = store.list_traces(application="app-a", status="ok")
    error_traces = store.list_traces(application="app-a", status="error")

    assert [trace["name"] for trace in ok_traces] == ["happy-path"]
    assert [trace["name"] for trace in error_traces] == ["failed-path"]


def test_summary_filters_by_date_range(tmp_path):
    store = SQLiteStore(tmp_path / "summary.db")

    with ai_trace("old-app", store=store) as trace:
        trace.model_call(provider="openai", model="old", prompt="p", response="r", cost_usd=0.25)
        old_id = trace.trace_id

    with ai_trace("new-app", store=store) as trace:
        trace.model_call(provider="openai", model="new", prompt="p", response="r", cost_usd=0.75)
        new_id = trace.trace_id

    with store._connect() as conn:
        conn.execute("UPDATE traces SET started_at = 1000 WHERE id = ?", (old_id,))
        conn.execute("UPDATE spans SET started_at = 1000, ended_at = 1001 WHERE trace_id = ?", (old_id,))
        conn.execute("UPDATE traces SET started_at = 2000 WHERE id = ?", (new_id,))
        conn.execute("UPDATE spans SET started_at = 2000, ended_at = 2001 WHERE trace_id = ?", (new_id,))

    summary = store.summary(since=1500, until=2500)

    assert summary["totals"]["traces"] == 1
    assert summary["totals"]["applications"] == 1
    assert summary["cost_by_application"] == [{"application": "new-app", "cost_usd": 0.75}]
    assert summary["prompt_model_comparisons"][0]["model"] == "new"


def test_list_traces_filters_by_date_range(tmp_path):
    store = SQLiteStore(tmp_path / "summary.db")

    with ai_trace("app-a", name="old", store=store) as trace:
        old_id = trace.trace_id

    with ai_trace("app-a", name="new", store=store) as trace:
        new_id = trace.trace_id

    with store._connect() as conn:
        conn.execute("UPDATE traces SET started_at = 1000 WHERE id = ?", (old_id,))
        conn.execute("UPDATE traces SET started_at = 2000 WHERE id = ?", (new_id,))

    traces = store.list_traces(since=1500, until=2500)

    assert [trace["name"] for trace in traces] == ["new"]


def test_summary_sorts_failure_rates_by_rate(tmp_path):
    store = SQLiteStore(tmp_path / "summary.db")

    try:
        with ai_trace("one-bad-trace", store=store):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    for _ in range(2):
        try:
            with ai_trace("more-failures-lower-rate", store=store):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
    for _ in range(2):
        with ai_trace("more-failures-lower-rate", store=store):
            pass

    failure_rates = store.summary()["failure_rates"]

    assert [row["application"] for row in failure_rates[:2]] == [
        "one-bad-trace",
        "more-failures-lower-rate",
    ]
    assert failure_rates[0]["failure_rate"] == 1.0
    assert failure_rates[1]["failure_rate"] == 0.5


def test_summary_sorts_slowest_steps_by_average_latency(tmp_path):
    store = SQLiteStore(tmp_path / "summary.db")

    with ai_trace("latency-agent", store=store) as trace:
        trace.tool_call(name="medium-step", latency_ms=400, success=True)
        trace.retrieval(query="q", contexts=[], name="slow-step", latency_ms=800)
        trace.model_call(
            provider="openai",
            model="fast-model",
            prompt="p",
            response="r",
            latency_ms=20,
        )

    slowest_steps = store.summary()["slowest_steps"]

    assert slowest_steps[0]["name"] == "slow-step"
    assert [row["avg_ms"] for row in slowest_steps] == sorted(
        [row["avg_ms"] for row in slowest_steps],
        reverse=True,
    )


def test_summary_tool_accuracy_keeps_success_and_explicit_accuracy_separate(tmp_path):
    store = SQLiteStore(tmp_path / "summary.db")

    with ai_trace("tool-agent", store=store) as trace:
        trace.tool_call(name="success-only", success=True)
        trace.tool_call(name="success-only", success=False)
        trace.tool_call(name="explicit", success=False, accuracy=0.9)

    tool_accuracy = store.summary()["tool_call_accuracy"]
    by_name = {row["name"]: row for row in tool_accuracy}

    assert [row["name"] for row in tool_accuracy[:2]] == ["explicit", "success-only"]
    assert by_name["success-only"]["success_rate"] == 0.5
    assert by_name["success-only"]["accuracy"] is None
    assert by_name["explicit"]["success_rate"] == 0.0
    assert by_name["explicit"]["accuracy"] == 0.9


def test_summary_tool_accuracy_uses_scores_attached_to_tool_spans(tmp_path):
    store = SQLiteStore(tmp_path / "summary.db")

    with ai_trace("tool-agent", store=store) as trace:
        search_span = trace.tool_call(name="search", success=True)
        search_span.score_accuracy(0.75)
        trace.score("helpfulness", 0.1, span_id=search_span.span_id)

        lookup_span = trace.tool_call(name="lookup", success=True)
        trace.score("tool_accuracy", 0.6, span_id=lookup_span.span_id)

        trace.tool_call(name="direct", success=True, accuracy=0.8)

    tool_accuracy = store.summary()["tool_call_accuracy"]
    by_name = {row["name"]: row for row in tool_accuracy}

    assert by_name["search"]["accuracy"] == 0.75
    assert by_name["search"]["scored_calls"] == 1
    assert by_name["lookup"]["accuracy"] == 0.6
    assert by_name["direct"]["accuracy"] == 0.8
