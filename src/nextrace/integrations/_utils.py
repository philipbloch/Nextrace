from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from nextrace.context import current_trace
from nextrace.core import Span, Trace

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
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [safe_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): safe_serialize(val) for key, val in value.items()}
    if is_dataclass(value):
        return safe_serialize(asdict(value))
    for method in ("model_dump", "to_dict"):
        serialize = getattr(value, method, None)
        if callable(serialize):
            try:
                return safe_serialize(serialize())
            except Exception:
                continue
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
    return _remove_sensitive_values(safe_serialize(kwargs))


@contextmanager
def model_span(
    *,
    provider: str,
    model: str,
    name: str,
    prompt: Any,
    trace: Trace | None = None,
    kwargs: dict[str, Any] | None = None,
) -> Iterator[Span]:
    active = trace or current_trace(required=True)
    with active.step(
        "model",
        name,
        provider=provider,
        model=model,
        prompt=prompt,
        metadata={"kwargs": compact_kwargs(kwargs or {})},
    ) as span:
        yield span


def set_model_result(
    span: Span, response: Any, *, usage: Any = None, response_id: Any = None
) -> None:
    span.set_result(response=safe_serialize(response), **normalize_usage(usage))
    if response_id is not None:
        span.metadata["id"] = response_id


def redact_url(url: str) -> str:
    parsed = urlsplit(url)
    query = urlencode(
        [(key, "[REDACTED]") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
    )
    host = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, host, parsed.path, query, parsed.fragment))


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
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key.strip()).lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or normalized.endswith(SENSITIVE_SUFFIXES)
