from __future__ import annotations

import json

from nextrace import SQLiteStore
from nextrace.codex_usage import import_codex_usage


def test_import_codex_usage_records_redacted_model_span(tmp_path):
    session_file = tmp_path / "rollout-2026-07-14T09-48-39-session-1.jsonl"
    events = [
        {
            "type": "session_meta",
            "timestamp": "2026-07-14T16:48:39.483Z",
            "payload": {
                "id": "session-1",
                "cwd": "/repo",
                "model_provider": "shopify-proxy",
            },
        },
        {
            "type": "event_msg",
            "timestamp": "2026-07-14T16:48:40.461Z",
            "payload": {"type": "task_started", "turn_id": "turn-1", "started_at": 1784047720},
        },
        {
            "type": "turn_context",
            "timestamp": "2026-07-14T16:48:40.498Z",
            "payload": {
                "turn_id": "turn-1",
                "cwd": "/repo",
                "model": "openai-org-test:gpt-test",
                "effort": "high",
            },
        },
        {
            "type": "event_msg",
            "timestamp": "2026-07-14T16:48:55.612Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 100,
                        "output_tokens": 50,
                        "reasoning_output_tokens": 10,
                        "total_tokens": 1050,
                    },
                    "model_context_window": 258400,
                },
            },
        },
    ]
    session_file.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    store = SQLiteStore(tmp_path / "traces.db")

    stats = import_codex_usage(
        store=store,
        application="se-assistant",
        files=[session_file],
        input_cost_per_million=1.0,
        cached_input_cost_per_million=0.1,
        output_cost_per_million=2.0,
    )

    assert stats.imported == 1
    traces = store.list_traces()
    assert len(traces) == 1
    detail = store.get_trace(traces[0]["id"])
    assert detail is not None
    span = detail["spans"][0]
    assert span["kind"] == "model"
    assert span["provider"] == "shopify-proxy"
    assert span["model"] == "gpt-test"
    assert span["input_tokens"] == 1000
    assert span["output_tokens"] == 50
    assert span["cost_usd"] == 0.00101
    assert span["prompt"] == {"redacted": True, "source": "codex"}
    assert span["response"] == {"redacted": True, "source": "codex"}

    rendered = json.dumps(detail)
    assert "raw prompt" not in rendered

    stats = import_codex_usage(store=store, application="se-assistant", files=[session_file])
    assert stats.imported == 1
    assert len(store.list_traces()) == 1
