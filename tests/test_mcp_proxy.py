from __future__ import annotations

import json

from nextrace import SQLiteStore
from nextrace.mcp_proxy import MCPTraceRecorder, build_target_url, redact_url, summarize_json
from nextrace.project import write_current_project


def test_summarize_json_redacts_sensitive_content():
    summary = summarize_json(
        {
            "query": "full text prompt should not be persisted",
            "Authorization": "Bearer secret-token",
            "nested": {"api_key": "abc123", "count": 2},
        }
    )

    rendered = json.dumps(summary)
    assert "full text prompt" not in rendered
    assert "secret-token" not in rendered
    assert "abc123" not in rendered
    assert summary["query"] == {"type": "string", "length": 40}
    assert summary["Authorization"] == "[REDACTED]"
    assert summary["nested"]["api_key"] == "[REDACTED]"


def test_mcp_recorder_tracks_tool_call_without_raw_content(tmp_path):
    store = SQLiteStore(tmp_path / "mcp.db")
    recorder = MCPTraceRecorder(
        application="se-assistant",
        server="tool-gateway",
        transport="stdio",
        store=store,
    )

    recorder.observe_client_json(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "gws_drive_search",
                "arguments": {
                    "query": "fullText contains merchant launch plan",
                    "access_token": "secret",
                },
            },
        }
    )
    recorder.observe_server_json(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "merchant launch plan result"}],
            },
        }
    )

    traces = store.list_traces()
    assert len(traces) == 1
    detail = store.get_trace(traces[0]["id"])
    assert detail is not None
    rendered = json.dumps(detail)
    assert "merchant launch plan" not in rendered
    assert "secret" not in rendered
    assert detail["application"] == "se-assistant"
    assert detail["spans"][0]["name"] == "tool:gws_drive_search"
    assert detail["spans"][0]["prompt"]["access_token"] == "[REDACTED]"
    assert detail["spans"][0]["response"]["content_items"] == 1
    assert store.list_connections()[0]["application"] == "se-assistant"
    assert store.list_connections()[0]["source"] == "tool-gateway"


def test_mcp_recorder_auto_application_uses_active_project(tmp_path, monkeypatch):
    state_path = tmp_path / "current-project.json"
    monkeypatch.setenv("NEXTRACE_PROJECT_STATE", str(state_path))
    store = SQLiteStore(tmp_path / "mcp.db")
    recorder = MCPTraceRecorder(
        application="auto",
        server="tool-gateway",
        transport="http",
        store=store,
    )

    write_current_project(project_path=tmp_path / "first-project", state_path=state_path)
    first = recorder.record_http_call(
        request_body=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        ).encode(),
        request_headers={},
        target_url="https://example.com/mcp",
    )
    recorder.finish_http_call(first, status_code=200, response_bytes=2)

    write_current_project(project_path=tmp_path / "second-project", state_path=state_path)
    second = recorder.record_http_call(
        request_body=json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        ).encode(),
        request_headers={},
        target_url="https://example.com/mcp",
    )
    recorder.finish_http_call(second, status_code=200, response_bytes=2)

    applications = {trace["application"] for trace in store.list_traces()}
    assert applications == {"first-project", "second-project"}
    connection_apps = {connection["application"] for connection in store.list_connections()}
    assert {"first-project", "second-project"}.issubset(connection_apps)


def test_url_helpers_redact_and_preserve_queries():
    assert redact_url("https://example.com/mcp?token=abc&x=1") == (
        "https://example.com/mcp?token=%5BREDACTED%5D&x=%5BREDACTED%5D"
    )
    assert build_target_url("https://example.com/mcp?a=1", "/local?b=2") == (
        "https://example.com/mcp?a=1&b=2"
    )
