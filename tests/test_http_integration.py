from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from nextrace import SQLiteStore, ai_trace
from nextrace.integrations.http import traced_http_json


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(
            {
                "result": "ok",
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            }
        ).encode()


def test_traced_http_json_redacts_query_values(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout: FakeResponse())
    store = SQLiteStore(tmp_path / "traces.db")

    with ai_trace("http-client", store=store):
        traced_http_json(
            "https://example.com/generate?access_token=secret&region=ca",
            payload={"model": "custom-model", "prompt": "hello"},
        )

    detail = store.get_trace(store.list_traces()[0]["id"])
    assert detail is not None
    metadata = detail["spans"][0]["metadata"]
    assert metadata["url"] == (
        "https://example.com/generate?access_token=%5BREDACTED%5D&region=%5BREDACTED%5D"
    )
    assert "secret" not in json.dumps(detail)


def test_http_retry_records_one_span_and_retry_event(tmp_path, monkeypatch):
    attempts = 0

    def request(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise URLError("temporary failure")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", request)
    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("http", store=store) as trace:
        result = traced_http_json("https://example.com", payload={}, retries=1)
    detail = store.get_trace(trace.trace_id)
    assert result["result"] == "ok"
    assert attempts == 2
    assert len(detail["spans"]) == len(detail["events"]) == 1
    assert detail["spans"][0]["retry_count"] == 1
    assert detail["spans"][0]["status"] == "ok"


def test_invalid_http_json_is_recorded_as_a_failure(tmp_path, monkeypatch):
    class InvalidResponse(FakeResponse):
        def read(self):
            return b"not JSON"

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: InvalidResponse())
    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("http", store=store) as trace:
        with pytest.raises(json.JSONDecodeError):
            traced_http_json("https://example.com", payload={})
    spans = store.get_trace(trace.trace_id)["spans"]
    assert len(spans) == 1
    assert spans[0]["status"] == "error"
