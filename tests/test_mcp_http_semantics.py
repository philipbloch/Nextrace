import gzip
import json
import os
import subprocess
import sys
import threading
from functools import partial
from http.client import HTTPConnection, RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.error import HTTPError

import pytest

from nextrace import SQLiteStore
from nextrace.mcp_protocol import HTTPResponseInspector
from nextrace.mcp_proxy import MCPTraceRecorder

REQUEST = b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"search"}}'


@pytest.mark.parametrize(
    "media_type,encoding,payload,expected_status,expected_error",
    [
        (
            "application/json",
            "identity",
            b'{"id":7,"result":{"isError":true,"content":[{"text":"private failure"}]}}',
            "error",
            "MCP tool returned isError",
        ),
        (
            "application/json",
            "gzip",
            b'{"id":7,"error":{"code":-32603,"message":"private failure"}}',
            "error",
            "MCP JSON-RPC error (-32603)",
        ),
        (
            "text/event-stream",
            "identity",
            b'data: {"method":"notifications/progress"}\n\ndata: {"id":7,"result":{"isError":true}}\n\n',
            "error",
            "MCP tool returned isError",
        ),
        (
            "text/event-stream",
            "identity",
            b'data: {"id":99,"error":{"code":-1}}\n\ndata: {"id":7,"result":{}}\n\n',
            "ok",
            None,
        ),
        (
            "text/event-stream",
            "identity",
            b'data: {"id":7,"result":{}}\n',
            "interrupted",
            "MCP response ended before a matching result was received",
        ),
        (
            "application/json",
            "identity",
            b'{"id":7,"result":',
            "interrupted",
            "Invalid MCP response JSON",
        ),
    ],
)
def test_proxy_forwards_exact_bytes_and_classifies_payload(
    http_proxy,
    monkeypatch,
    media_type,
    encoding,
    payload,
    expected_status,
    expected_error,
):
    port, store = http_proxy
    wire_bytes = gzip.compress(payload) if encoding == "gzip" else payload

    def respond(request, timeout):
        return HTTPError(
            request.full_url,
            200,
            "OK",
            {
                "Content-Type": media_type,
                "Content-Encoding": encoding,
            },
            BytesIO(wire_bytes),
        )

    monkeypatch.setattr("urllib.request.urlopen", respond)
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("POST", "/mcp", body=REQUEST)
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == wire_bytes
        assert response.getheader("Content-Encoding") == encoding
    finally:
        connection.close()
    trace = store.list_traces()[0]
    detail = store.get_trace(trace["id"])
    assert detail["status"] == detail["spans"][0]["status"] == expected_status
    assert detail["error"] == expected_error
    assert detail["spans"][0]["response"]["http_status"] == 200
    if b"private failure" in payload:
        assert detail["spans"][0]["metadata"]["error_details"][0]["message"] == "private failure"
    else:
        assert "private failure" not in json.dumps(detail)


def test_sse_final_result_is_recorded_before_the_stream_closes(http_proxy, monkeypatch):
    port, store = http_proxy
    delivered = threading.Event()
    release = threading.Event()
    payload = b'data: {"id":7,"result":{"isError":true}}\n\n'

    class Response:
        status = 200
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            raise AssertionError("Streaming forwarding must use read1")

        def read1(self, size):
            if not delivered.is_set():
                delivered.set()
                return payload
            assert release.wait(5)
            raise TimeoutError("timeout after final result")

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("POST", "/mcp", body=REQUEST)
        response = connection.getresponse()
        assert response.read(len(payload)) == payload
        # The next read starts only after the inspector and recorder finish this frame.
        for _ in range(100):
            errors = store.list_traces(status="error")
            if errors:
                break
            threading.Event().wait(0.01)
        assert len(errors) == 1
        assert errors[0]["error"] == "MCP tool returned isError"
        release.set()
        response.read()
    finally:
        release.set()
        connection.close()
    assert store.get_trace(errors[0]["id"])["error"] == "MCP tool returned isError"
    assert len(store.get_trace(errors[0]["id"])["spans"]) == 1


def test_http_cancellation_is_session_scoped_and_late_responses_do_not_overwrite_it(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    recorder = MCPTraceRecorder(application="test", server="test", transport="http", store=store)
    first, second = [
        recorder.record_http_call(
            request_body=REQUEST,
            request_headers={"Mcp-Session-Id": session},
            target_url="https://example.com/mcp",
        )
        for session in ("private-session-a", "private-session-b")
    ]
    cancel = recorder.record_http_call(
        request_body=b'{"method":"notifications/cancelled","params":{"requestId":7}}',
        request_headers={"Mcp-Session-Id": "private-session-a"},
        target_url="https://example.com/mcp",
    )
    recorder.finish_http_call(cancel, status_code=202, response_bytes=0)
    assert store.get_trace(first.trace.trace_id)["status"] == "interrupted"
    assert store.get_trace(second.trace.trace_id)["status"] == "running"
    recorder.finish_http_call(first, status_code=200, response_bytes=10)
    assert store.get_trace(first.trace.trace_id)["status"] == "interrupted"
    recorder.finish_http_call(second, status_code=200, response_bytes=10)
    detail = store.get_trace(first.trace.trace_id)
    assert len(detail["spans"]) == 1
    assert "success" not in detail["spans"][0]["metadata"]
    assert "private-session" not in json.dumps(detail)


def test_client_disconnect_is_an_interrupted_recording(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    recorder = MCPTraceRecorder(application="test", server="test", transport="http", store=store)
    call = recorder.record_http_call(
        request_body=REQUEST, request_headers={}, target_url="https://example.com/mcp"
    )
    recorder.finish_http_call(call, status_code=200, response_bytes=0, error=BrokenPipeError())
    detail = store.get_trace(call.trace.trace_id)
    assert detail["status"] == detail["spans"][0]["status"] == "interrupted"
    assert "cancelled" not in detail["error"]


def test_oversized_responses_are_forwarded_even_when_inspection_stops(http_proxy, monkeypatch):
    port, store = http_proxy
    payload = json.dumps({"id": 7, "result": {"text": "private" * 100}}).encode()
    monkeypatch.setattr(
        "nextrace.mcp_proxy.HTTPResponseInspector", partial(HTTPResponseInspector, max_bytes=64)
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: HTTPError(
            request.full_url,
            200,
            "OK",
            {"Content-Type": "application/json"},
            BytesIO(payload),
        ),
    )
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("POST", "/mcp", body=REQUEST)
        assert connection.getresponse().read() == payload
    finally:
        connection.close()
    trace = store.list_traces()[0]
    assert trace["status"] == "interrupted"
    assert trace["error"] == "MCP response inspection limit exceeded"
    assert "private" not in json.dumps(store.get_trace(trace["id"]))


def test_stdio_cancellation_finishes_once_and_shutdown_interrupts_remaining_requests(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    recorder = MCPTraceRecorder(application="test", server="test", transport="stdio", store=store)
    for request_id in (1, 2):
        recorder.observe_client_json(
            {"id": request_id, "method": "tools/call", "params": {"name": "search"}}
        )
    recorder.observe_client_json({"method": "notifications/cancelled", "params": {"requestId": 1}})
    recorder.observe_server_json({"id": 1, "result": {}})
    recorder.interrupt_pending("MCP server stopped")
    interrupted = store.list_traces(status="interrupted")
    assert len(interrupted) == 2
    assert store.list_traces(status="running") == []
    assert all(len(store.get_trace(trace["id"])["spans"]) == 1 for trace in interrupted)


@pytest.mark.skipif(os.name == "nt", reason="Windows terminate() does not deliver SIGTERM")
def test_sigterm_interrupts_active_proxy_calls_without_waiting_for_upstream(tmp_path):
    started, release = threading.Event(), threading.Event()

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            started.set()
            release.wait(10)

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    server_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    server_thread.start()
    path = tmp_path / "traces.db"
    program = """
import sys
import nextrace.mcp_proxy as proxy
create = proxy.ThreadingHTTPServer
def server(address, handler):
    instance = create(address, handler)
    print(instance.server_port, flush=True)
    return instance
proxy.ThreadingHTTPServer = server
proxy.run_http_proxy(application='test', server='test', target_url=sys.argv[2],
    host='127.0.0.1', port=0, db_path=sys.argv[1])
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            program,
            str(path),
            f"http://127.0.0.1:{upstream.server_port}/mcp",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    client_thread = None
    try:
        port = int(child.stdout.readline())

        def request():
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("POST", "/mcp", body=REQUEST)
                connection.getresponse().read()
            except (RemoteDisconnected, ConnectionResetError):
                pass
            finally:
                connection.close()

        client_thread = threading.Thread(target=request, daemon=True)
        client_thread.start()
        assert started.wait(5)
        store = SQLiteStore(path)
        assert len(store.list_traces(status="running")) == 1
        child.terminate()
        child.communicate(timeout=5)
        trace = store.list_traces()[0]
        assert trace["status"] == "interrupted"
        assert trace["ended_at"] is not None
    finally:
        release.set()
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
        if client_thread:
            client_thread.join(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        server_thread.join(timeout=5)
