"""OpenAI integration helpers."""

from __future__ import annotations

import time
from typing import Any

from nextrace.context import current_trace
from nextrace.integrations._utils import get_nested, record_model_span


def traced_openai_chat(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    trace: Any | None = None,
    name: str = "openai.chat.completions.create",
    **kwargs: Any,
) -> Any:
    """Call ``client.chat.completions.create`` and record a model span."""
    active = trace or current_trace(required=True)
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
    except Exception as exc:
        record_model_span(
            active,
            provider="openai",
            model=model,
            name=name,
            prompt=messages,
            response=None,
            started_at=started,
            kwargs=kwargs,
            error=exc,
        )
        raise

    record_model_span(
        active,
        provider="openai",
        model=model,
        name=name,
        prompt=messages,
        response=response,
        started_at=started,
        usage=get_nested(response, "usage"),
        kwargs=kwargs,
        response_id=get_nested(response, "id"),
    )
    return response


async def async_traced_openai_chat(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    trace: Any | None = None,
    name: str = "openai.chat.completions.create",
    **kwargs: Any,
) -> Any:
    """Async variant for ``AsyncOpenAI`` clients."""
    active = trace or current_trace(required=True)
    started = time.perf_counter()
    try:
        response = await client.chat.completions.create(model=model, messages=messages, **kwargs)
    except Exception as exc:
        record_model_span(
            active,
            provider="openai",
            model=model,
            name=name,
            prompt=messages,
            response=None,
            started_at=started,
            kwargs=kwargs,
            error=exc,
        )
        raise

    record_model_span(
        active,
        provider="openai",
        model=model,
        name=name,
        prompt=messages,
        response=response,
        started_at=started,
        usage=get_nested(response, "usage"),
        kwargs=kwargs,
        response_id=get_nested(response, "id"),
    )
    return response
