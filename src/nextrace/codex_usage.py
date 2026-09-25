"""Import local Codex model usage into Nextrace traces."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nextrace.pricing import default_pricing_registry
from nextrace.storage import SQLiteStore
from nextrace.types import SpanRecord, TraceRecord


@dataclass(frozen=True)
class CodexUsageImportStats:
    imported: int
    files: int


def find_codex_session_files(
    *,
    codex_home: str | Path,
    session_ids: Iterable[str] | None = None,
    latest: int | None = None,
) -> list[Path]:
    """Find Codex JSONL session files by id, or the latest files when no id is provided."""
    home = Path(codex_home).expanduser()
    roots = [home / "sessions", home / "archived_sessions"]
    files: list[Path] = []
    ids = [item for item in (session_ids or []) if item]
    if ids:
        for session_id in ids:
            for root in roots:
                if root.exists():
                    files.extend(root.rglob(f"*{session_id}*.jsonl"))
    else:
        for root in roots:
            if root.exists():
                files.extend(root.rglob("*.jsonl"))
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        files = files[: latest or 1]
    return sorted(set(files))


def import_codex_usage(
    *,
    store: SQLiteStore,
    application: str,
    files: Iterable[str | Path],
    input_cost_per_million: float | None = None,
    cached_input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> CodexUsageImportStats:
    """Import Codex token counts as redacted model spans.

    Codex session JSONL stores token usage and model metadata, but not raw provider cost.
    Cost is estimated from explicit prices or the configured pricing registry.
    """
    imported = 0
    file_count = 0
    for file_path in [Path(path).expanduser() for path in files]:
        file_count += 1
        imported += _import_codex_file(
            store=store,
            application=application,
            file_path=file_path,
            input_cost_per_million=input_cost_per_million,
            cached_input_cost_per_million=cached_input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            provider_override=provider_override,
            model_override=model_override,
        )
    return CodexUsageImportStats(imported=imported, files=file_count)


def _import_codex_file(
    *,
    store: SQLiteStore,
    application: str,
    file_path: Path,
    input_cost_per_million: float | None,
    cached_input_cost_per_million: float | None,
    output_cost_per_million: float | None,
    provider_override: str | None,
    model_override: str | None,
) -> int:
    session_id = _session_id_from_path(file_path)
    provider = provider_override
    model = model_override
    cwd: str | None = None
    turn_id: str | None = None
    turn_started_at: float | None = None
    reasoning_effort: str | None = None
    imported = 0

    with file_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload") if isinstance(event, dict) else None
            if not isinstance(payload, dict):
                continue

            if event.get("type") == "session_meta":
                session_id = str(payload.get("id") or payload.get("session_id") or session_id)
                provider = provider_override or _string_or_none(payload.get("model_provider")) or provider
                cwd = _string_or_none(payload.get("cwd")) or cwd
                continue

            if event.get("type") == "turn_context":
                turn_id = _string_or_none(payload.get("turn_id")) or turn_id
                model = model_override or _string_or_none(payload.get("model")) or model
                cwd = _string_or_none(payload.get("cwd")) or cwd
                reasoning_effort = _string_or_none(payload.get("effort")) or reasoning_effort
                continue

            if payload.get("type") == "task_started":
                turn_id = _string_or_none(payload.get("turn_id")) or turn_id
                turn_started_at = _number_or_none(payload.get("started_at"))
                continue

            info = payload.get("info")
            if payload.get("type") != "token_count" or not isinstance(info, dict):
                continue
            usage = info.get("last_token_usage")
            if not isinstance(usage, dict):
                continue

            ended_at = _parse_timestamp(event.get("timestamp")) or turn_started_at
            if ended_at is None:
                continue
            started_at = turn_started_at or ended_at
            codex_elapsed_ms = max(0.0, round((ended_at - started_at) * 1000, 3))
            duration_ms = 0.0
            current_provider = provider_override or provider or "codex"
            current_model = _normalize_codex_model(model_override or model or "unknown")
            trace_id = _stable_id("codex-trace", session_id, str(line_number))
            span_id = _stable_id("codex-span", session_id, str(line_number))
            input_tokens = _int_or_none(usage.get("input_tokens"))
            cached_input_tokens = _int_or_none(usage.get("cached_input_tokens"))
            output_tokens = _int_or_none(usage.get("output_tokens"))
            total_tokens = _int_or_none(usage.get("total_tokens"))
            cost_usd = _estimate_cost(
                provider=current_provider,
                model=current_model,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                input_cost_per_million=input_cost_per_million,
                cached_input_cost_per_million=cached_input_cost_per_million,
                output_cost_per_million=output_cost_per_million,
            )
            metadata = {
                "source": "codex",
                "codex_session_id": session_id,
                "codex_session_file": str(file_path),
                "codex_line": line_number,
                "codex_turn_id": turn_id,
                "cwd": cwd,
                "reasoning_effort": reasoning_effort,
                "cached_input_tokens": cached_input_tokens,
                "reasoning_output_tokens": _int_or_none(usage.get("reasoning_output_tokens")),
                "model_context_window": _int_or_none(info.get("model_context_window")),
                "codex_elapsed_ms": codex_elapsed_ms,
                "cost_estimated": cost_usd is not None,
            }
            store.start_trace(
                TraceRecord(
                    id=trace_id,
                    session_id=session_id,
                    application=application,
                    name=f"codex:model:{current_model}",
                    user_id=None,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=duration_ms,
                    status="ok",
                    error=None,
                    tags=["codex", "model"],
                    metadata={
                        "source": "codex",
                        "provider": current_provider,
                        "model": current_model,
                        "cost_estimated": cost_usd is not None,
                    },
                )
            )
            store.record_span(
                SpanRecord(
                    id=span_id,
                    trace_id=trace_id,
                    parent_id=None,
                    kind="model",
                    name=f"{current_provider}:{current_model}",
                    provider=current_provider,
                    model=current_model,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=duration_ms,
                    prompt={"redacted": True, "source": "codex"},
                    response={"redacted": True, "source": "codex"},
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    retry_count=0,
                    status="ok",
                    error=None,
                    metadata=metadata,
                )
            )
            imported += 1
    return imported


def _estimate_cost(
    *,
    provider: str,
    model: str,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    output_tokens: int | None,
    input_cost_per_million: float | None,
    cached_input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> float | None:
    if (
        input_cost_per_million is None
        and cached_input_cost_per_million is None
        and output_cost_per_million is None
    ):
        return default_pricing_registry.estimate(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
    input_price = input_cost_per_million or 0.0
    cached_input_price = cached_input_cost_per_million
    if cached_input_price is None:
        cached_input_price = input_price
    output_price = output_cost_per_million or 0.0
    cached_tokens = min(cached_input_tokens or 0, input_tokens or 0)
    uncached_input_tokens = max((input_tokens or 0) - cached_tokens, 0)
    return round(
        (uncached_input_tokens / 1_000_000 * input_price)
        + (cached_tokens / 1_000_000 * cached_input_price)
        + ((output_tokens or 0) / 1_000_000 * output_price),
        8,
    )


def _stable_id(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:32]


def _session_id_from_path(path: Path) -> str:
    stem = path.stem
    if "-" not in stem:
        return stem
    return stem.rsplit("-", 1)[-1]


def _parse_timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _normalize_codex_model(model: str) -> str:
    if model.startswith("openai-org-") and ":" in model:
        return model.split(":", 1)[1]
    return model


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
