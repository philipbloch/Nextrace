"""Integration helper functions."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, is_dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_KEYS = {
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "auth",
    "authtoken",
    "authorization",
    "bearer",
    "bearer_token",
    "client",
    "client_secret",
    "clientsecret",
    "cookie",
    "headers",
    "password",
    "refresh_token",
    "refreshtoken",
    "secret",
    "session",
    "session_id",
    "session_token",
    "token",
}
SENSITIVE_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_auth",
    "_auth_token",
    "_authorization",
    "_bearer",
    "_cookie",
    "_password",
    "_refresh_token",
    "_secret",
    "_session",
)


def safe_serialize(value: Any) -> Any:
    """Convert provider and client objects into JSON-friendly structures."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [safe_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): safe_serialize(val) for key, val in value.items()}
    if is_dataclass(value):
        return safe_serialize(asdict(value))
    if hasattr(value, "model_dump"):
        try:
            return safe_serialize(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "to_dict"):
        try:
            return safe_serialize(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        public = {
            key: val
            for key, val in vars(value).items()
            if not key.startswith("_") and not callable(val)
        }
        if public:
            return safe_serialize(public)
    return str(value)


def get_nested(value: Any, *path: str) -> Any:
    current = value
    for part in path:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def normalize_usage(usage: Any) -> dict[str, int | None]:
    """Normalize OpenAI, Anthropic, Gemini, and custom usage objects."""
    if usage is None:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    input_tokens = _first_not_none(
        get_nested(usage, "input_tokens"),
        get_nested(usage, "prompt_tokens"),
        get_nested(usage, "prompt_token_count"),
    )
    output_tokens = _first_not_none(
        get_nested(usage, "output_tokens"),
        get_nested(usage, "completion_tokens"),
        get_nested(usage, "candidates_token_count"),
    )
    total_tokens = _first_not_none(
        get_nested(usage, "total_tokens"),
        get_nested(usage, "total_token_count"),
    )

    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    return {
        "input_tokens": int(input_tokens) if input_tokens is not None else None,
        "output_tokens": int(output_tokens) if output_tokens is not None else None,
        "total_tokens": int(total_tokens) if total_tokens is not None else None,
    }


def compact_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Keep metadata useful without storing obvious secret-bearing values."""
    return _remove_sensitive_values(safe_serialize(kwargs))


def record_model_span(
    trace: Any,
    *,
    provider: str,
    model: str,
    name: str,
    prompt: Any,
    response: Any,
    started_at: float,
    usage: Any = None,
    kwargs: dict[str, Any] | None = None,
    response_id: Any = None,
    error: BaseException | None = None,
) -> None:
    """Record the common span shape emitted by provider client integrations."""
    normalized_usage = normalize_usage(usage)
    metadata = {"kwargs": compact_kwargs(kwargs or {})}
    if response_id is not None:
        metadata["id"] = response_id
    trace.model_call(
        provider=provider,
        model=model,
        name=name,
        prompt=prompt,
        response=safe_serialize(response),
        latency_ms=(time.perf_counter() - started_at) * 1000,
        input_tokens=normalized_usage["input_tokens"],
        output_tokens=normalized_usage["output_tokens"],
        total_tokens=normalized_usage["total_tokens"],
        metadata=metadata,
        error=error,
    )


def redact_url(url: str) -> str:
    """Preserve URL routing information while redacting query values."""
    parsed = urlsplit(url)
    query = urlencode([(key, "[REDACTED]") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _first_not_none(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _remove_sensitive_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _remove_sensitive_values(item)
            for key, item in value.items()
            if not is_sensitive_key(key)
        }
    if isinstance(value, list):
        return [_remove_sensitive_values(item) for item in value]
    return value


def is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", key.strip()).lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or normalized.endswith(SENSITIVE_SUFFIXES)
