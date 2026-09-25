from __future__ import annotations

import json

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
