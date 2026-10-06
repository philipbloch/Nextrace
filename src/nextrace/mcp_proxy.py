from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO

from nextrace import SQLiteStore, Trace
from nextrace.context import default_db_path
from nextrace.core import error_status
from nextrace.integrations._utils import is_sensitive_key, redact_url
from nextrace.mcp_protocol import (
    HTTPResponseInspector,
    correlation_ids,
    diagnostic_text,
    jsonrpc_id_key,
    message_params,
    response_error,
    response_error_details,
)
from nextrace.project import read_current_project, resolve_application

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
    summary: dict[str, Any] = {}
    for key, value in headers.items():
        if is_sensitive_key(key):
            summary[key] = "[REDACTED]"
        else:
            summary[key] = {"type": "string", "length": len(value)}
    return summary


@dataclass
class PendingMCPCall:
    request_id: str
    method: str
    name: str
    arguments: Any
    metadata: dict[str, Any]
    trace: Trace
    started_at: float
    response_ids: tuple[str, ...] = ()
    http_scope: str | None = field(default=None, repr=False)


class MCPTraceRecorder:
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
        self._http_pending: dict[str, PendingMCPCall] = {}
        self._lock = threading.RLock()
        self._connected_applications: set[str] = set()
        self._current_application()

    def observe_client_json(self, message: Any) -> None:
        if not isinstance(message, dict):
            self._record_notification(
                "jsonrpc.batch", {"batch_length": len(message)} if isinstance(message, list) else {}
            )
            return
        method = message.get("method")
        if not method:
            return
        request_id = message.get("id")
        if request_id is None:
            if method == "notifications/cancelled":
                params = message_params(message)
                with self._lock:
                    cancelled = self._pending.pop(jsonrpc_id_key(params.get("requestId")), None)
                if cancelled is not None:
                    self._finish_call(
                        cancelled,
                        {"response": "missing"},
                        InterruptedError("MCP client requested cancellation"),
                    )
            self._record_notification(method, self._metadata_for_message(message))
            return

        pending = self._start_call(
            message,
            metadata={
                "request_id": summarize_json(request_id),
                "direction": "client_to_server",
            },
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
        details = response_error_details(message)
        if details is not None:
            pending.metadata["error_details"] = [details]
        self._finish_call(pending, self._response_summary(message), response_error(message))

    def _start_call(self, message: dict[str, Any], *, metadata: dict[str, Any]) -> PendingMCPCall:
        name = self._span_name(message)
        method = str(message.get("method") or "http.request")
        trace = self._new_trace(name=name, jsonrpc_method=method)
        context = message_params(message).get("_meta", {})
        context = context.get("nextrace", {}) if isinstance(context, dict) else {}
        headers = metadata.pop("correlation_headers", {})
        metadata["correlation_ids"] = {
            **correlation_ids({"jsonrpc_id": message.get("id")}),
            **correlation_ids(message_params(message).get("_meta")),
            **correlation_ids(headers),
        }
        if not isinstance(context, dict):
            context = {}

        def context_value(key):
            value = context.get(key) or headers.get("x-nextrace-" + key.replace("_", "-"))
            return value if isinstance(value, str) and 0 < len(value) <= 512 else None

        session_id = (
            context_value("session_id") or self.session_id or os.getenv("NEXTRACE_SESSION_ID")
        )
        turn_id = context_value("turn_id") or os.getenv("NEXTRACE_TURN_ID")
        if session_id:
            trace.session_id = session_id
        if session_id and turn_id:
            trace.turn_id = turn_id
        cwd = context_value("project_path") or read_current_project().get("project_path")
        if isinstance(cwd, str) and cwd:
            trace.metadata["cwd"] = str(Path(cwd).expanduser().resolve())
        if "http_method" in metadata:
            trace.metadata["http_method"] = metadata["http_method"]
        trace.__enter__()
        return PendingMCPCall(
            request_id=jsonrpc_id_key(message.get("id")),
            method=method,
            name=name,
            arguments=self._arguments_for_message(message),
            metadata={**self._metadata_for_message(message), **metadata},
            trace=trace,
            started_at=time.perf_counter(),
        )

    def _finish_call(
        self,
        pending: PendingMCPCall,
        result: Any,
        error: BaseException | str | None,
        *,
        kind: str = "tool",
    ) -> None:
        status = error_status(error) if error is not None else "ok"
        metadata = dict(pending.metadata)
        if isinstance(error, BaseException):
            metadata["exception_type"] = type(error).__name__
            metadata["traceback"] = diagnostic_text(
                "".join(traceback.format_exception(type(error), error, error.__traceback__))
            )
        if kind == "tool" and status != "interrupted":
            metadata["success"] = error is None
        pending.trace.status = status
        pending.trace.error = diagnostic_text(str(error)) if error is not None else None
        for key in (
            "error_details",
            "correlation_ids",
            "exception_type",
            "traceback",
            "http_status",
        ):
            if key in metadata:
                pending.trace.metadata[key] = metadata[key]
        pending.trace.record_span(
            kind=kind,
            name=pending.name,
            prompt=pending.arguments,
            response=result,
            latency_ms=(time.perf_counter() - pending.started_at) * 1000,
            metadata=metadata,
            error=InterruptedError(pending.trace.error)
            if status == "interrupted"
            else pending.trace.error,
        )
        pending.trace.__exit__(None, None, None)

    def record_http_call(
        self,
        *,
        request_body: bytes,
        request_headers: dict[str, str],
        target_url: str,
        http_method: str = "POST",
    ) -> PendingMCPCall:
        http_method = http_method.upper()
        headers = {key.lower(): value for key, value in request_headers.items()}
        message = decode_json_bytes(request_body)
        representative = message[0] if isinstance(message, list) and message else message
        if not isinstance(representative, dict):
            representative = {"method": "http.request", "params": {}}
        pending = self._start_call(
            representative,
            metadata={
                "correlation_headers": headers,
                "direction": "client_to_http_server",
                "http_target": redact_url(target_url),
                "http_method": http_method,
                "headers": summarize_headers(request_headers),
                "batch_length": len(message) if isinstance(message, list) else None,
            },
        )
        messages = message if isinstance(message, list) else [representative]
        pending.response_ids = tuple(
            jsonrpc_id_key(item["id"])
            for item in messages
            if isinstance(item, dict) and "method" in item and item.get("id") is not None
        )
        session = headers.get("mcp-session-id")
        if session:
            identity = headers.get("authorization", "") + "\0" + session
            pending.http_scope = hashlib.sha256(identity.encode()).hexdigest()
        with self._lock:
            self._http_pending[pending.trace.trace_id] = pending
        if pending.method == "notifications/cancelled":
            self._cancel_http_calls(message_params(representative).get("requestId"), pending)
        return pending

    def _cancel_http_calls(self, request_id: Any, notification: PendingMCPCall) -> None:
        if request_id is None or notification.http_scope is None:
            return
        request_key = jsonrpc_id_key(request_id)
        with self._lock:
            cancelled = [
                call
                for call in self._http_pending.values()
                if call.http_scope == notification.http_scope
                and request_key in call.response_ids
                and call is not notification
            ]
        for call in cancelled:
            self.finish_http_call(
                call,
                status_code=None,
                response_bytes=None,
                error=InterruptedError("MCP client requested cancellation"),
            )

    def finish_http_call(
        self,
        pending: PendingMCPCall,
        *,
        status_code: int | None,
        response_bytes: int | None,
        response_headers: dict[str, str] | None = None,
        inspection: HTTPResponseInspector | None = None,
        error: BaseException | str | None = None,
    ) -> None:
        with self._lock:
            if self._http_pending.pop(pending.trace.trace_id, None) is None:
                return
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            error = InterruptedError("Connection closed before recording the complete response")
        if (
            error is None
            and inspection is not None
            and status_code is not None
            and status_code < 400
        ):
            if inspection.error is not None:
                error = inspection.error
            elif pending.response_ids and not inspection.complete:
                error = InterruptedError(
                    inspection.problem or "MCP response ended before a matching result was received"
                )
        http_method = pending.metadata["http_method"]
        is_transport = pending.method == "http.request" and http_method in {
            "GET",
            "DELETE",
            "OPTIONS",
        }
        # MCP permits 405 for the optional event stream and session cleanup.
        outcome = None
        if error is None and is_transport and status_code == 405:
            outcome = {
                "GET": "event_stream_not_supported",
                "DELETE": "session_termination_not_supported",
            }.get(http_method)
        if error is None and outcome is None and (status_code is None or status_code >= 400):
            error = f"HTTP {status_code if status_code is not None else 'unknown'}"
        result = {
            "http_method": http_method,
            "http_status": status_code,
            "response_bytes": response_bytes,
            "headers": summarize_headers(response_headers or {}),
        }
        pending.metadata["http_status"] = status_code
        pending.metadata["correlation_ids"].update(correlation_ids(response_headers))
        if inspection is not None and pending.response_ids:
            result["mcp_response_complete"] = inspection.complete
            if inspection.problem:
                result["inspection_problem"] = inspection.problem
            if inspection.error:
                result["mcp_error"] = inspection.error
        if inspection is not None and inspection.error_details and error is not None:
            pending.metadata["error_details"] = inspection.error_details
        if outcome is not None:
            result["transport_outcome"] = outcome
            pending.metadata["transport_outcome"] = outcome
            pending.trace.metadata["transport_outcome"] = outcome
        self._finish_call(pending, result, error, kind="transport" if is_transport else "tool")

    def interrupt_pending(self, error: BaseException | str) -> None:
        interruption = (
            error if isinstance(error, InterruptedError) else InterruptedError(str(error))
        )
        with self._lock:
            pending_calls = list(self._pending.values())
            self._pending.clear()
            http_calls = list(self._http_pending.values())
        for pending in pending_calls:
            self._finish_call(pending, {"response": "missing"}, interruption)
        for pending in http_calls:
            self.finish_http_call(
                pending, status_code=None, response_bytes=None, error=interruption
            )

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
        params = message_params(message)
        if method == "tools/call" and params.get("name"):
            return f"tool:{params['name']}"
        if method == "resources/read" and params.get("uri"):
            return "resource:read"
        if method == "prompts/get" and params.get("name"):
            return f"prompt:{params['name']}"
        return method

    def _arguments_for_message(self, message: dict[str, Any]) -> Any:
        params = message_params(message)
        if message.get("method") == "tools/call":
            return summarize_json(params.get("arguments", {}))
        return summarize_json(params)

    def _metadata_for_message(self, message: dict[str, Any]) -> dict[str, Any]:
        params = message_params(message)
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


def decode_json_bytes(body: bytes) -> Any | None:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
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

    threads = [
        threading.Thread(target=client_to_child, daemon=True),
        threading.Thread(
            target=copy_json_lines,
            kwargs={
                "source": process.stdout,
                "target": sys.stdout.buffer,
                "observer": recorder.observe_server_json,
            },
            daemon=True,
        ),
        threading.Thread(target=copy_bytes, args=(process.stderr, sys.stderr.buffer), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        with _shutdown_signals():
            return_code = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait()
    for thread in threads[1:]:
        thread.join(timeout=1)
    if return_code != 0:
        recorder.interrupt_pending(f"MCP child exited with code {return_code}")
    else:
        recorder.interrupt_pending("MCP child exited before response")
    return return_code


def _observe(observer, *args, **kwargs):
    try:
        return observer(*args, **kwargs)
    except Exception as exc:
        # Observability must not interrupt traffic to the MCP server.
        print(f"nextrace recorder: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def copy_json_lines(*, source: BinaryIO, target: BinaryIO, observer: Any) -> None:
    while line := source.readline():
        message = decode_json_bytes(line)
        if message is not None:
            _observe(observer, message)
        target.write(line)
        target.flush()


def copy_bytes(source: BinaryIO, target: BinaryIO) -> None:
    while chunk := source.read(65536):
        target.write(chunk)
        target.flush()


@contextmanager
def _shutdown_signals():
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def stop(_signal, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


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
            pending = _observe(
                recorder.record_http_call,
                request_body=body,
                request_headers=incoming_headers,
                target_url=destination,
                http_method=self.command,
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
            error = None
            inspection = None

            def record_response(error=None):
                if pending is not None:
                    _observe(
                        recorder.finish_http_call,
                        pending,
                        status_code=status_code,
                        response_bytes=response_bytes,
                        response_headers=response_headers,
                        inspection=inspection,
                        error=error,
                    )

            try:
                try:
                    response = urllib.request.urlopen(request, timeout=timeout)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    status_code = int(response.status)
                    response_headers = dict(response.headers.items())
                    if pending is not None:
                        inspection = HTTPResponseInspector(
                            response_headers, pending.response_ids, status_code=status_code
                        )
                    self.send_response(status_code)
                    for key, value in response.headers.items():
                        if key.lower() not in HOP_BY_HOP_HEADERS:
                            self.send_header(key, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    read_chunk = getattr(response, "read1", response.read)
                    while chunk := read_chunk(65536):
                        response_bytes += len(chunk)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        if inspection is not None:
                            _observe(inspection.feed, chunk)
                            if inspection.complete:
                                record_response()
                    if inspection is not None:
                        _observe(inspection.finish)
            except Exception as exc:
                error = exc
                if status_code is None:
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    payload = json.dumps({"error": "MCP proxy upstream request failed"}).encode(
                        "utf-8"
                    )
                    self.wfile.write(payload)
                    response_bytes = len(payload)
                    status_code = 502
            finally:
                record_response(error)

        do_GET = do_POST = do_DELETE = do_OPTIONS = _proxy

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(
        f"Nextrace MCP HTTP proxy: http://{host}:{port} -> {redact_url(target_url)}",
        file=sys.stderr,
    )
    try:
        with _shutdown_signals():
            httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        _observe(
            recorder.interrupt_pending,
            "MCP HTTP proxy stopped before completing the response",
        )
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
    return urllib.parse.urlunsplit(
        (target.scheme, target.netloc, target.path, query, target.fragment)
    )
