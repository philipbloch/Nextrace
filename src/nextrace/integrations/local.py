"""Tracing decorators for local model functions."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from nextrace.context import current_trace
from nextrace.integrations._utils import safe_serialize

PromptExtractor = Callable[..., Any]
UsageExtractor = Callable[[Any], dict[str, int | None]]


def traced_model_call(
    *,
    provider: str = "local",
    model: str,
    name: str | None = None,
    prompt_extractor: PromptExtractor | None = None,
    usage_extractor: UsageExtractor | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a local model function so it emits model spans."""

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        span_name = name or func.__name__

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                trace = current_trace(required=False)
                if trace is None:
                    return await func(*args, **kwargs)
                prompt = _extract_prompt(prompt_extractor, args, kwargs)
                started = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    trace.model_call(
                        provider=provider,
                        model=model,
                        name=span_name,
                        prompt=prompt,
                        response=None,
                        latency_ms=(time.perf_counter() - started) * 1000,
                        error=exc,
                    )
                    raise
                usage = usage_extractor(result) if usage_extractor else {}
                trace.model_call(
                    provider=provider,
                    model=model,
                    name=span_name,
                    prompt=prompt,
                    response=safe_serialize(result),
                    latency_ms=(time.perf_counter() - started) * 1000,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    total_tokens=usage.get("total_tokens"),
                )
                return result

            return async_wrapper

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            trace = current_trace(required=False)
            if trace is None:
                return func(*args, **kwargs)
            prompt = _extract_prompt(prompt_extractor, args, kwargs)
            started = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                trace.model_call(
                    provider=provider,
                    model=model,
                    name=span_name,
                    prompt=prompt,
                    response=None,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error=exc,
                )
                raise
            usage = usage_extractor(result) if usage_extractor else {}
            trace.model_call(
                provider=provider,
                model=model,
                name=span_name,
                prompt=prompt,
                response=safe_serialize(result),
                latency_ms=(time.perf_counter() - started) * 1000,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
            return result

        return wrapper

    return decorate


def _extract_prompt(extractor: PromptExtractor | None, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if extractor is not None:
        return safe_serialize(extractor(*args, **kwargs))
    if len(args) == 1 and not kwargs:
        return safe_serialize(args[0])
    return safe_serialize({"args": args, "kwargs": kwargs})

