"""Gemini integration helpers."""

from __future__ import annotations

import time
from typing import Any

from nextrace.context import current_trace
from nextrace.integrations._utils import get_nested, record_model_span


def traced_gemini_generate(
    model_client: Any,
    prompt: Any,
    *,
    model: str | None = None,
    trace: Any | None = None,
    name: str = "gemini.generate_content",
    **kwargs: Any,
) -> Any:
    """Call ``model_client.generate_content`` and record a model span."""
    active = trace or current_trace(required=True)
    provider_model = model or getattr(model_client, "model_name", None) or getattr(model_client, "_model_name", None)
    started = time.perf_counter()
    try:
        response = model_client.generate_content(prompt, **kwargs)
    except Exception as exc:
        record_model_span(
            active,
            provider="gemini",
            model=provider_model or "unknown",
            name=name,
            prompt=prompt,
            response=None,
            started_at=started,
            kwargs=kwargs,
            error=exc,
        )
        raise

    record_model_span(
        active,
        provider="gemini",
        model=provider_model or "unknown",
        name=name,
        prompt=prompt,
        response=response,
        started_at=started,
        usage=get_nested(response, "usage_metadata"),
        kwargs=kwargs,
    )
    return response
