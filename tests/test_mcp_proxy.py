from __future__ import annotations

import json
from http.client import HTTPConnection
from io import BytesIO
from urllib.error import HTTPError

import pytest

from nextrace import SQLiteStore
from nextrace.mcp_proxy import (
    MCPTraceRecorder,
    build_target_url,
    copy_json_lines,
    redact_url,
    summarize_json,
)
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


@pytest.mark.parametrize(
    "invalid",
    [b'{"result":"\xff"}\n', b"[" * 10_000 + b"0" + b"]" * 10_000 + b"\n"],
    ids=["invalid-utf8", "excessive-nesting"],
)
def test_stdio_forwards_invalid_frames_and_continues_recording_valid_messages(invalid):
    valid = b'{"id":7,"result":{}}\n'
    target = BytesIO()
    observed = []
    copy_json_lines(source=BytesIO(invalid + valid), target=target, observer=observed.append)
    assert target.getvalue() == invalid + valid
    assert observed == [{"id": 7, "result": {}}]


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


@pytest.mark.parametrize(
    ("http_method", "status_code", "rpc_method", "error", "expected_error", "kind", "outcome"),
    [
        ("GET", 405, None, None, None, "transport", "event_stream_not_supported"),
        ("delete", 405, None, None, None, "transport", "session_termination_not_supported"),
        ("GET", 200, None, None, None, "transport", None),
        ("POST", 200, "tools/call", None, None, "tool", None),
        ("POST", 405, "tools/call", None, "HTTP 405", "tool", None),
        ("POST", 405, None, None, "HTTP 405", "tool", None),
        ("OPTIONS", 405, None, None, "HTTP 405", "transport", None),
        ("GET", 405, "tools/call", None, "HTTP 405", "tool", None),
        ("GET", 401, None, None, "HTTP 401", "transport", None),
        ("POST", 429, "tools/call", None, "HTTP 429", "tool", None),
        ("GET", 500, None, None, "HTTP 500", "transport", None),
        ("GET", None, None, None, "HTTP unknown", "transport", None),
        ("DELETE", 405, None, TimeoutError("timed out"), "timed out", "transport", None),
    ],
)
def test_http_status_classification(
    tmp_path,
    http_method,
    status_code,
    rpc_method,
    error,
    expected_error,
    kind,
    outcome,
):
    store = SQLiteStore(tmp_path / "mcp.db")
    recorder = MCPTraceRecorder(
        application="test",
        server="test-mcp",
        transport="http",
        store=store,
    )
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": rpc_method}).encode()
    pending = recorder.record_http_call(
        request_body=body if rpc_method else b"",
        request_headers={},
        target_url="https://example.com/mcp",
        http_method=http_method,
    )
    recorder.finish_http_call(
        pending,
        status_code=status_code,
        response_bytes=12,
        error=error,
    )

    detail = store.get_trace(pending.trace.trace_id)
    span = detail["spans"][0]
    expected_status = "error" if expected_error is not None else "ok"
    assert detail["status"] == span["status"] == expected_status
    assert detail["error"] == span["error"] == expected_error
    assert span["kind"] == kind
    assert span["response"]["http_status"] == status_code
    assert span["response"]["http_method"] == http_method.upper()
    assert detail["metadata"]["http_method"] == http_method.upper()
    assert span["metadata"]["http_method"] == http_method.upper()
    assert span["response"].get("transport_outcome") == outcome
    assert span["metadata"].get("transport_outcome") == outcome
    assert detail["metadata"].get("transport_outcome") == outcome


@pytest.mark.parametrize("recording_fails", [False, True])
def test_http_proxy_preserves_responses_and_records_request_methods(
    http_proxy, monkeypatch, recording_fails
):
    if recording_fails:

        def fail_to_record(*args, **kwargs):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(SQLiteStore, "start_trace", fail_to_record)

    def upstream_response(request, timeout):
        assert request.full_url == "https://example.com/mcp"
        assert request.get_header("Authorization") == "Bearer secret"
        status = 429 if request.get_method() == "POST" else 405
        raise HTTPError(
            request.full_url,
            status,
            "Rejected",
            {"Content-Type": "text/plain", "Set-Cookie": "private-cookie"},
            BytesIO(b"private upstream response"),
        )

    monkeypatch.setattr("nextrace.mcp_proxy.urllib.request.urlopen", upstream_response)
    port, store = http_proxy
    for method, status in [("GET", 405), ("DELETE", 405), ("POST", 429)]:
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "search", "arguments": {"query": "private query"}},
                    }
                )
                if method == "POST"
                else None
            )
            connection.request(
                method,
                "/mcp",
                body=body,
                headers={
                    "Authorization": "Bearer secret",
                    "Mcp-Session-Id": "private-session",
                },
            )
            response = connection.getresponse()
            assert response.status == status
            assert response.read() == b"private upstream response"
        finally:
            connection.close()

    traces = store.list_traces()
    if recording_fails:
        assert traces == []
        return
    assert len(traces) == 3
    assert [trace["error"] for trace in store.list_traces(status="error")] == ["HTTP 429"]
    summary = store.summary()
    assert summary["failure_rates"][0]["failures"] == 1
    assert [
        (row["name"], row["calls"], row["success_rate"]) for row in summary["tool_call_accuracy"]
    ] == [("tool:search", 1, 0.0)]
    details = json.dumps([store.get_trace(trace["id"]) for trace in traces])
    for private_value in (
        "private query",
        "secret",
        "private-session",
        "private-cookie",
    ):
        assert private_value not in details
    failed = store.get_trace(store.list_traces(status="error")[0]["id"])
    assert (
        failed["spans"][0]["metadata"]["error_details"][0]["message"] == "private upstream response"
    )


def test_stdio_tool_error_is_not_treated_as_success(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    recorder = MCPTraceRecorder(application="test", server="test", transport="stdio", store=store)
    recorder.observe_client_json(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search"},
        }
    )
    recorder.observe_server_json(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"isError": True, "content": [{"type": "text", "text": "sensitive failure"}]},
        }
    )
    trace = store.list_traces(status="error")[0]
    detail = store.get_trace(trace["id"])
    assert detail["spans"][0]["status"] == "error"
    assert detail["spans"][0]["metadata"]["error_details"][0]["message"] == "sensitive failure"


def test_http_failure_retains_correlation_ids_and_redacts_exception_details(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    recorder = MCPTraceRecorder(application="test", server="test", transport="http", store=store)
    pending = recorder.record_http_call(
        request_body=b'{"id":"rpc-7","method":"tools/call","params":{"name":"search"}}',
        request_headers={"X-Request-ID": "request-1", "Authorization": "Bearer private-auth"},
        target_url="https://example.com/mcp",
    )
    try:
        raise TimeoutError("Upstream timed out with api_key=private-key")
    except TimeoutError as exc:
        recorder.finish_http_call(
            pending,
            status_code=None,
            response_bytes=0,
            response_headers={"X-Correlation-ID": "correlation-1", "Set-Cookie": "private-cookie"},
            error=exc,
        )
    detail = store.get_trace(pending.trace.trace_id)
    metadata = detail["spans"][0]["metadata"]
    assert metadata["correlation_ids"] == {
        "jsonrpc_id": "rpc-7",
        "x-request-id": "request-1",
        "X-Correlation-ID": "correlation-1",
    }
    assert metadata["exception_type"] == "TimeoutError"
    assert "Traceback" in metadata["traceback"]
    assert "api_key=[REDACTED]" in metadata["traceback"]
    assert detail["metadata"]["traceback"] == metadata["traceback"]
    assert "private" not in json.dumps(detail)
