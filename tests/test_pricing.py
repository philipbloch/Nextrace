from __future__ import annotations

import json

from nextrace.pricing import (
    PRICING_FILE_ENV,
    PricingRegistry,
    default_pricing_registry,
    load_configured_pricing,
    load_pricing_file,
)


def test_default_pricing_aliases_shopify_proxy_to_openai():
    cost = default_pricing_registry.estimate(
        provider="shopify-proxy",
        model="gpt-5.5",
        input_tokens=1000,
        cached_input_tokens=100,
        output_tokens=50,
    )

    assert cost == 0.00605


def test_default_pricing_handles_claude_cache_and_effective_dates():
    introductory = default_pricing_registry.estimate(
        provider="anthropic",
        model="claude-sonnet-5",
        input_tokens=1300,
        cached_input_tokens=300,
        cache_write_5m_tokens=150,
        cache_write_1h_tokens=50,
        output_tokens=50,
        effective_at="2026-08-31",
    )
    standard = default_pricing_registry.estimate(
        provider="anthropic",
        model="claude-sonnet-5",
        input_tokens=1300,
        cached_input_tokens=300,
        cache_write_5m_tokens=150,
        cache_write_1h_tokens=50,
        output_tokens=50,
        effective_at="2026-09-01",
    )

    assert introductory == 0.003135
    assert standard == 0.0047025


def test_default_pricing_supports_gemini_text_models():
    cost = default_pricing_registry.estimate(
        provider="google",
        model="gemini-2.5-flash",
        input_tokens=1000,
        output_tokens=50,
    )

    assert cost == 0.000425


def test_default_pricing_supports_gemini_modes():
    priority_cost = default_pricing_registry.estimate(
        provider="gemini",
        model="gemini-2.5-flash",
        input_tokens=1000,
        output_tokens=50,
        mode="priority",
    )

    assert priority_cost == 0.000765


def test_pricing_preserves_explicit_zero_cache_write_rate():
    registry = PricingRegistry()
    registry.register(
        provider="custom",
        model="free-cache-writes",
        input_per_million=2.0,
        output_per_million=4.0,
        cache_write_5m_per_million=0.0,
    )

    cost = registry.estimate(
        provider="custom",
        model="free-cache-writes",
        input_tokens=0,
        output_tokens=0,
        cache_write_5m_tokens=1000,
    )

    assert cost == 0.0


def test_pricing_file_override_applies_before_provider_alias(tmp_path):
    registry = PricingRegistry()
    registry.register(
        provider="openai",
        model="gpt-5.5",
        input_per_million=5.0,
        cached_input_per_million=0.50,
        output_per_million=30.0,
    )
    path = tmp_path / "pricing.json"
    path.write_text(
        json.dumps(
            {
                "prices": [
                    {
                        "provider": "shopify-proxy",
                        "model": "gpt-5.5",
                        "input_per_million": 1.0,
                        "cached_input_per_million": 0.10,
                        "output_per_million": 2.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    load_pricing_file(registry, path)

    shopify_cost = registry.estimate(
        provider="shopify-proxy",
        model="gpt-5.5",
        input_tokens=1000,
        cached_input_tokens=100,
        output_tokens=50,
    )
    openai_cost = registry.estimate(
        provider="openai",
        model="gpt-5.5",
        input_tokens=1000,
        cached_input_tokens=100,
        output_tokens=50,
    )

    assert shopify_cost == 0.00101
    assert openai_cost == 0.00605


def test_configured_pricing_loads_env_file(tmp_path):
    registry = PricingRegistry()
    path = tmp_path / "pricing.json"
    path.write_text(
        json.dumps(
            {
                "prices": [
                    {
                        "provider": "internal-proxy",
                        "model": "internal-model",
                        "input_per_million": 2.0,
                        "output_per_million": 3.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    load_configured_pricing(
        registry,
        env={PRICING_FILE_ENV: str(path)},
        default_path=tmp_path / "missing.json",
    )

    cost = registry.estimate(
        provider="internal-proxy",
        model="internal-model",
        input_tokens=1000,
        output_tokens=50,
    )

    assert cost == 0.00215
