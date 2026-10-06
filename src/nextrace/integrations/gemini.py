from __future__ import annotations

from typing import Any

from nextrace.core import Trace
from nextrace.integrations._utils import get_nested, model_span, set_model_result


def traced_gemini_generate(
    model_client: Any,
    prompt: Any,
    *,
    model: str | None = None,
    trace: Trace | None = None,
    name: str = "gemini.generate_content",
    **kwargs: Any,
) -> Any:
    provider_model = (
        model
        or getattr(model_client, "model_name", None)
        or getattr(model_client, "_model_name", None)
    )
    with model_span(
        provider="gemini",
        model=provider_model or "unknown",
        name=name,
        prompt=prompt,
        trace=trace,
        kwargs=kwargs,
    ) as span:
        response = model_client.generate_content(prompt, **kwargs)
        set_model_result(span, response, usage=get_nested(response, "usage_metadata"))
        return response
