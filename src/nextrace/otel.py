from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from typing import Any


def otel_id(value: str, size: int) -> str:
    if re.fullmatch(rf"[0-9a-fA-F]{{{size}}}", value) and int(value, 16):
        return value.lower()
    return hashlib.sha256(value.encode()).hexdigest()[:size]


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            encoded = {"boolValue": value}
        elif isinstance(value, int):
            encoded = {"intValue": str(value)}
        elif isinstance(value, float) and math.isfinite(value):
            encoded = {"doubleValue": value}
        else:
            encoded = {"stringValue": str(value)}
        result.append({"key": key, "value": encoded})
    return result


def _ns(value: float) -> str:
    return str(max(0, int(value * 1_000_000_000)))


def valid_parents(spans: list[dict]) -> dict[str, str | None]:
    """Break missing/cyclic parents deterministically without losing any spans."""
    parents = {s["id"]: s.get("parent_id") for s in spans}
    for span_id, parent in list(parents.items()):
        seen = {span_id}
        while parent in parents:
            if parent in seen:
                parents[span_id] = None
                break
            seen.add(parent)
            parent = parents[parent]
        if parents[span_id] not in parents:
            parents[span_id] = None
    return parents


def export_traces(traces: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Serialize completed traces as OTLP/HTTP JSON. Never include raw payloads or metadata."""
    resources: dict[str, list] = {}
    for trace in traces:
        if trace.get("ended_at") is None or trace.get("status") == "running":
            continue
        trace_id = otel_id(trace["id"], 32)
        root_id = otel_id("root:" + trace["id"], 16)
        spans = [
            s
            for s in trace.get("spans", [])
            if s.get("ended_at") is not None and s.get("status") != "running"
        ]
        parents = valid_parents(spans)
        common = {
            "nextrace.session.id": trace.get("session_id"),
            "nextrace.turn.id": trace.get("turn_id"),
        }
        start = min([trace["started_at"], *(s["started_at"] for s in spans)])
        end = max([trace["ended_at"], *(s["ended_at"] for s in spans)])
        exported = [
            {
                "traceId": trace_id,
                "spanId": root_id,
                "name": trace["name"],
                "kind": 1,
                "startTimeUnixNano": _ns(start),
                "endTimeUnixNano": _ns(end),
                "attributes": _attributes({**common, "nextrace.status": trace["status"]}),
                "status": {"code": {"error": 2, "ok": 1}.get(trace["status"], 0)},
            }
        ]
        for span in spans:
            attrs = {
                **common,
                "nextrace.kind": span["kind"],
                "nextrace.status": span["status"],
                "nextrace.timing": span.get("metadata", {}).get("timing", "measured"),
                "gen_ai.provider.name": span.get("provider"),
                "gen_ai.request.model": span.get("model"),
                "gen_ai.usage.input_tokens": span.get("input_tokens"),
                "gen_ai.usage.output_tokens": span.get("output_tokens"),
                "nextrace.retry_count": span.get("retry_count"),
                "nextrace.correlation.method": span.get("metadata", {}).get("correlation_method"),
            }
            if span["status"] == "error":
                attrs["error.type"] = "operation_error"
            exported.append(
                {
                    "traceId": trace_id,
                    "spanId": otel_id(span["id"], 16),
                    "parentSpanId": otel_id(parents[span["id"]], 16)
                    if parents[span["id"]]
                    else root_id,
                    "name": span["name"],
                    "kind": 3 if span["kind"] in {"tool", "model", "retrieval", "transport"} else 1,
                    "startTimeUnixNano": _ns(span["started_at"]),
                    "endTimeUnixNano": _ns(max(span["started_at"], span["ended_at"])),
                    "attributes": _attributes(attrs),
                    "status": {"code": {"error": 2, "ok": 1}.get(span["status"], 0)},
                }
            )
        resources.setdefault(trace["application"], []).extend(exported)
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _attributes({"service.name": app})},
                "scopeSpans": [{"scope": {"name": "nextrace", "version": "0.1.0"}, "spans": spans}],
            }
            for app, spans in resources.items()
        ]
    }


def send_otlp(payload: dict, endpoint: str, *, timeout: float = 30) -> None:
    """One explicit batch send; failures never change or discard the local recordings."""
    url = urllib.parse.urlsplit(endpoint)
    if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
        raise ValueError("Use an HTTP(S) OTLP traces endpoint without embedded credentials")
    try:
        headers = json.loads(os.getenv("NEXTRACE_OTLP_HEADERS", "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("NEXTRACE_OTLP_HEADERS must be a JSON object") from exc
    if not isinstance(headers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
    ):
        raise ValueError("NEXTRACE_OTLP_HEADERS must contain string header names and values")
    data = json.dumps(payload, allow_nan=False).encode()
    if len(data) > 64 * 1024 * 1024:
        raise ValueError("Export exceeds 64 MiB; select a smaller time range")
    headers = {**headers, "Content-Type": "application/json", "Accept": "application/json"}
    request = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

    # Avoid forwarding authentication to another host through an HTTP redirect.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"OTLP collector returned HTTP {response.status}")
            raw = response.read(4 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OTLP collector returned HTTP {exc.code}") from None
    except (OSError, urllib.error.URLError):
        raise RuntimeError(
            "Could not reach the OTLP collector; local traces are preserved"
        ) from None
    if len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("OTLP collector response exceeded 4 MiB")
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError
        partial = result.get("partialSuccess", {})
        if not isinstance(partial, dict):
            raise ValueError
        rejected = int(partial.get("rejectedSpans", 0))
    except (ValueError, TypeError):
        raise RuntimeError("OTLP collector returned an invalid response") from None
    if rejected or partial.get("errorMessage"):
        raise RuntimeError(f"OTLP collector reported partial success ({rejected} rejected spans)")
