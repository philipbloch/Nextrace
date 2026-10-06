import test from "node:test";
import assert from "node:assert/strict";
import { errorDiagnosis } from "./diagnosis.js";

test("historical HTTP 200 tool failures explain the missing message without guessing a cause", () => {
  const trace = {
    id: "trace",
    session_id: "session",
    status: "error",
    error: "MCP tool returned isError",
    spans: [
      {
        id: "span",
        name: "tool:search",
        status: "error",
        error: "MCP tool returned isError",
        response: { http_status: 200 },
      },
    ],
  };
  const diagnosis = errorDiagnosis(trace, trace);
  assert.match(diagnosis.explanation, /HTTP 200.*operation reported failure/);
  assert.equal(diagnosis.missingMessage, true);
  assert.equal(diagnosis.name, "tool:search");
  assert.ok(
    diagnosis.identifiers.some(([label, value]) => label === "Span ID" && value === "span"),
  );
});

test("captured errors expose original messages, codes, source IDs and exception tracebacks", () => {
  const trace = { id: "parent", session_id: "session", turn_id: "turn" };
  const span = {
    id: "span",
    trace_id: "child",
    status: "error",
    error: "MCP JSON-RPC error (-32602)",
    metadata: {
      traceback: "Traceback\nValueError: invalid input",
      exception_type: "ValueError",
      correlation_ids: { "x-request-id": "upstream" },
      error_details: [
        {
          category: "jsonrpc",
          code: -32602,
          message: "Invalid page identifier",
          correlation_ids: { jsonrpc_id: 0 },
        },
      ],
    },
  };
  const diagnosis = errorDiagnosis(span, trace);
  assert.match(diagnosis.explanation, /rejected the method parameters/);
  assert.equal(diagnosis.details[0].message, "Invalid page identifier");
  assert.equal(diagnosis.traceback, span.metadata.traceback);
  assert.equal(diagnosis.missingMessage, false);
  assert.deepEqual(Object.fromEntries(diagnosis.identifiers), {
    "Trace ID": "parent",
    "Session ID": "session",
    "Turn ID": "turn",
    "Span ID": "span",
    "Source trace ID": "child",
    "x-request-id": "upstream",
    jsonrpc_id: 0,
  });
});

test("HTTP failures and interruptions describe only the recorded outcome", () => {
  const trace = { id: "trace", status: "error", error: "HTTP 429", metadata: { http_status: 429 } };
  assert.match(errorDiagnosis(trace, trace).explanation, /rate limited/);
  trace.status = "interrupted";
  assert.match(errorDiagnosis(trace, trace).explanation, /does not establish/);
  const success = { id: "span", status: "ok" };
  assert.equal(errorDiagnosis(success, trace), null);
  const timeout = {
    id: "timeout",
    status: "error",
    error: "Timed out",
    metadata: { http_status: 502, exception_type: "TimeoutError" },
  };
  assert.match(errorDiagnosis(timeout, trace).explanation, /TimeoutError stopped/);
  assert.equal(errorDiagnosis(timeout, trace).missingMessage, false);
});

test("multiple response identifiers remain associated with their own failures", () => {
  const trace = {
    id: "trace",
    status: "error",
    metadata: {
      error_details: [
        {
          category: "mcp_tool",
          code: "PAGE_NOT_FOUND",
          message: "first failure",
          correlation_ids: { jsonrpc_id: 1 },
        },
        {
          category: "jsonrpc",
          code: -32602,
          message: "second failure",
          correlation_ids: { jsonrpc_id: 2 },
        },
      ],
    },
  };
  const diagnosis = errorDiagnosis(trace, trace);
  const identifiers = Object.fromEntries(diagnosis.identifiers);
  assert.equal(identifiers["Response 1 · jsonrpc_id"], 1);
  assert.equal(identifiers["Response 2 · jsonrpc_id"], 2);
  assert.match(diagnosis.explanation, /rejected the method parameters/);
});

test("a correlated recording wrapper uses the diagnostics of its failed step", () => {
  const wrapper = {
    id: "observation-child",
    kind: "observation",
    status: "error",
    error: "MCP tool returned isError",
  };
  const trace = {
    id: "parent",
    spans: [
      wrapper,
      {
        id: "span",
        parent_id: wrapper.id,
        trace_id: "child",
        kind: "tool",
        status: "error",
        error: wrapper.error,
        metadata: { error_details: [{ category: "mcp_tool", message: "Page not found" }] },
      },
    ],
  };
  const diagnosis = errorDiagnosis(wrapper, trace);
  assert.equal(diagnosis.details[0].message, "Page not found");
  assert.equal(diagnosis.missingMessage, false);
});
