"""Recalculate stored model costs from configured pricing."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nextrace.pricing import PricingRegistry, default_pricing_registry


@dataclass(frozen=True)
class RepriceStats:
    matched: int
    repriced: int
    skipped: int
    cost_before: float
    cost_after: float


def reprice_model_spans(
    *,
    db_path: str | Path,
    registry: PricingRegistry = default_pricing_registry,
    application: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    since: float | None = None,
    until: float | None = None,
    dry_run: bool = False,
) -> RepriceStats:
    """Recalculate cost_usd for stored model spans using current pricing."""
    path = Path(db_path).expanduser()
    clauses = ["s.kind = 'model'"]
    params: list[Any] = []
    if application:
        clauses.append("t.application = ?")
        params.append(application)
    if provider:
        clauses.append("s.provider = ?")
        params.append(provider)
    if model:
        clauses.append("s.model = ?")
        params.append(model)
    if since is not None:
        clauses.append("t.started_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("t.started_at < ?")
        params.append(until)

    where = " AND ".join(clauses)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT
                s.id,
                s.provider,
                s.model,
                s.started_at,
                s.input_tokens,
                s.output_tokens,
                s.cost_usd,
                s.metadata
            FROM spans s
            JOIN traces t ON t.id = s.trace_id
            WHERE {where}
            """,
            params,
        ).fetchall()

        updates: list[tuple[float, str]] = []
        skipped = 0
        cost_before = 0.0
        cost_after = 0.0
        for row in rows:
            estimate = _estimate_row_cost(registry, row)
            if estimate is None:
                skipped += 1
                continue
            cost_before += float(row["cost_usd"] or 0.0)
            cost_after += estimate
            updates.append((estimate, row["id"]))

        if updates and not dry_run:
            conn.executemany("UPDATE spans SET cost_usd = ? WHERE id = ?", updates)

    return RepriceStats(
        matched=len(rows),
        repriced=len(updates),
        skipped=skipped,
        cost_before=round(cost_before, 8),
        cost_after=round(cost_after, 8),
    )


def _estimate_row_cost(registry: PricingRegistry, row: sqlite3.Row) -> float | None:
    provider = row["provider"]
    model = row["model"]
    input_tokens = _int_or_none(row["input_tokens"])
    output_tokens = _int_or_none(row["output_tokens"])
    if provider is None or model is None or input_tokens is None or output_tokens is None:
        return None

    metadata = _load_metadata(row["metadata"])
    cached_input_tokens = _int_or_none(metadata.get("cached_input_tokens"))
    cache_write_5m_tokens = _int_or_none(metadata.get("cache_write_5m_input_tokens"))
    cache_write_1h_tokens = _int_or_none(metadata.get("cache_write_1h_input_tokens"))

    if metadata.get("source") == "claude-code":
        cache_creation = _int_or_none(metadata.get("cache_creation_input_tokens")) or 0
        cached_input_tokens = _int_or_none(metadata.get("cache_read_input_tokens"))
        input_tokens = max(input_tokens - cache_creation, 0)
    elif metadata.get("source") == "pi":
        cached_input_tokens = _int_or_none(metadata.get("cache_read_input_tokens"))
        cache_write_5m_tokens = _int_or_none(metadata.get("cache_write_input_tokens"))
        input_tokens = max(input_tokens - (cache_write_5m_tokens or 0), 0)

    return registry.estimate(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_5m_tokens=cache_write_5m_tokens,
        cache_write_1h_tokens=cache_write_1h_tokens,
        effective_at=datetime.fromtimestamp(float(row["started_at"])),
    )


def _load_metadata(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
