from __future__ import annotations

import inspect
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import Any

from nextrace.context import current_trace
from nextrace.integrations._utils import safe_serialize, set_model_result

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

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        span_name = name or func.__name__

        @contextmanager
        def capture(args, kwargs):
            trace = current_trace()
            if trace is None:
                yield None
                return
            with trace.step(
                "model",
                span_name,
                provider=provider,
                model=model,
                prompt=_extract_prompt(prompt_extractor, args, kwargs),
            ) as span:
                yield span

        def save_result(span, result):
            if span is not None:
                usage = usage_extractor(result) if usage_extractor else None
                set_model_result(span, result, usage=usage)
            return result

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with capture(args, kwargs) as span:
                    return save_result(span, await func(*args, **kwargs))

            return async_wrapper

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with capture(args, kwargs) as span:
                return save_result(span, func(*args, **kwargs))

        return wrapper

    return decorate


def _extract_prompt(
    extractor: PromptExtractor | None, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    if extractor is not None:
        return safe_serialize(extractor(*args, **kwargs))
    if len(args) == 1 and not kwargs:
        return safe_serialize(args[0])
    return safe_serialize({"args": args, "kwargs": kwargs})
