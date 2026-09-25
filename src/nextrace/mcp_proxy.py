"""Redacting MCP proxy utilities."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO

from nextrace import SQLiteStore, Trace
from nextrace.context import default_db_path
from nextrace.integrations._utils import is_sensitive_key, redact_url
from nextrace.project import resolve_application

HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def summarize_json(value: Any, *, max_items: int = 8) -> Any:
    """Return a content-safe shape summary for JSON-like data."""
    if isinstance(value, dict):
        summary: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                summary["_truncated_keys"] = len(value) - max_items
                break
            key_text = str(key)
            if is_sensitive_key(key_text):
                summary[key_text] = "[REDACTED]"
            else:
                summary[key_text] = summarize_json(item, max_items=max_items)
        return summary
    if isinstance(value, list):
        return {
            "type": "list",
            "length": len(value),
            "items": [summarize_json(item, max_items=max_items) for item in value[:max_items]],
            **({"truncated_items": len(value) - max_items} if len(value) > max_items else {}),
        }
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return value
    return {"type": type(value).__name__}


def summarize_headers(headers: dict[str, str]) -> dict[str, Any]:
    """Summarize headers while preserving useful non-secret routing hints."""
    summary: dict[str, Any] = {}
    for key, value in headers.items():
        if is_sensitive_key(key):
            summary[key] = "[REDACTED]"
        else:
            summary[key] = {"type": "string", "length": len(value)}
    return summary


def jsonrpc_id_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


@dataclass
class PendingMCPCall:
    request_id: str
    method: str
    name: str
    arguments: Any
    metadata: dict[str, Any]
    trace: Any
    started_at: float


class MCPTraceRecorder:
    """Records MCP JSON-RPC calls without storing raw request or response content."""

    def __init__(
        self,
        *,
        application: str,
        server: str,
        transport: str,
        store: SQLiteStore,
        session_id: str | None = None,
        connection_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.application = application
        self.server = server
        self.transport = transport
        self.store = store
        self.session_id = session_id
        self.connection_metadata = connection_metadata or {}
        self._pending: dict[str, PendingMCPCall] = {}
        self._lock = threading.RLock()
        self._connected_applications: set[str] = set()
        self._current_application()

    def observe_client_json(self, message: Any) -> None:
        if not isinstance(message, dict):
            self._record_notification("jsonrpc.batch", {"batch_length": len(message)} if isinstance(message, list) else {})
            return
        method = message.get("method")
        if not method:
            return
        request_id = message.get("id")
        if request_id is None:
            self._record_notification(method, self._metadata_for_message(message))
            return

        name = self._span_name(message)
        metadata = self._metadata_for_message(message)
        metadata["request_id"] = summarize_json(request_id)
        metadata["direction"] = "client_to_server"
        trace = self._new_trace(name=name, jsonrpc_method=method)
        trace.__enter__()
        pending = PendingMCPCall(
            request_id=jsonrpc_id_key(request_id),
            method=method,
            name=name,
            arguments=self._arguments_for_message(message),
            metadata=metadata,
            trace=trace,
            started_at=time.perf_counter(),
        )
        with self._lock:
            self._pending[pending.request_id] = pending

    def observe_server_json(self, message: Any) -> None:
        if not isinstance(message, dict) or "method" in message:
            return
        if "id" not in message:
            return
        request_id = jsonrpc_id_key(message.get("id"))
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        elapsed_ms = (time.perf_counter() - pending.started_at) * 1000
        error = message.get("error")
        response_summary = self._response_summary(message)
        span_error = summarize_json(error) if error is not None else None
        if span_error is not None:
            pending.trace.status = "error"
            pending.trace.error = str(span_error)
        pending.trace.tool_call(
            name=pending.name,
            arguments=pending.arguments,
            result=response_summary,
            success=span_error is None,
            latency_ms=elapsed_ms,
            metadata=pending.metadata,
            error=span_error,
        )
        pending.trace.__exit__(None, None, None)

    def record_http_call(
        self,
        *,
        request_body: bytes,
        request_headers: dict[str, str],
        target_url: str,
    ) -> PendingMCPCall:
        message = decode_json_bytes(request_body)
        representative = message[0] if isinstance(message, list) and message else message
        if not isinstance(representative, dict):
            representative = {"method": "http.request", "params": {}}
        name = self._span_name(representative)
        method = str(representative.get("method") or "http.request")
        metadata = self._metadata_for_message(representative)
        metadata.update(
            {
                "direction": "client_to_http_server",
                "http_target": redact_url(target_url),
                "headers": summarize_headers(request_headers),
                "batch_length": len(message) if isinstance(message, list) else None,
            }
        )
        trace = self._new_trace(name=name, jsonrpc_method=method)
        trace.__enter__()
        return PendingMCPCall(
            request_id=jsonrpc_id_key(representative.get("id")),
            method=method,
            name=name,
            arguments=self._arguments_for_message(representative),
            metadata=metadata,
            trace=trace,
            started_at=time.perf_counter(),
        )

    def finish_http_call(
        self,
        pending: PendingMCPCall,
        *,
        status_code: int | None,
        response_bytes: int,
        response_headers: dict[str, str] | None = None,
        error: BaseException | str | None = None,
    ) -> None:
        elapsed_ms = (time.perf_counter() - pending.started_at) * 1000
        span_error = error
        if span_error is None and (status_code is None or status_code >= 400):
            span_error = f"HTTP {status_code if status_code is not None else 'unknown'}"
        if span_error is not None:
            pending.trace.status = "error"
            pending.trace.error = str(span_error)
        result = {
            "http_status": status_code,
            "response_bytes": response_bytes,
            "headers": summarize_headers(response_headers or {}),
        }
        pending.trace.tool_call(
            name=pending.name,
            arguments=pending.arguments,
            result=result,
            success=span_error is None,
            latency_ms=elapsed_ms,
            metadata=pending.metadata,
            error=str(span_error) if span_error else None,
        )
        pending.trace.__exit__(None, None, None)

    def finish_pending_with_error(self, error: BaseException | str) -> None:
        with self._lock:
            pending_calls = list(self._pending.values())
            self._pending.clear()
        for pending in pending_calls:
            elapsed_ms = (time.perf_counter() - pending.started_at) * 1000
            pending.trace.status = "error"
            pending.trace.error = str(error)
            pending.trace.tool_call(
                name=pending.name,
                arguments=pending.arguments,
                result={"response": "missing"},
                success=False,
                latency_ms=elapsed_ms,
                metadata=pending.metadata,
                error=str(error),
            )
            pending.trace.__exit__(None, None, None)

    def _record_notification(self, method: str, metadata: dict[str, Any]) -> None:
        with self._new_trace(name=method, jsonrpc_method=method) as trace:
            trace.event(method, metadata, kind="mcp.notification")

    def _new_trace(self, *, name: str, jsonrpc_method: str) -> Trace:
        application = self._current_application()
        return Trace(
            application=application,
            name=f"mcp:{self.server}:{name}",
            session_id=self.session_id,
            tags=["mcp", self.transport, self.server],
            metadata={
                "server": self.server,
                "transport": self.transport,
                "jsonrpc_method": jsonrpc_method,
            },
            store=self.store,
        )

    def _current_application(self) -> str:
        application = resolve_application(self.application)
        self._record_connection(application)
        return application

    def _record_connection(self, application: str) -> None:
        with self._lock:
            if application in self._connected_applications:
                return
            self.store.record_connection(
                application=application,
                source=self.server,
                transport=self.transport,
                metadata=self.connection_metadata,
            )
            self._connected_applications.add(application)

    def _span_name(self, message: dict[str, Any]) -> str:
        method = str(message.get("method") or "unknown")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "tools/call" and params.get("name"):
            return f"tool:{params['name']}"
        if method == "resources/read" and params.get("uri"):
            return "resource:read"
        if method == "prompts/get" and params.get("name"):
            return f"prompt:{params['name']}"
        return method

    def _arguments_for_message(self, message: dict[str, Any]) -> Any:
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if message.get("method") == "tools/call":
            return summarize_json(params.get("arguments", {}))
        return summarize_json(params)

    def _metadata_for_message(self, message: dict[str, Any]) -> dict[str, Any]:
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        metadata = {
            "server": self.server,
            "transport": self.transport,
            "jsonrpc_method": message.get("method"),
        }
        if "name" in params:
            metadata["mcp_name"] = params.get("name")
        if "uri" in params:
            metadata["mcp_uri"] = {"type": "string", "length": len(str(params.get("uri")))}
        return metadata

    def _response_summary(self, message: dict[str, Any]) -> dict[str, Any]:
        if "error" in message:
            return {"error": summarize_json(message["error"])}
        result = message.get("result")
        if isinstance(result, dict):
            summary = {
                "keys": sorted(str(key) for key in result.keys()),
                "summary": summarize_json(result),
            }
            if "content" in result and isinstance(result["content"], list):
                summary["content_items"] = len(result["content"])
            return summary
        return {"summary": summarize_json(result)}


def decode_json_line(line: bytes) -> Any | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def decode_json_bytes(body: bytes) -> Any | None:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def run_stdio_proxy(
    *,
    application: str,
    server: str,
    command: list[str],
    db_path: str | Path | None = None,
    session_id: str | None = None,
) -> int:
    if not command:
        print("nextrace mcp-proxy requires a command after --", file=sys.stderr)
        return 2

    store = SQLiteStore(db_path or default_db_path())
    recorder = MCPTraceRecorder(
        application=application,
        server=server,
        transport="stdio",
        store=store,
        session_id=session_id,
        connection_metadata={"command": summarize_json(command)},
    )
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    def client_to_child() -> None:
        try:
            copy_json_lines(
                source=sys.stdin.buffer,
                target=process.stdin,
                observer=recorder.observe_client_json,
            )
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    def child_to_client() -> None:
        copy_json_lines(
            source=process.stdout,
            target=sys.stdout.buffer,
            observer=recorder.observe_server_json,
        )

    def child_stderr_to_stderr() -> None:
        copy_bytes(process.stderr, sys.stderr.buffer)

    threads = [
        threading.Thread(target=client_to_child, daemon=True),
        threading.Thread(target=child_to_client, daemon=True),
        threading.Thread(target=child_stderr_to_stderr, daemon=True),
    ]
    for thread in threads:
        thread.start()
    return_code = process.wait()
    for thread in threads[1:]:
        thread.join(timeout=1)
    if return_code != 0:
        recorder.finish_pending_with_error(f"MCP child exited with code {return_code}")
    else:
        recorder.finish_pending_with_error("MCP child exited before response")
    return return_code


def copy_json_lines(*, source: BinaryIO, target: BinaryIO, observer: Any) -> None:
    while True:
        line = source.readline()
        if not line:
            break
        message = decode_json_line(line)
        if message is not None:
            try:
                observer(message)
            except Exception as exc:  # pragma: no cover - defensive: proxy must keep traffic flowing.
                print(f"nextrace mcp-proxy recorder error: {type(exc).__name__}: {exc}", file=sys.stderr)
        target.write(line)
        target.flush()


def copy_bytes(source: BinaryIO, target: BinaryIO) -> None:
    while True:
        chunk = source.read(65536)
        if not chunk:
            break
        target.write(chunk)
        target.flush()


def run_http_proxy(
    *,
    application: str,
    server: str,
    target_url: str,
    host: str,
    port: int,
    db_path: str | Path | None = None,
    session_id: str | None = None,
    timeout: float = 3600,
) -> int:
    store = SQLiteStore(db_path or default_db_path())
    recorder = MCPTraceRecorder(
        application=application,
        server=server,
        transport="http",
        store=store,
        session_id=session_id,
        connection_metadata={"target": redact_url(target_url), "local": f"http://{host}:{port}"},
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self._proxy()

        def do_POST(self) -> None:  # noqa: N802
            self._proxy()

        def do_DELETE(self) -> None:  # noqa: N802
            self._proxy()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._proxy()

        def log_message(self, format: str, *args: Any) -> None:
            print(f"nextrace mcp-http-proxy: {format % args}", file=sys.stderr)

        def _proxy(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            incoming_headers = dict(self.headers.items())
            forward_headers = {
                key: value
                for key, value in incoming_headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            }
            destination = build_target_url(target_url, self.path)
            pending = recorder.record_http_call(
                request_body=body,
                request_headers=incoming_headers,
                target_url=destination,
            )
            request = urllib.request.Request(
                destination,
                data=body if self.command not in {"GET", "DELETE"} else None,
                headers=forward_headers,
                method=self.command,
            )
            status_code: int | None = None
            response_headers: dict[str, str] = {}
            response_bytes = 0
            try:
                try:
                    response = urllib.request.urlopen(request, timeout=timeout)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    status_code = int(response.status)
                    response_headers = dict(response.headers.items())
                    self.send_response(status_code)
                    for key, value in response.headers.items():
                        if key.lower() not in HOP_BY_HOP_HEADERS:
                            self.send_header(key, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    while chunk := response.read(65536):
                        response_bytes += len(chunk)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                recorder.finish_http_call(
                    pending,
                    status_code=status_code,
                    response_bytes=response_bytes,
                    response_headers=response_headers,
                )
            except Exception as exc:
                if status_code is None:
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    payload = json.dumps({"error": "MCP proxy upstream request failed"}).encode("utf-8")
                    self.wfile.write(payload)
                    response_bytes = len(payload)
                    status_code = 502
                recorder.finish_http_call(
                    pending,
                    status_code=status_code,
                    response_bytes=response_bytes,
                    response_headers=response_headers,
                    error=exc,
                )

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(
        f"Nextrace MCP HTTP proxy: http://{host}:{port} -> {target_url}",
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        httpd.server_close()
    return 0


def build_target_url(target_url: str, incoming_path: str) -> str:
    target = urllib.parse.urlsplit(target_url)
    incoming = urllib.parse.urlsplit(incoming_path)
    query_parts = []
    if target.query:
        query_parts.append(target.query)
    if incoming.query:
        query_parts.append(incoming.query)
    query = "&".join(query_parts)
    return urllib.parse.urlunsplit((target.scheme, target.netloc, target.path, query, target.fragment))
