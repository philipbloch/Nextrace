const GENERIC_MCP_ERROR = /^MCP (tool returned isError|JSON-RPC error)/;

const HTTP_FAILURES = {
  401: "The upstream service rejected authentication. Check the service connection or sign in again.",
  403: "The upstream service denied access. Check permissions for this operation.",
  404: "The upstream service could not find the requested resource or endpoint.",
  405: "The upstream service rejected this HTTP method.",
  429: "The upstream service rate limited the request. Check its retry instructions before retrying.",
};

const RPC_FAILURES = {
  "-32700": "The MCP server could not parse the JSON-RPC request.",
  "-32600": "The MCP server rejected the JSON-RPC request as invalid.",
  "-32601": "The MCP server did not recognize the requested method.",
  "-32602": "The MCP server rejected the method parameters.",
  "-32603": "The MCP server reported an internal error. Its message may explain the cause.",
};

export function hasFailure(record) {
  return Boolean(
    record && (record.error || record.status === "error" || record.status === "interrupted"),
  );
}

function failureExplanation(record, details, httpStatus, legacyCode, legacyMcp) {
  if (record.status === "interrupted") {
    return "Recording stopped before a completed result was available. This does not establish whether the upstream operation succeeded.";
  }
  const exceptionType = record.metadata?.exception_type;
  if (httpStatus >= 400 && !exceptionType) {
    return HTTP_FAILURES[httpStatus] || `The upstream service returned HTTP ${httpStatus}.`;
  }
  const rpc = details.find((item) => item.category === "jsonrpc");
  if (rpc || legacyCode) {
    return RPC_FAILURES[rpc?.code ?? legacyCode] || "The MCP server returned a JSON-RPC error.";
  }
  if (legacyMcp || details.some((item) => item.category === "mcp_tool")) {
    return httpStatus != null && httpStatus < 400
      ? `HTTP ${httpStatus} returned a response, but the MCP operation reported failure.`
      : "The MCP operation reported failure.";
  }
  if (exceptionType) return `A recorded ${exceptionType} stopped this operation.`;
  return "This step failed. Read the recorded message and traceback for the cause.";
}

export function errorDiagnosis(record, trace) {
  if (
    (record === trace || record.kind === "observation") &&
    !record.metadata?.error_details &&
    !record.metadata?.traceback
  ) {
    const failed = (trace.spans || []).filter(
      (span) =>
        span.kind !== "observation" &&
        hasFailure(span) &&
        (record === trace || span.parent_id === record.id),
    );
    record =
      failed.find((span) => span.metadata?.error_details || span.metadata?.traceback) ||
      failed[0] ||
      record;
  }
  if (!hasFailure(record)) return null;
  const metadata = record.metadata || {};
  const details = metadata.error_details || [];
  const httpStatus = record.response?.http_status ?? metadata.http_status;
  const legacyMcp = GENERIC_MCP_ERROR.test(record.error || record.response?.mcp_error || "");
  const codes = details.filter((item) => item.code != null).map((item) => String(item.code));
  const legacyCode = record.error?.match(/^MCP JSON-RPC error \((-?\d+)\)$/)?.[1];
  if (!codes.length && legacyCode) codes.push(legacyCode);
  const hasMessage = details.some((item) => item.message);
  const identifiers = {
    "Trace ID": trace.id,
    "Session ID": trace.session_id,
    "Turn ID": trace.turn_id,
    ...(record !== trace ? { "Span ID": record.id } : {}),
    ...(record.trace_id && record.trace_id !== trace.id
      ? { "Source trace ID": record.trace_id }
      : {}),
    ...metadata.correlation_ids,
  };
  details.forEach((item, index) => {
    for (const [label, value] of Object.entries(item.correlation_ids || {})) {
      identifiers[details.length > 1 ? `Response ${index + 1} · ${label}` : label] = value;
    }
  });
  return {
    name: record.name,
    explanation: failureExplanation(record, details, httpStatus, legacyCode, legacyMcp),
    details,
    codes,
    httpStatus,
    error: record.error,
    hasMessage,
    missingMessage: (legacyMcp || (httpStatus >= 400 && !metadata.exception_type)) && !hasMessage,
    traceback: metadata.traceback,
    exceptionType: metadata.exception_type,
    identifiers: Object.entries(identifiers).filter(([, value]) => value != null && value !== ""),
  };
}
