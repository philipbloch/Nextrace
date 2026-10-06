import gzip
import json
import zlib

import pytest

from nextrace.mcp_protocol import MAX_ERROR_TEXT, HTTPResponseInspector, response_error_details


@pytest.mark.parametrize("media_type", ["application/json", "text/event-stream"])
@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"result": {"isError": False, "content": []}}, None),
        (
            {"result": {"isError": True, "content": [{"text": "private failure"}]}},
            "MCP tool returned isError",
        ),
        ({"error": {"code": -32603, "message": "private failure"}}, "MCP JSON-RPC error (-32603)"),
    ],
)
def test_json_and_sse_classify_only_the_matching_response(media_type, payload, expected):
    message = json.dumps({"jsonrpc": "2.0", "id": 7, **payload}).encode()
    if media_type == "text/event-stream":
        message = (
            b"\xef\xbb\xbf: keepalive\r\nid: event-1\r\ndata:\r\n\r\n"
            b'data: {"jsonrpc":"2.0","id":99,"error":{"code":-1}}\r\n\r\n'
            b'data: {"method":"notifications/progress","params":{"progress":1}}\r\n\r\n'
            b"data: " + message + b"\r\n\r\n"
        )
    inspector = HTTPResponseInspector({"Content-Type": media_type + "; charset=utf-8"}, ("7",))
    for byte in message:
        inspector.feed(bytes([byte]))
    inspector.finish()
    assert inspector.complete
    assert inspector.error == expected
    if expected is None:
        assert inspector.error_details == []
    else:
        assert inspector.error_details[0]["message"] == "private failure"
        assert inspector.error_details[0]["correlation_ids"]["jsonrpc_id"] == 7
    assert not inspector._buffer and not inspector._event


@pytest.mark.parametrize("separator", [b"\n", b"\r", b"\r\n"])
def test_sse_multiline_data_and_utf8_split_across_chunks(separator):
    message = separator.join(
        [
            b'data: {"jsonrpc":"2.0","id":"request",',
            'data: "result":{"text":"café ☕"}}'.encode(),
            b"",
            b"",
        ]
    )
    inspector = HTTPResponseInspector({"content-type": "text/event-stream"}, ('"request"',))
    for i in range(0, len(message), 2):
        inspector.feed(message[i : i + 2])
    inspector.finish()
    assert inspector.complete
    assert inspector.error is None


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
def test_compressed_response(encoding, compress):
    message = compress(b'{"jsonrpc":"2.0","id":1,"result":{"isError":true}}')
    inspector = HTTPResponseInspector(
        {
            "content-type": "application/json",
            "content-encoding": encoding,
        },
        ("1",),
    )
    for i in range(0, len(message), 3):
        inspector.feed(message[i : i + 3])
    inspector.finish()
    assert inspector.complete
    assert inspector.error == "MCP tool returned isError"


@pytest.mark.parametrize(
    "media_type,payload",
    [
        ("application/json", b'{"id":1,"result":'),
        ("application/json", b'{"id":"1","result":{}}'),
        ("text/event-stream", b'data: {"id":1,"result":{}}\n'),
        ("text/event-stream", b'data: {"method":"notifications/progress"}\n\n'),
    ],
)
def test_truncated_or_unrelated_messages_never_complete_request(media_type, payload):
    inspector = HTTPResponseInspector({"content-type": media_type}, ("1",))
    inspector.feed(payload)
    inspector.finish()
    assert not inspector.complete


def test_response_limits_stop_inspection_without_retaining_payload():
    inspector = HTTPResponseInspector({"content-type": "application/json"}, ("1",), max_bytes=64)
    inspector.feed(b"x" * 65)
    inspector.feed(b"x" * 1000)
    inspector.finish()
    assert not inspector.complete
    assert inspector.problem == "MCP response inspection limit exceeded"
    assert not inspector._buffer


def test_json_batch_requires_every_expected_id_and_ignores_server_requests():
    inspector = HTTPResponseInspector({"content-type": "application/json"}, ("1", "2"))
    inspector.feed(
        json.dumps(
            [
                {"id": 1, "method": "sampling/createMessage", "params": {}},
                {"id": 2, "result": {}},
            ]
        ).encode()
    )
    inspector.finish()
    assert not inspector.complete
    assert inspector.matched_ids == {"2"}


def test_malformed_sse_event_does_not_hide_a_later_matching_failure():
    inspector = HTTPResponseInspector({"content-type": "text/event-stream"}, ("1",))
    inspector.feed(b"data: not JSON\n\n")
    inspector.feed(b'data: {"id":1,"result":{"isError":true}}\n\n')
    inspector.finish()
    assert inspector.complete
    assert inspector.error == "MCP tool returned isError"


def test_json_rpc_parse_failure_can_have_a_null_id():
    inspector = HTTPResponseInspector({"content-type": "application/json"}, ("1",))
    inspector.feed(b'{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"private input"}}')
    inspector.finish()
    assert inspector.error == "MCP JSON-RPC error (-32700)"
    assert not inspector.complete


def test_structured_error_captures_only_diagnostic_fields_and_redacts_credentials():
    details = response_error_details(
        {
            "id": "request-7",
            "result": {
                "isError": True,
                "content": [{"type": "image", "data": "private-image"}],
                "structuredContent": {
                    "error": {
                        "message": "Page not found. token=private-token",
                        "code": "NOT_FOUND",
                    },
                    "requestId": "upstream-123",
                    "authorization": "private-auth",
                    "payload": "private-data",
                },
            },
        }
    )
    assert details["message"] == "Page not found. token=[REDACTED]"
    assert details["code"] == "NOT_FOUND"
    assert details["correlation_ids"] == {"jsonrpc_id": "request-7", "requestId": "upstream-123"}
    assert "private" not in json.dumps(details)


def test_error_message_limit_and_json_content_codes():
    details = response_error_details(
        {
            "id": 0,
            "result": {
                "isError": True,
                "content": [
                    {"type": "text", "text": '{"error":{"code":"MISSING"},"trace_id":"abc"}'},
                    {"type": "text", "text": "x" * 20_000},
                ],
            },
        }
    )
    assert len(details["message"]) == 16_384
    assert details["message_truncated"] is True
    assert details["code"] == "MISSING"
    assert details["correlation_ids"] == {"jsonrpc_id": 0, "trace_id": "abc"}


@pytest.mark.parametrize("quote", ['"', "'"])
def test_message_limit_does_not_expose_a_partially_captured_credential(quote):
    text = "x" * (MAX_ERROR_TEXT - 80) + f" password={quote}" + "private-value " * 100 + quote
    details = response_error_details(
        {
            "id": 1,
            "result": {"isError": True, "content": [{"type": "text", "text": text}]},
        }
    )
    assert details["message_truncated"] is True
    assert "password=[REDACTED]" in details["message"]
    assert "private-value" not in details["message"]
    assert len(details["message"]) <= MAX_ERROR_TEXT


def test_batch_failures_keep_their_own_messages_codes_and_ids():
    inspector = HTTPResponseInspector({"Content-Type": "application/json"}, ("1", "2"))
    inspector.feed(
        json.dumps(
            [
                {"id": 99, "error": {"message": "unrelated failure"}},
                {"id": 1, "error": {"code": -32602, "message": "Invalid parameters"}},
                {"id": 2, "result": {"isError": True, "content": [{"text": "Page not found"}]}},
            ]
        ).encode()
    )
    inspector.finish()
    assert inspector.complete
    assert [item["message"] for item in inspector.error_details] == [
        "Invalid parameters",
        "Page not found",
    ]
    assert [item["correlation_ids"]["jsonrpc_id"] for item in inspector.error_details] == [1, 2]


@pytest.mark.parametrize(
    "media_type,payload,message,code",
    [
        ("text/plain", b"Access denied", "Access denied", None),
        (
            "application/problem+json",
            b'{"detail":"Access denied","code":"DENIED","requestId":"r1"}',
            "Access denied",
            "DENIED",
        ),
        (
            "application/json",
            b'{"error":{"message":"Access denied","code":0,"data":{"trace_id":"trace-1"}},"requestId":"r1","payload":"private-data"}',
            "Access denied",
            0,
        ),
        ("application/json", b'{"error":"Access denied"}', "Access denied", None),
    ],
)
def test_http_error_body_is_inspected_without_a_jsonrpc_request(media_type, payload, message, code):
    inspector = HTTPResponseInspector({"Content-Type": media_type}, (), status_code=403)
    inspector.feed(payload)
    inspector.finish()
    assert inspector.error_details[0]["message"] == message
    assert inspector.error_details[0].get("code") == code
    assert "private-data" not in json.dumps(inspector.error_details)
    if code == 0:
        assert inspector.error_details[0]["correlation_ids"] == {
            "requestId": "r1",
            "trace_id": "trace-1",
        }


def test_malformed_urls_and_secrets_do_not_break_error_capture():
    details = response_error_details(
        {
            "id": 1,
            "error": {
                "message": 'Failed https://[broken Bearer private-token api_key="private-key" https://user:private-password@example.com/api?token=private-query',
                "data": {"correlation_id": "c1", "cookie": "private-cookie"},
            },
        }
    )
    assert "private" not in json.dumps(details)
    assert details["correlation_ids"]["correlation_id"] == "c1"
