import asyncio
from types import SimpleNamespace

import pytest

from nextrace import SQLiteStore, ai_trace
from nextrace.integrations.anthropic import (
    async_traced_anthropic_messages,
    traced_anthropic_messages,
)
from nextrace.integrations.gemini import traced_gemini_generate
from nextrace.integrations.local import traced_model_call
from nextrace.integrations.openai import async_traced_openai_chat, traced_openai_chat


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_provider_adapters_record_one_span_and_preserve_results(
    tmp_path, provider, asynchronous, fail
):
    result = {"id": "response-1", "usage": {"input_tokens": 0, "output_tokens": 3}}
    failure = ValueError("model failed")

    def create(**kwargs):
        assert kwargs["model"] == "test-model"
        assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
        if fail:
            raise failure
        return result

    async def async_create(**kwargs):
        return create(**kwargs)

    endpoint = SimpleNamespace(create=async_create if asynchronous else create)
    client = SimpleNamespace(messages=endpoint, chat=SimpleNamespace(completions=endpoint))
    adapters = {
        ("openai", False): traced_openai_chat,
        ("openai", True): async_traced_openai_chat,
        ("anthropic", False): traced_anthropic_messages,
        ("anthropic", True): async_traced_anthropic_messages,
    }
    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("test", store=store) as trace:
        try:
            response = adapters[provider, asynchronous](
                client,
                model="test-model",
                messages=[{"role": "user", "content": "hello"}],
                api_key="secret-key",
            )
            if asynchronous:
                response = asyncio.run(response)
            assert not fail
            assert response is result
        except ValueError as error:
            assert fail
            assert error is failure
    spans = store.get_trace(trace.trace_id)["spans"]
    assert len(spans) == 1
    span = spans[0]
    assert span["provider"] == provider
    assert span["metadata"]["kwargs"] == {}
    assert span["status"] == ("error" if fail else "ok")
    if not fail:
        assert span["input_tokens"] == 0
        assert span["output_tokens"] == span["total_tokens"] == 3
        assert span["metadata"]["id"] == "response-1"


def test_gemini_usage_and_local_async_failures(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    response = {"usage_metadata": {"prompt_token_count": 7, "candidates_token_count": 2}}
    model = SimpleNamespace(model_name="test", generate_content=lambda *args, **kwargs: response)

    @traced_model_call(model="local")
    async def fail():
        raise RuntimeError("local failed")

    with ai_trace("test", store=store) as trace:
        assert traced_gemini_generate(model, "hello") is response
        with pytest.raises(RuntimeError, match="local failed"):
            asyncio.run(fail())
    spans = store.get_trace(trace.trace_id)["spans"]
    assert [(span["provider"], span["status"]) for span in spans] == [
        ("gemini", "ok"),
        ("local", "error"),
    ]
    assert spans[0]["total_tokens"] == 9

    with pytest.raises(RuntimeError, match="local failed"):
        asyncio.run(fail())
    assert len(store.get_trace(trace.trace_id)["spans"]) == 2
