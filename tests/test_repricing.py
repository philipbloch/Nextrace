from __future__ import annotations

from nextrace import SQLiteStore
from nextrace.pricing import PricingRegistry
from nextrace.repricing import reprice_model_spans
from nextrace.types import SpanRecord, TraceRecord


def test_reprice_model_spans_uses_cached_codex_tokens(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    store.start_trace(
        TraceRecord(
            id="trace-1",
            session_id="session-1",
            application="nextrace",
            name="codex:model:gpt-5.5",
            user_id=None,
            started_at=1784570400.0,
            ended_at=1784570401.0,
            duration_ms=1000.0,
            status="ok",
            error=None,
            tags=["codex"],
            metadata={"source": "codex"},
        )
    )
    store.record_span(
        SpanRecord(
            id="span-1",
            trace_id="trace-1",
            parent_id=None,
            kind="model",
            name="shopify-proxy:gpt-5.5",
            provider="shopify-proxy",
            model="gpt-5.5",
            started_at=1784570400.0,
            ended_at=1784570401.0,
            duration_ms=1000.0,
            prompt={"redacted": True},
            response={"redacted": True},
            input_tokens=1000,
            output_tokens=50,
            total_tokens=1050,
            cost_usd=0.00605,
            retry_count=0,
            status="ok",
            error=None,
            metadata={"source": "codex", "cached_input_tokens": 100},
        )
    )
    registry = PricingRegistry()
    registry.register_override(
        provider="shopify-proxy",
        model="gpt-5.5",
        input_per_million=10.0,
        cached_input_per_million=1.0,
        output_per_million=40.0,
    )

    stats = reprice_model_spans(
        db_path=tmp_path / "traces.db",
        registry=registry,
        application="nextrace",
        since=1784570399.0,
        until=1784570402.0,
    )

    assert stats.matched == 1
    assert stats.repriced == 1
    assert stats.skipped == 0
    assert stats.cost_before == 0.00605
    assert stats.cost_after == 0.0111
    trace = store.get_trace("trace-1")
    assert trace is not None
    assert trace["spans"][0]["cost_usd"] == 0.0111


def test_reprice_model_spans_dry_run_does_not_update(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    store.start_trace(
        TraceRecord(
            id="trace-1",
            session_id="session-1",
            application="nextrace",
            name="codex:model:gpt-5.5",
            user_id=None,
            started_at=1784570400.0,
            ended_at=1784570401.0,
            duration_ms=1000.0,
            status="ok",
            error=None,
        )
    )
    store.record_span(
        SpanRecord(
            id="span-1",
            trace_id="trace-1",
            parent_id=None,
            kind="model",
            name="shopify-proxy:gpt-5.5",
            provider="shopify-proxy",
            model="gpt-5.5",
            started_at=1784570400.0,
            ended_at=1784570401.0,
            duration_ms=1000.0,
            prompt=None,
            response=None,
            input_tokens=1000,
            output_tokens=50,
            total_tokens=1050,
            cost_usd=0.00605,
            retry_count=0,
            status="ok",
            error=None,
            metadata={"source": "codex", "cached_input_tokens": 100},
        )
    )
    registry = PricingRegistry()
    registry.register_override(
        provider="shopify-proxy",
        model="gpt-5.5",
        input_per_million=10.0,
        cached_input_per_million=1.0,
        output_per_million=40.0,
    )

    stats = reprice_model_spans(
        db_path=tmp_path / "traces.db",
        registry=registry,
        dry_run=True,
    )

    assert stats.repriced == 1
    trace = store.get_trace("trace-1")
    assert trace is not None
    assert trace["spans"][0]["cost_usd"] == 0.00605


def test_reprice_model_spans_uses_pi_cache_tokens(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    store.start_trace(
        TraceRecord(
            id="pi-trace",
            session_id="pi-session",
            application="pi-app",
            name="pi:model:custom-model",
            user_id=None,
            started_at=1784570400.0,
            ended_at=1784570401.0,
            duration_ms=1000.0,
            status="ok",
            error=None,
        )
    )
    store.record_span(
        SpanRecord(
            id="pi-span",
            trace_id="pi-trace",
            parent_id=None,
            kind="model",
            name="custom-provider:custom-model",
            provider="custom-provider",
            model="custom-model",
            started_at=1784570400.0,
            ended_at=1784570401.0,
            duration_ms=1000.0,
            prompt=None,
            response=None,
            input_tokens=1600,
            output_tokens=50,
            total_tokens=1650,
            cost_usd=0.01,
            retry_count=0,
            status="ok",
            error=None,
            metadata={
                "source": "pi",
                "cache_read_input_tokens": 500,
                "cache_write_input_tokens": 100,
            },
        )
    )
    registry = PricingRegistry()
    registry.register_override(
        provider="custom-provider",
        model="custom-model",
        input_per_million=10.0,
        cached_input_per_million=1.0,
        cache_write_5m_per_million=12.5,
        output_per_million=40.0,
    )

    stats = reprice_model_spans(db_path=tmp_path / "traces.db", registry=registry)

    assert stats.repriced == 1
    assert stats.cost_after == 0.01375
    trace = store.get_trace("pi-trace")
    assert trace is not None
    assert trace["spans"][0]["cost_usd"] == 0.01375
