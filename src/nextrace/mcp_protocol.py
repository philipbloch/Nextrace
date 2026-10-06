from __future__ import annotations

import json
import re
import zlib
from typing import Any

from nextrace.integrations._utils import redact_url

MAX_ERROR_TEXT = 16_384
CORRELATION_KEYS = {
    "requestid",
    "xrequestid",
    "correlationid",
    "xcorrelationid",
    "traceid",
    "xtraceid",
    "spanid",
    "traceparent",
    "jsonrpcid",
}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?(?:authorization|cookie|(?:access_|refresh_|session_)?token|"
    r"api[_-]?key|client[_-]?secret|password)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*(?:\"|$)|'[^']*(?:'|$)|(?:Bearer\s+)?[^\s,;}\]]+)"
)


def diagnostic_text(value: str, *, limit: int = MAX_ERROR_TEXT) -> str:
    value = SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    value = re.sub(r"https?://[^\s<>\"']+", _redact_diagnostic_url, value)
    return value[:limit]


def _redact_diagnostic_url(match: re.Match) -> str:
    try:
        return redact_url(match.group())
    except ValueError:
        return "[INVALID URL]"


def correlation_ids(values: Any) -> dict[str, str | int]:
    if not isinstance(values, dict):
        return {}
    return {
        key: diagnostic_text(value, limit=512) if isinstance(value, str) else value
        for key, value in values.items()
        if isinstance(key, str)
        and re.sub(r"[_-]", "", key).lower() in CORRELATION_KEYS
        and type(value) in {str, int}
        and value != ""
    }


def jsonrpc_id_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def message_params(message: dict[str, Any]) -> dict[str, Any]:
    params = message.get("params")
    return params if isinstance(params, dict) else {}


def response_error(message: dict[str, Any]) -> str | None:
    error = message.get("error")
    if error is not None:
        code = error.get("code") if isinstance(error, dict) else None
        suffix = f" ({code})" if type(code) is int else ""
        return f"MCP JSON-RPC error{suffix}"
    result = message.get("result")
    if isinstance(result, dict) and result.get("isError") is True:
        return "MCP tool returned isError"
    return None


def _error_details(category: str, texts: list[Any], sources: list[Any]) -> dict[str, Any]:
    sources = [source for source in sources if isinstance(source, dict)]
    for source in tuple(sources):
        error = source.get("error")
        if isinstance(error, dict):
            sources.append(error)
            source = error
        if isinstance(source.get("data"), dict):
            sources.append(source["data"])
    code = next(
        (source["code"] for source in sources if type(source.get("code")) in {str, int}),
        None,
    )
    if not any(isinstance(text, str) and text for text in texts):
        texts = [source.get("message") for source in sources]
    # Bound persisted diagnostics independently of the response inspection buffer.
    parts = []
    remaining = MAX_ERROR_TEXT
    truncated = False
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        if remaining == 0:
            truncated = True
            break
        separator = "\n\n" if parts else ""
        part = separator + text
        truncated |= len(part) > remaining
        parts.append(part[:remaining])
        remaining = max(0, remaining - len(part))
    identifiers = {}
    for source in sources:
        identifiers.update(correlation_ids(source))
    details = {"category": category, "correlation_ids": identifiers}
    if parts:
        details["message"] = diagnostic_text("".join(parts))
    if code is not None:
        details["code"] = diagnostic_text(code, limit=256) if isinstance(code, str) else code
    if truncated:
        details["message_truncated"] = True
    return details


def response_error_details(message: dict[str, Any]) -> dict[str, Any] | None:
    if response_error(message) is None:
        return None
    error = message.get("error")
    sources = [{"jsonrpc_id": message.get("id")}, message, message.get("_meta")]
    if error is not None:
        text = error.get("message") if isinstance(error, dict) else error
        return _error_details("jsonrpc", [text], sources)

    result = message["result"]
    sources.extend([result, result.get("_meta"), result.get("structuredContent")])
    texts = []
    content = result.get("content")
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type", "text") != "text":
            continue
        text = block.get("text")
        texts.append(text)
        if isinstance(text, str) and len(text) <= MAX_ERROR_TEXT:
            try:
                sources.append(json.loads(text))
            except (ValueError, RecursionError):
                pass
    return _error_details("mcp_tool", texts, sources)


class HTTPResponseInspector:
    def __init__(
        self,
        headers: dict[str, str],
        request_ids: tuple[str, ...],
        *,
        max_bytes: int = 8 * 1024 * 1024,
        status_code: int | None = None,
    ) -> None:
        headers = {key.lower(): value for key, value in headers.items()}
        self.media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        self.request_ids = set(request_ids)
        self.matched_ids: set[str] = set()
        self.error: str | None = None
        self.error_details: list[dict[str, Any]] = []
        self._http_error = status_code is not None and status_code >= 400
        self.problem: str | None = None
        self.max_bytes = max_bytes
        self._buffer = bytearray()
        self._event = bytearray()
        self._first_line = True
        encoding = headers.get("content-encoding", "identity").strip().lower()
        self._decompressor = None
        if encoding in {"gzip", "deflate"}:
            self._decompressor = zlib.decompressobj(31 if encoding == "gzip" else 15)
        elif encoding not in {"", "identity"}:
            self.problem = "Unsupported MCP response content encoding"
        supported_types = {"application/json", "text/event-stream"}
        if self._http_error:
            supported_types.update({"text/plain", "application/problem+json"})
        if self.media_type not in supported_types:
            self.problem = "Unsupported MCP response content type"
        self._disabled = self.problem is not None

    @property
    def complete(self) -> bool:
        return bool(self.request_ids) and self.request_ids <= self.matched_ids

    def feed(self, chunk: bytes) -> None:
        if self._disabled or (not self.request_ids and not self._http_error):
            return
        if self._decompressor is not None:
            try:
                chunk = self._decompressor.decompress(chunk, self.max_bytes + 1)
            except zlib.error:
                self._stop("Invalid compressed MCP response")
                return
            if self._decompressor.unconsumed_tail:
                self._stop("MCP response inspection limit exceeded")
                return
        if len(self._buffer) + len(self._event) + len(chunk) > self.max_bytes:
            self._stop("MCP response inspection limit exceeded")
            return
        self._buffer.extend(chunk)
        if self.media_type == "text/event-stream":
            self._read_lines()

    def finish(self) -> None:
        try:
            if self._disabled or (not self.request_ids and not self._http_error):
                return
            if self._decompressor is not None and not self._decompressor.eof:
                self._stop("Compressed MCP response ended early")
                return
            if self.media_type == "text/plain":
                text = self._buffer.decode("utf-8", errors="replace")
                if text:
                    self.error_details.append(_error_details("http", [text], []))
            elif self.media_type in {"application/json", "application/problem+json"}:
                self._read_message(bytes(self._buffer))
            else:
                self._read_lines(final=True)
        finally:
            self._buffer.clear()
            self._event.clear()

    def _read_lines(self, *, final: bool = False) -> None:
        consumed = 0
        for match in re.finditer(rb"\r\n|\r|\n", self._buffer):
            # A CR at a chunk boundary may be the first half of CRLF.
            if not final and match.group() == b"\r" and match.end() == len(self._buffer):
                break
            line = bytes(self._buffer[consumed : match.start()])
            consumed = match.end()
            if self._first_line:
                line = line.removeprefix(b"\xef\xbb\xbf")
                self._first_line = False
            if not line:
                if self._event.strip():
                    self._read_message(bytes(self._event))
                self._event.clear()
                continue
            field, _, value = line.partition(b":")
            if field == b"data":
                self._event.extend(value.removeprefix(b" ") + b"\n")
        del self._buffer[:consumed]
        # SSE dispatches only at a blank line; unfinished events at EOF are discarded.

    def _read_message(self, payload: bytes) -> None:
        try:
            message = json.loads(payload)
        except (ValueError, RecursionError):
            self.problem = (
                "Invalid HTTP error JSON" if self._http_error else "Invalid MCP response JSON"
            )
            return
        messages = message if isinstance(message, list) else [message]
        for item in messages:
            if not isinstance(item, dict) or "method" in item:
                continue
            if "id" not in item or not ({"result", "error"} & item.keys()):
                continue
            # A JSON-RPC parse failure cannot recover an ID; the HTTP envelope
            # still identifies the failed request. Unrelated SSE IDs stay ignored.
            request_id = jsonrpc_id_key(item["id"])
            already_matched = request_id in self.matched_ids
            if request_id in self.request_ids:
                self.matched_ids.add(request_id)
            elif not (
                self.media_type == "application/json" and item["id"] is None and "error" in item
            ):
                continue
            self.error = self.error or response_error(item)
            if not already_matched:
                details = response_error_details(item)
                if details is not None:
                    self.error_details.append(details)
        if (
            self._http_error
            and self.media_type != "text/event-stream"
            and not self.error_details
            and isinstance(message, dict)
        ):
            error = message.get("error")
            source = error if isinstance(error, dict) else message
            text = source.get("message") or source.get("detail") or source.get("title")
            details = _error_details(
                "http", [text or error], [message, source, message.get("_meta")]
            )
            if "message" in details or "code" in details:
                self.error_details.append(details)

    def _stop(self, reason: str) -> None:
        self._disabled = True
        self.problem = reason
        self._buffer.clear()
        self._event.clear()
