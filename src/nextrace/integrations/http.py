"""Custom HTTP endpoint tracing."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from nextrace.context import current_trace
from nextrace.integrations._utils import normalize_usage, redact_url, safe_serialize


def traced_http_json(
    url: str,
    *,
    payload: dict[str, Any],
    provider: str = "custom-http",
    model: str | None = None,
    trace: Any | None = None,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    timeout: float = 60,
    retries: int = 0,
    name: str = "http.json",
) -> Any:
    """Call a JSON HTTP model endpoint and record the request as a model span."""
    if retries < 0:
        raise ValueError("retries must be zero or greater")

    active = trace or current_trace(required=True)
    started = time.perf_counter()
    last_error: BaseException | None = None
    response_payload: Any = None
    request_method = method.upper()
    model_name = model or str(payload.get("model") or "unknown")
    metadata = {"url": redact_url(url), "method": request_method}
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **(headers or {}),
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method=request_method,
    )

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                response_payload = json.loads(raw) if raw else None
                break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                active.retry(
                    name,
                    error=exc,
                    metadata={"attempt": attempt + 1, "url": metadata["url"]},
                )
                continue
            usage = normalize_usage(_usage_from_response(response_payload))
            active.model_call(
                provider=provider,
                model=model_name,
                name=name,
                prompt=payload,
                response=safe_serialize(response_payload),
                latency_ms=(time.perf_counter() - started) * 1000,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                total_tokens=usage["total_tokens"],
                retry_count=attempt,
                metadata=metadata,
                error=exc,
            )
            raise

    usage = normalize_usage(_usage_from_response(response_payload))
    active.model_call(
        provider=provider,
        model=model_name,
        name=name,
        prompt=payload,
        response=safe_serialize(response_payload),
        latency_ms=(time.perf_counter() - started) * 1000,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        total_tokens=usage["total_tokens"],
        retry_count=attempt,
        metadata={**metadata, "last_error": str(last_error) if last_error else None},
    )
    return response_payload


def _usage_from_response(response: Any) -> Any:
    if isinstance(response, dict):
        return response.get("usage") or response.get("usage_metadata")
    return None
