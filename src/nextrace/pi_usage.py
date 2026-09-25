"""Import local Pi agent usage into Nextrace traces."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nextrace.pricing import default_pricing_registry
from nextrace.storage import SQLiteStore
from nextrace.types import SpanRecord, TraceRecord


@dataclass(frozen=True)
class PiUsageImportStats:
    imported: int
    files: int


def pi_project_slug(project_path: str | Path) -> str:
    """Return Pi's on-disk session directory name for an absolute project path."""
    return "--" + str(Path(project_path).expanduser()).strip("/").replace("/", "-") + "--"


def find_pi_session_files(
    *,
    pi_home: str | Path,
    project_path: str | Path | None = None,
    latest: int | None = None,
) -> list[Path]:
    """Find Pi JSONL session files for one project or across all projects."""
    home = Path(pi_home).expanduser()
    sessions_root = home / "sessions"
    if project_path:
        root = sessions_root / pi_project_slug(project_path)
        files = list(root.glob("*.jsonl")) if root.exists() else []
    else:
        files = list(sessions_root.glob("*/*.jsonl")) if sessions_root.exists() else []
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    if latest:
        files = files[:latest]
    return files


def import_pi_usage(
    *,
    store: SQLiteStore,
    application: str,
    files: Iterable[str | Path],
) -> PiUsageImportStats:
    """Import Pi assistant-message usage as redacted model spans."""
    imported = 0
    file_count = 0
    for file_path in [Path(path).expanduser() for path in files]:
        file_count += 1
        imported += _import_pi_file(
            store=store,
            application=application,
            file_path=file_path,
        )
    return PiUsageImportStats(imported=imported, files=file_count)


def _import_pi_file(*, store: SQLiteStore, application: str, file_path: Path) -> int:
    imported = 0
    session_id = file_path.stem
    project_path: str | None = None

    with file_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "session":
                session_id = str(event.get("id") or session_id)
                project_path = _string_or_none(event.get("cwd")) or project_path
                continue
            if event.get("type") != "message":
                continue

            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            provider = _string_or_none(message.get("provider"))
            model = _string_or_none(message.get("model"))
            usage = message.get("usage")
            if not provider or not model or not isinstance(usage, dict):
                continue
            timestamp = _parse_timestamp(event.get("timestamp") or message.get("timestamp"))
            if timestamp is None:
                continue

            message_id = str(event.get("id") or message.get("responseId") or line_number)
            input_tokens = _int_or_zero(usage.get("input"))
            output_tokens = _int_or_zero(usage.get("output"))
            cache_read_tokens = _int_or_zero(usage.get("cacheRead"))
            cache_write_tokens = _int_or_zero(usage.get("cacheWrite"))
            calculated_total = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
            total_tokens = _int_or_zero(usage.get("totalTokens")) or calculated_total
            recorded_cost = _pi_recorded_cost(usage)
            estimated_cost = default_pricing_registry.estimate(
                provider=provider,
                model=model,
                input_tokens=input_tokens + cache_read_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cache_read_tokens,
                cache_write_5m_tokens=cache_write_tokens,
                effective_at=datetime.fromtimestamp(timestamp, timezone.utc).date(),
            )
            cost_usd = estimated_cost if estimated_cost is not None else recorded_cost
            cost_source = "nextrace-pricing" if estimated_cost is not None else "pi-log"
            stop_reason = _string_or_none(message.get("stopReason"))
            status = "error" if stop_reason in {"error", "aborted"} else "ok"
            error = f"Pi model call ended with {stop_reason}" if status == "error" else None
            trace_id = _stable_id("pi-trace", session_id, message_id, str(line_number))
            span_id = _stable_id("pi-span", session_id, message_id, str(line_number))
            metadata = {
                "source": "pi",
                "pi_session_id": session_id,
                "pi_session_file": str(file_path),
                "pi_line": line_number,
                "pi_message_id": message_id,
                "pi_api": _string_or_none(message.get("api")),
                "pi_response_id": _string_or_none(message.get("responseId")),
                "cwd": project_path,
                "stop_reason": stop_reason,
                "cache_read_input_tokens": cache_read_tokens,
                "cache_write_input_tokens": cache_write_tokens,
                "pi_recorded_cost_usd": recorded_cost,
                "cost_source": cost_source,
                "cost_estimated": cost_usd is not None,
            }
            store.start_trace(
                TraceRecord(
                    id=trace_id,
                    session_id=session_id,
                    application=application,
                    name=f"pi:model:{model}",
                    user_id=None,
                    started_at=timestamp,
                    ended_at=timestamp,
                    duration_ms=0.0,
                    status=status,
                    error=error,
                    tags=["pi", "model"],
                    metadata={
                        "source": "pi",
                        "provider": provider,
                        "model": model,
                        "cost_source": cost_source,
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
                    name=f"{provider}:{model}",
                    provider=provider,
                    model=model,
                    started_at=timestamp,
                    ended_at=timestamp,
                    duration_ms=0.0,
                    prompt={"redacted": True, "source": "pi"},
                    response={"redacted": True, "source": "pi"},
                    input_tokens=input_tokens + cache_read_tokens + cache_write_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    retry_count=0,
                    status=status,
                    error=error,
                    metadata=metadata,
                )
            )
            imported += 1
    return imported


def _pi_recorded_cost(usage: dict[str, Any]) -> float | None:
    cost = usage.get("cost")
    if not isinstance(cost, dict):
        return None
    value = cost.get("total")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _stable_id(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:32]


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        return timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
    if not isinstance(value, str):
        return None
    normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    return 0
