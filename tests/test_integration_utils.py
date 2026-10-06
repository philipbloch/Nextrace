from __future__ import annotations

from nextrace.integrations._utils import compact_kwargs, normalize_usage, redact_url


def test_redaction_handles_uppercase_credentials_and_url_userinfo():
    assert compact_kwargs({"AUTHORIZATION": "secret", "API_KEY": "secret", "maxTokens": 12}) == {
        "maxTokens": 12,
    }
    assert redact_url("https://user:secret@example.com/path?token=secret") == (
        "https://example.com/path?token=%5BREDACTED%5D"
    )


def test_normalize_usage_preserves_explicit_zero_values():
    usage = {
        "input_tokens": 0,
        "prompt_tokens": 12,
        "output_tokens": 0,
        "completion_tokens": 4,
        "total_tokens": 0,
        "total_token_count": 16,
    }

    assert normalize_usage(usage) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def test_compact_kwargs_removes_nested_credentials_but_keeps_token_counts():
    kwargs = {
        "apiKey": "top-secret",
        "max_tokens": 512,
        "options": {
            "access_token": "nested-secret",
            "temperature": 0.2,
        },
    }

    assert compact_kwargs(kwargs) == {
        "max_tokens": 512,
        "options": {"temperature": 0.2},
    }


def test_redact_url_preserves_keys_and_fragment_without_query_values():
    redacted = redact_url("https://example.com/models?token=secret&mode=fast#usage")

    assert redacted == ("https://example.com/models?token=%5BREDACTED%5D&mode=%5BREDACTED%5D#usage")
    assert "secret" not in redacted
