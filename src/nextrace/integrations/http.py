from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from nextrace.context import current_trace
from nextrace.core import Trace
from nextrace.integrations._utils import get_nested, redact_url, set_model_result


def traced_http_json(
    url: str,
    *,
    payload: dict[str, Any],
    provider: str = "custom-http",
    model: str | None = None,
    trace: Trace | None = None,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    timeout: float = 60,
    retries: int = 0,
    name: str = "http.json",
) -> Any:
    if retries < 0:
        raise ValueError("retries must be zero or greater")

    active = trace or current_trace(required=True)
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

    with active.step(
        "model", name, provider=provider, model=model_name, prompt=payload, metadata=metadata
    ) as span:
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read().decode("utf-8")
                    result = json.loads(raw) if raw else None
                usage = get_nested(result, "usage") or get_nested(result, "usage_metadata")
                set_model_result(span, result, usage=usage)
                span.metadata["last_error"] = (
                    span.metadata["retry_errors"][-1] if span.retry_count else None
                )
                return result
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == retries:
                    raise
                span.add_retry(exc)
                active.retry(
                    name, error=exc, metadata={"attempt": attempt + 1, "url": metadata["url"]}
                )
