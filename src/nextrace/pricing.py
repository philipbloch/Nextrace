"""Token cost estimation with provider/model presets."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

PRICING_FILE_ENV = "NEXTRACE_PRICING_FILE"
DEFAULT_PRICING_FILE = Path("~/.nextrace/pricing.json")


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None
    cache_write_5m_per_million: float | None = None
    cache_write_1h_per_million: float | None = None
    effective_from: date | None = None
    effective_until: date | None = None


class PricingRegistry:
    """A small configurable registry for token pricing.

    Built-in presets are intentionally limited to token-priced text models. Apps
    can still register or override prices for private models and custom billing
    arrangements.
    """

    def __init__(self) -> None:
        self._prices: dict[tuple[str, str, str], list[ModelPrice]] = {}
        self._exact_prices: dict[tuple[str, str, str], list[ModelPrice]] = {}
        self._aliases: dict[tuple[str, str], tuple[str, str]] = {}
        self._exact_aliases: dict[tuple[str, str], tuple[str, str]] = {}
        self._provider_aliases: dict[str, str] = {
            "codex": "openai",
            "openai": "openai",
            "shopify-proxy": "openai",
            "anthropic": "anthropic",
            "claude": "anthropic",
            "gemini": "gemini",
            "google": "gemini",
            "google-ai": "gemini",
        }

    def register(
        self,
        *,
        provider: str,
        model: str,
        input_per_million: float,
        output_per_million: float,
        cached_input_per_million: float | None = None,
        cache_write_5m_per_million: float | None = None,
        cache_write_1h_per_million: float | None = None,
        effective_from: date | str | None = None,
        effective_until: date | str | None = None,
        mode: str = "standard",
        aliases: tuple[str, ...] = (),
    ) -> None:
        self._register_price(
            provider=provider,
            model=model,
            input_per_million=input_per_million,
            output_per_million=output_per_million,
            cached_input_per_million=cached_input_per_million,
            cache_write_5m_per_million=cache_write_5m_per_million,
            cache_write_1h_per_million=cache_write_1h_per_million,
            effective_from=effective_from,
            effective_until=effective_until,
            mode=mode,
            aliases=aliases,
            exact_provider=False,
        )

    def register_override(
        self,
        *,
        provider: str,
        model: str,
        input_per_million: float,
        output_per_million: float,
        cached_input_per_million: float | None = None,
        cache_write_5m_per_million: float | None = None,
        cache_write_1h_per_million: float | None = None,
        effective_from: date | str | None = None,
        effective_until: date | str | None = None,
        mode: str = "standard",
        aliases: tuple[str, ...] = (),
    ) -> None:
        """Register an exact provider/model override before built-in aliases apply."""
        self._register_price(
            provider=provider,
            model=model,
            input_per_million=input_per_million,
            output_per_million=output_per_million,
            cached_input_per_million=cached_input_per_million,
            cache_write_5m_per_million=cache_write_5m_per_million,
            cache_write_1h_per_million=cache_write_1h_per_million,
            effective_from=effective_from,
            effective_until=effective_until,
            mode=mode,
            aliases=aliases,
            exact_provider=True,
        )

    def register_alias(
        self,
        *,
        provider: str,
        alias: str,
        canonical_provider: str,
        canonical_model: str,
    ) -> None:
        self._aliases[(self._canonical_provider(provider), self._normalize_model(alias))] = (
            self._canonical_provider(canonical_provider),
            self._normalize_model(canonical_model),
        )

    def estimate(
        self,
        *,
        provider: str | None,
        model: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None = None,
        cache_write_5m_tokens: int | None = None,
        cache_write_1h_tokens: int | None = None,
        effective_at: date | datetime | str | None = None,
        mode: str = "standard",
    ) -> float | None:
        if not provider or not model or input_tokens is None or output_tokens is None:
            return None
        price = self.price_for(
            provider=provider,
            model=model,
            effective_at=effective_at,
            mode=mode,
        )
        if price is None:
            return None

        input_count = max(input_tokens, 0)
        output_count = max(output_tokens, 0)
        cached_count = min(max(cached_input_tokens or 0, 0), input_count)
        uncached_count = max(input_count - cached_count, 0)
        cache_read_price = (
            price.cached_input_per_million
            if price.cached_input_per_million is not None
            else price.input_per_million
        )
        write_5m_price = (
            price.cache_write_5m_per_million
            if price.cache_write_5m_per_million is not None
            else price.input_per_million
        )
        write_1h_price = (
            price.cache_write_1h_per_million
            if price.cache_write_1h_per_million is not None
            else write_5m_price
        )

        return round(
            (uncached_count / 1_000_000 * price.input_per_million)
            + (cached_count / 1_000_000 * cache_read_price)
            + ((cache_write_5m_tokens or 0) / 1_000_000 * write_5m_price)
            + ((cache_write_1h_tokens or 0) / 1_000_000 * write_1h_price)
            + (output_count / 1_000_000 * price.output_per_million),
            8,
        )

    def price_for(
        self,
        *,
        provider: str,
        model: str,
        effective_at: date | datetime | str | None = None,
        mode: str = "standard",
    ) -> ModelPrice | None:
        resolved_mode = self._normalize_mode(mode)
        as_of = _coerce_date(effective_at) or date.today()
        exact_provider, exact_model = self._resolve_exact(provider, model)
        exact_price = self._price_from(
            self._exact_prices,
            exact_provider,
            exact_model,
            resolved_mode,
            as_of,
        )
        if exact_price is not None:
            return exact_price

        canonical_provider, canonical_model = self._resolve(provider, model)
        return self._price_from(
            self._prices,
            canonical_provider,
            canonical_model,
            resolved_mode,
            as_of,
        )

    def _register_price(
        self,
        *,
        provider: str,
        model: str,
        input_per_million: float,
        output_per_million: float,
        cached_input_per_million: float | None,
        cache_write_5m_per_million: float | None,
        cache_write_1h_per_million: float | None,
        effective_from: date | str | None,
        effective_until: date | str | None,
        mode: str,
        aliases: tuple[str, ...],
        exact_provider: bool,
    ) -> None:
        provider_key = (
            self._normalize_provider(provider) if exact_provider else self._canonical_provider(provider)
        )
        model_key = self._normalize_model(model)
        key = (provider_key, model_key, self._normalize_mode(mode))
        prices = self._exact_prices if exact_provider else self._prices
        prices.setdefault(key, []).append(
            ModelPrice(
                input_per_million=input_per_million,
                output_per_million=output_per_million,
                cached_input_per_million=cached_input_per_million,
                cache_write_5m_per_million=cache_write_5m_per_million,
                cache_write_1h_per_million=cache_write_1h_per_million,
                effective_from=_coerce_date(effective_from),
                effective_until=_coerce_date(effective_until),
            )
        )
        prices[key].sort(key=lambda item: item.effective_from or date.min, reverse=True)
        for alias in aliases:
            if exact_provider:
                self._exact_aliases[(provider_key, self._normalize_model(alias))] = (
                    provider_key,
                    model_key,
                )
            else:
                self.register_alias(
                    provider=provider_key,
                    alias=alias,
                    canonical_provider=provider_key,
                    canonical_model=model_key,
                )

    def _price_from(
        self,
        prices: dict[tuple[str, str, str], list[ModelPrice]],
        provider: str,
        model: str,
        mode: str,
        as_of: date,
    ) -> ModelPrice | None:
        candidates = prices.get((provider, model, mode))
        if not candidates and mode != "standard":
            candidates = prices.get((provider, model, "standard"))
        if not candidates:
            return None
        for candidate in candidates:
            if candidate.effective_from and as_of < candidate.effective_from:
                continue
            if candidate.effective_until and as_of >= candidate.effective_until:
                continue
            return candidate
        return None

    def _resolve_exact(self, provider: str, model: str) -> tuple[str, str]:
        exact_provider = self._normalize_provider(provider)
        exact_model = self._normalize_model(model)
        return self._exact_aliases.get(
            (exact_provider, exact_model),
            (exact_provider, exact_model),
        )

    def _resolve(self, provider: str, model: str) -> tuple[str, str]:
        canonical_provider = self._canonical_provider(provider)
        canonical_model = self._normalize_model(model)
        return self._aliases.get(
            (canonical_provider, canonical_model),
            (canonical_provider, canonical_model),
        )

    def _canonical_provider(self, provider: str) -> str:
        normalized = self._normalize_provider(provider)
        return self._provider_aliases.get(normalized, normalized)

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        return provider.strip().lower()

    @staticmethod
    def _normalize_model(model: str) -> str:
        normalized = model.strip().lower()
        normalized = re.sub(r"\s+\(.+\)$", "", normalized)
        return normalized

    @staticmethod
    def _normalize_mode(mode: str | None) -> str:
        return (mode or "standard").strip().lower()


def _coerce_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"Unsupported date value: {type(value).__name__}")


def load_configured_pricing(
    registry: PricingRegistry,
    *,
    env: Mapping[str, str] | None = None,
    default_path: str | Path = DEFAULT_PRICING_FILE,
) -> None:
    """Load optional pricing overrides from env or the default user config path."""
    source_env = env if env is not None else os.environ
    configured_path = source_env.get(PRICING_FILE_ENV)
    if configured_path:
        load_pricing_file(registry, configured_path)
        return

    path = Path(default_path).expanduser()
    if path.exists():
        load_pricing_file(registry, path)


def load_pricing_file(registry: PricingRegistry, path: str | Path) -> None:
    """Load exact provider/model pricing overrides from a JSON file."""
    file_path = Path(path).expanduser()
    with file_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("prices")
    else:
        rows = None
    if not isinstance(rows, list):
        raise ValueError("Pricing file must contain a top-level prices list")

    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Pricing row {index} must be an object")
        register = (
            registry.register_override
            if _bool_value(row, "exact_provider", index, True)
            else registry.register
        )
        register(
            provider=_string_value(row, "provider", index),
            model=_string_value(row, "model", index),
            input_per_million=_float_value(row, "input_per_million", index),
            output_per_million=_float_value(row, "output_per_million", index),
            cached_input_per_million=_optional_float_value(row, "cached_input_per_million", index),
            cache_write_5m_per_million=_optional_float_value(row, "cache_write_5m_per_million", index),
            cache_write_1h_per_million=_optional_float_value(row, "cache_write_1h_per_million", index),
            effective_from=_optional_string_value(row, "effective_from", index),
            effective_until=_optional_string_value(row, "effective_until", index),
            mode=_optional_string_value(row, "mode", index) or "standard",
            aliases=_aliases_value(row, index),
        )


def _string_value(row: dict[str, object], key: str, index: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Pricing row {index} must include a non-empty {key}")
    return value


def _optional_string_value(row: dict[str, object], key: str, index: int) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Pricing row {index} field {key} must be a string")
    return value


def _float_value(row: dict[str, object], key: str, index: int) -> float:
    value = _optional_float_value(row, key, index)
    if value is None:
        raise ValueError(f"Pricing row {index} must include numeric {key}")
    return value


def _optional_float_value(row: dict[str, object], key: str, index: int) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Pricing row {index} field {key} must be numeric")
    return float(value)


def _bool_value(row: dict[str, object], key: str, index: int, default: bool) -> bool:
    value = row.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Pricing row {index} field {key} must be true or false")
    return value


def _aliases_value(row: dict[str, object], index: int) -> tuple[str, ...]:
    value = row.get("aliases")
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Pricing row {index} field aliases must be a list of strings")
    return tuple(value)


def _build_default_pricing_registry() -> PricingRegistry:
    registry = PricingRegistry()
    _register_openai_defaults(registry)
    _register_anthropic_defaults(registry)
    _register_gemini_defaults(registry)
    load_configured_pricing(registry)
    return registry


def _register_openai_defaults(registry: PricingRegistry) -> None:
    rows: tuple[tuple[str, float, float | None, float | None, float], ...] = (
        ("gpt-5.6-sol", 5.0, 0.50, 6.25, 30.0),
        ("gpt-5.6-terra", 2.50, 0.25, 3.125, 15.0),
        ("gpt-5.6-luna", 1.0, 0.10, 1.25, 6.0),
        ("gpt-5.5", 5.0, 0.50, None, 30.0),
        ("gpt-5.5-pro", 30.0, None, None, 180.0),
        ("gpt-5.4", 2.50, 0.25, None, 15.0),
        ("gpt-5.4-mini", 0.75, 0.075, None, 4.50),
        ("gpt-5.4-nano", 0.20, 0.02, None, 1.25),
        ("gpt-5.4-pro", 30.0, None, None, 180.0),
        ("gpt-5.3-codex", 1.75, 0.175, None, 14.0),
        ("gpt-5.2", 1.75, 0.175, None, 14.0),
        ("gpt-5.2-pro", 21.0, None, None, 168.0),
        ("gpt-5.2-codex", 1.75, 0.175, None, 14.0),
        ("gpt-5.1", 1.25, 0.125, None, 10.0),
        ("gpt-5.1-codex-max", 1.25, 0.125, None, 10.0),
        ("gpt-5.1-codex", 1.25, 0.125, None, 10.0),
        ("gpt-5.1-codex-mini", 0.25, 0.025, None, 2.0),
        ("gpt-5", 1.25, 0.125, None, 10.0),
        ("gpt-5-codex", 1.25, 0.125, None, 10.0),
        ("gpt-5-mini", 0.25, 0.025, None, 2.0),
        ("gpt-5-nano", 0.05, 0.005, None, 0.40),
        ("gpt-5-pro", 15.0, None, None, 120.0),
        ("gpt-4.1", 2.0, 0.50, None, 8.0),
        ("gpt-4.1-mini", 0.40, 0.10, None, 1.60),
        ("gpt-4.1-nano", 0.10, 0.025, None, 0.40),
        ("gpt-4o", 2.50, 1.25, None, 10.0),
        ("gpt-4o-2024-05-13", 5.0, None, None, 15.0),
        ("gpt-4o-mini", 0.15, 0.075, None, 0.60),
        ("o1", 15.0, 7.50, None, 60.0),
        ("o1-pro", 150.0, None, None, 600.0),
        ("o3-pro", 20.0, None, None, 80.0),
        ("o3", 2.0, 0.50, None, 8.0),
        ("o4-mini", 1.10, 0.275, None, 4.40),
        ("o3-mini", 1.10, 0.55, None, 4.40),
        ("o1-mini", 1.10, 0.55, None, 4.40),
        ("codex-mini-latest", 1.50, 0.375, None, 6.0),
        ("chat-latest", 5.0, 0.50, None, 30.0),
        ("gpt-5.3-chat-latest", 1.75, 0.175, None, 14.0),
        ("gpt-5.2-chat-latest", 1.75, 0.175, None, 14.0),
        ("gpt-5.1-chat-latest", 1.25, 0.125, None, 10.0),
        ("gpt-5-chat-latest", 1.25, 0.125, None, 10.0),
        ("chatgpt-4o-latest", 5.0, None, None, 15.0),
    )
    for model, input_price, cached_price, cache_write_price, output_price in rows:
        registry.register(
            provider="openai",
            model=model,
            input_per_million=input_price,
            cached_input_per_million=cached_price,
            cache_write_5m_per_million=cache_write_price,
            output_per_million=output_price,
        )


def _register_anthropic_defaults(registry: PricingRegistry) -> None:
    def add(
        model: str,
        input_price: float,
        cache_5m: float,
        cache_1h: float,
        cache_read: float,
        output_price: float,
        *,
        effective_from: str | None = None,
        effective_until: str | None = None,
        aliases: tuple[str, ...] = (),
    ) -> None:
        registry.register(
            provider="anthropic",
            model=model,
            input_per_million=input_price,
            output_per_million=output_price,
            cached_input_per_million=cache_read,
            cache_write_5m_per_million=cache_5m,
            cache_write_1h_per_million=cache_1h,
            effective_from=effective_from,
            effective_until=effective_until,
            aliases=aliases,
        )

    add("claude-fable-5", 10.0, 12.50, 20.0, 1.0, 50.0)
    add("claude-mythos-5", 10.0, 12.50, 20.0, 1.0, 50.0, aliases=("claude-mythos-preview",))
    for model in ("claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5"):
        add(model, 5.0, 6.25, 10.0, 0.50, 25.0)
    add("claude-opus-4-1", 15.0, 18.75, 30.0, 1.50, 75.0)
    add("claude-opus-4", 15.0, 18.75, 30.0, 1.50, 75.0)
    add("claude-sonnet-5", 2.0, 2.50, 4.0, 0.20, 10.0, effective_until="2026-09-01")
    add("claude-sonnet-5", 3.0, 3.75, 6.0, 0.30, 15.0, effective_from="2026-09-01")
    for model in ("claude-sonnet-4-6", "claude-sonnet-4-5", "claude-sonnet-4"):
        add(model, 3.0, 3.75, 6.0, 0.30, 15.0)
    add("claude-haiku-4-5", 1.0, 1.25, 2.0, 0.10, 5.0)
    add("claude-haiku-3-5", 0.80, 1.0, 1.60, 0.08, 4.0)


def _register_gemini_defaults(registry: PricingRegistry) -> None:
    rows: tuple[tuple[str, float, float, float | None, str], ...] = (
        ("gemini-3.5-flash", 1.50, 9.0, 0.15, "standard"),
        ("gemini-3.5-flash", 0.75, 4.50, 0.075, "batch"),
        ("gemini-3.5-flash", 0.75, 4.50, 0.08, "flex"),
        ("gemini-3.5-flash", 2.70, 16.20, 0.27, "priority"),
        ("gemini-3.1-flash-lite", 0.25, 1.50, 0.025, "standard"),
        ("gemini-3.1-flash-lite", 0.125, 0.75, 0.0125, "batch"),
        ("gemini-3.1-flash-lite", 0.125, 0.75, 0.0125, "flex"),
        ("gemini-3.1-flash-lite", 0.45, 2.70, 0.045, "priority"),
        ("gemini-3-flash-preview", 0.50, 3.0, 0.05, "standard"),
        ("gemini-3-flash-preview", 0.25, 1.50, 0.05, "batch"),
        ("gemini-3-flash-preview", 0.25, 1.50, 0.05, "flex"),
        ("gemini-3-flash-preview", 0.90, 5.40, 0.09, "priority"),
        ("gemini-2.5-pro", 1.25, 10.0, 0.125, "standard"),
        ("gemini-2.5-pro", 0.625, 5.0, 0.125, "batch"),
        ("gemini-2.5-pro", 0.625, 5.0, 0.125, "flex"),
        ("gemini-2.5-pro", 2.25, 18.0, 0.225, "priority"),
        ("gemini-2.5-flash", 0.30, 2.50, 0.03, "standard"),
        ("gemini-2.5-flash", 0.15, 1.25, 0.03, "batch"),
        ("gemini-2.5-flash", 0.15, 1.25, 0.03, "flex"),
        ("gemini-2.5-flash", 0.54, 4.50, 0.054, "priority"),
        ("gemini-2.5-flash-lite", 0.10, 0.40, 0.01, "standard"),
        ("gemini-2.5-flash-lite", 0.05, 0.20, 0.01, "batch"),
        ("gemini-2.5-flash-lite", 0.05, 0.20, 0.01, "flex"),
        ("gemini-2.5-flash-lite", 0.18, 0.72, 0.018, "priority"),
        ("gemini-2.5-flash-lite-preview-09-2025", 0.10, 0.40, 0.01, "standard"),
        ("gemini-2.5-computer-use-preview-10-2025", 1.25, 10.0, None, "standard"),
    )
    for model, input_price, output_price, cached_price, mode in rows:
        registry.register(
            provider="gemini",
            model=model,
            input_per_million=input_price,
            output_per_million=output_price,
            cached_input_per_million=cached_price,
            mode=mode,
        )


default_pricing_registry = _build_default_pricing_registry()
