import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nextrace import SQLiteStore, ai_trace
from nextrace.cli import main
from nextrace.otel import export_traces, send_otlp, valid_parents


def fixture_trace(store):
    with ai_trace("app", session_id="session", turn_id="turn", store=store) as trace:
        with trace.step("workflow", "work"):
            trace.model_call(
                provider="test",
                model="test",
                prompt="PROMPT-SECRET",
                response="RESPONSE-SECRET",
                input_tokens=20,
                output_tokens=10,
                latency_ms=5,
                metadata={"credential": "AUTH-SECRET"},
            )
    return store.get_trace(trace.trace_id)


def test_otel_export_has_valid_ids_hierarchy_units_status_and_no_raw_payloads(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    detail = fixture_trace(store)
    detail["metadata"]["traceback"] = "TRACEBACK-SECRET"
    detail["spans"][0]["metadata"]["error_details"] = [{"message": "DIAGNOSTIC-SECRET"}]
    detail["spans"][0]["metadata"]["correlation_ids"] = {"request_id": "REQUEST-SECRET"}
    payload = export_traces([detail])
    rendered = json.dumps(payload)
    assert "SECRET" not in rendered and "cost" not in rendered and "prompt" not in rendered
    resource = payload["resourceSpans"][0]
    assert resource["resource"]["attributes"] == [
        {"key": "service.name", "value": {"stringValue": "app"}}
    ]
    spans = resource["scopeSpans"][0]["spans"]
    assert len(spans) == 3
    by_name = {s["name"]: s for s in spans}
    assert by_name["test:test"]["parentSpanId"] == by_name["work"]["spanId"]
    assert by_name["work"]["parentSpanId"] == by_name["app"]["spanId"]
    assert len({s["spanId"] for s in spans}) == 3
    for span in spans:
        assert len(span["traceId"]) == 32 and int(span["traceId"], 16) > 0
        assert len(span["spanId"]) == 16 and int(span["spanId"], 16) > 0
        assert int(span["endTimeUnixNano"]) >= int(span["startTimeUnixNano"])
        assert isinstance(span["status"]["code"], int)
    assert spans == export_traces([detail])["resourceSpans"][0]["scopeSpans"][0]["spans"]


def test_running_and_unknown_endings_are_not_fabricated(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("app", store=store) as trace:
        detail = store.get_trace(trace.trace_id)
        assert export_traces([detail]) == {"resourceSpans": []}
    detail["status"] = "interrupted"
    assert export_traces([detail]) == {"resourceSpans": []}
    completed = store.get_trace(trace.trace_id)
    assert export_traces([completed])["resourceSpans"]


def test_orphan_and_cyclic_parent_recovery_does_not_drop_spans():
    parents = valid_parents(
        [
            {"id": "a", "parent_id": "b"},
            {"id": "b", "parent_id": "a"},
            {"id": "c", "parent_id": "missing"},
        ]
    )
    assert set(parents) == {"a", "b", "c"}
    assert parents["c"] is None
    assert parents["a"] is None or parents["b"] is None


def test_cli_and_dashboard_download_export_the_same_payload(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from nextrace.dashboard.app import create_app

    db = tmp_path / "traces.db"
    store = SQLiteStore(db)
    trace = fixture_trace(store)
    output = tmp_path / "export.json"
    assert (
        main(["export-otel", "--db", str(db), "--output", str(output), "--trace-id", trace["id"]])
        == 0
    )
    client = TestClient(create_app(db))
    response = client.get(f"/api/traces/{trace['id']}/otel")
    assert response.status_code == 200
    assert response.json() == json.loads(output.read_text())
    assert "attachment" in response.headers["content-disposition"]
    with ai_trace("app", store=store) as running:
        assert client.get(f"/api/traces/{running.trace_id}/otel").status_code == 409
    assert client.get("/api/traces/missing/otel").status_code == 404


@pytest.mark.parametrize(
    "response,status,expected",
    [
        ({}, 200, None),
        ({"partialSuccess": {"rejectedSpans": "2"}}, 200, "partial success"),
        ({"partialSuccess": {"errorMessage": "COLLECTOR-SECRET"}}, 200, "partial success"),
        ({}, 503, "HTTP 503"),
        ({}, 204, "HTTP 204"),
        ([], 200, "invalid response"),
    ],
)
def test_http_export_and_rejection_handling(response, status, expected, monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers.get("Content-Type"),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("NEXTRACE_OTLP_HEADERS", '{"Authorization":"TEST-SECRET"}')
    payload = {"resourceSpans": []}
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/traces"
        if expected:
            with pytest.raises(RuntimeError, match=expected) as error:
                send_otlp(payload, url)
            assert "SECRET" not in str(error.value)
        else:
            send_otlp(payload, url)
        assert received == [("/v1/traces", "application/json", payload)]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_cost_is_not_exposed_even_for_legacy_databases(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    store = SQLiteStore(db)
    trace = fixture_trace(store)
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE spans ADD COLUMN cost_usd REAL")
        conn.execute("UPDATE spans SET cost_usd = 100")
        conn.execute(
            'UPDATE spans SET metadata = \'{"cost_estimated":true,"pricing_mode":"standard"}\''
        )
    store = SQLiteStore(db)
    for value in (
        store.summary(),
        store.list_traces(),
        store.get_trace(trace["id"]),
        export_traces([store.get_trace(trace["id"])]),
    ):
        assert "cost" not in json.dumps(value) and "pricing" not in json.dumps(value)


def test_otlp_payload_decodes_against_the_official_protobuf_schema(tmp_path):
    import base64

    from google.protobuf.json_format import ParseDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    store = SQLiteStore(tmp_path / "traces.db")
    payload = export_traces([fixture_trace(store)])
    # OTLP JSON uses hex IDs; protobuf's generic JSON parser expects base64 for bytes.
    for resource in payload["resourceSpans"]:
        for scope in resource["scopeSpans"]:
            for span in scope["spans"]:
                for key in ("traceId", "spanId", "parentSpanId"):
                    if key in span:
                        span[key] = base64.b64encode(bytes.fromhex(span[key])).decode()
    parsed = ParseDict(payload, ExportTraceServiceRequest(), ignore_unknown_fields=False)
    spans = parsed.resource_spans[0].scope_spans[0].spans
    assert len(spans) == 3
    assert len(spans[0].trace_id) == 16 and len(spans[0].span_id) == 8
    assert spans[0].end_time_unix_nano >= spans[0].start_time_unix_nano
