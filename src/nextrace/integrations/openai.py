from __future__ import annotations

from typing import Any

from nextrace.core import Trace
from nextrace.integrations._utils import get_nested, model_span, set_model_result


def traced_openai_chat(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    trace: Trace | None = None,
    name: str = "openai.chat.completions.create",
    **kwargs: Any,
) -> Any:
    with model_span(
        provider="openai",
        model=model,
        name=name,
        prompt=messages,
        trace=trace,
        kwargs=kwargs,
    ) as span:
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        set_model_result(
            span,
            response,
            usage=get_nested(response, "usage"),
            response_id=get_nested(response, "id"),
        )
        return response


async def async_traced_openai_chat(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    trace: Trace | None = None,
    name: str = "openai.chat.completions.create",
    **kwargs: Any,
) -> Any:
    with model_span(
        provider="openai",
        model=model,
        name=name,
        prompt=messages,
        trace=trace,
        kwargs=kwargs,
    ) as span:
        response = await client.chat.completions.create(model=model, messages=messages, **kwargs)
        set_model_result(
            span,
            response,
            usage=get_nested(response, "usage"),
            response_id=get_nested(response, "id"),
        )
        return response
