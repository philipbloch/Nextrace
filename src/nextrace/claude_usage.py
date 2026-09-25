"""Import local Claude Code usage into Nextrace traces."""

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
class ClaudeUsageImportStats:
    imported: int
    files: int


def claude_project_slug(project_path: str | Path) -> str:
    """Return Claude Code's on-disk project slug for an absolute path."""
    return "-" + str(Path(project_path).expanduser()).strip("/").replace("/", "-")


def find_claude_project_files(
    *,
    claude_home: str | Path,
    project_path: str | Path | None = None,
    latest: int | None = None,
) -> list[Path]:
    """Find Claude Code JSONL transcript files."""
    home = Path(claude_home).expanduser()
    if project_path:
        root = home / "projects" / claude_project_slug(project_path)
        files = list(root.glob("*.jsonl")) if root.exists() else []
    else:
        root = home / "projects"
        files = list(root.glob("*/*.jsonl")) if root.exists() else []
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    if latest:
        files = files[:latest]
    return files


def import_claude_usage(
    *,
    store: SQLiteStore,
    application: str,
    files: Iterable[str | Path],
    provider: str = "anthropic",
) -> ClaudeUsageImportStats:
    """Import Claude Code assistant message usage as redacted model spans."""
    imported = 0
    file_count = 0
    for file_path in [Path(path).expanduser() for path in files]:
        file_count += 1
        imported += _import_claude_file(
            store=store,
            application=application,
            file_path=file_path,
            provider=provider,
        )
    return ClaudeUsageImportStats(imported=imported, files=file_count)


def _import_claude_file(
    *,
    store: SQLiteStore,
    application: str,
    file_path: Path,
    provider: str,
) -> int:
    imported = 0
    with file_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "assistant":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            model = _string_or_none(message.get("model"))
            if not model or model == "<synthetic>":
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            timestamp = _parse_timestamp(event.get("timestamp"))
            if timestamp is None:
                continue

            session_id = str(event.get("sessionId") or event.get("session_id") or file_path.stem)
            message_id = str(message.get("id") or event.get("uuid") or line_number)
            trace_id = _stable_id("claude-trace", session_id, message_id, str(line_number))
            span_id = _stable_id("claude-span", session_id, message_id, str(line_number))
            input_tokens = _int_or_none(usage.get("input_tokens")) or 0
            output_tokens = _int_or_none(usage.get("output_tokens")) or 0
            cache_creation_tokens = _int_or_none(usage.get("cache_creation_input_tokens")) or 0
            cache_read_tokens = _int_or_none(usage.get("cache_read_input_tokens")) or 0
            cache_creation = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
            cache_write_5m_tokens = _int_or_none(cache_creation.get("ephemeral_5m_input_tokens")) or 0
            cache_write_1h_tokens = _int_or_none(cache_creation.get("ephemeral_1h_input_tokens")) or 0
            unknown_cache_write_tokens = max(
                cache_creation_tokens - cache_write_5m_tokens - cache_write_1h_tokens,
                0,
            )
            total_tokens = input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens
            cost_usd = _estimate_claude_cost(
                model=model,
                input_tokens=input_tokens + cache_read_tokens,
                output_tokens=output_tokens,
                cache_write_5m_tokens=cache_write_5m_tokens + unknown_cache_write_tokens,
                cache_write_1h_tokens=cache_write_1h_tokens,
                cache_read_tokens=cache_read_tokens,
                effective_at=datetime.fromtimestamp(timestamp, timezone.utc).date(),
            )
            metadata = {
                "source": "claude-code",
                "claude_session_id": session_id,
                "claude_session_file": str(file_path),
                "claude_line": line_number,
                "claude_message_id": message_id,
                "request_id": _string_or_none(event.get("requestId")),
                "cwd": _string_or_none(event.get("cwd")),
                "service_tier": _string_or_none(usage.get("service_tier")),
                "speed": _string_or_none(usage.get("speed")),
                "inference_geo": _string_or_none(usage.get("inference_geo")),
                "cache_creation_input_tokens": cache_creation_tokens,
                "cache_read_input_tokens": cache_read_tokens,
                "cache_write_5m_input_tokens": cache_write_5m_tokens,
                "cache_write_1h_input_tokens": cache_write_1h_tokens,
                "cache_write_unknown_input_tokens": unknown_cache_write_tokens,
                "web_search_requests": _server_tool_count(usage, "web_search_requests"),
                "web_fetch_requests": _server_tool_count(usage, "web_fetch_requests"),
                "cost_estimated": cost_usd is not None,
            }
            store.start_trace(
                TraceRecord(
                    id=trace_id,
                    session_id=session_id,
                    application=application,
                    name=f"claude-code:model:{model}",
                    user_id=None,
                    started_at=timestamp,
                    ended_at=timestamp,
                    duration_ms=0.0,
                    status="ok",
                    error=None,
                    tags=["claude-code", "model"],
                    metadata={
                        "source": "claude-code",
                        "provider": provider,
                        "model": model,
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
                    prompt={"redacted": True, "source": "claude-code"},
                    response={"redacted": True, "source": "claude-code"},
                    input_tokens=input_tokens + cache_creation_tokens + cache_read_tokens,
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


def _estimate_claude_cost(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_5m_tokens: int,
    cache_write_1h_tokens: int,
    cache_read_tokens: int,
    effective_at: Any,
) -> float | None:
    return default_pricing_registry.estimate(
        provider="anthropic",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cache_read_tokens,
        cache_write_5m_tokens=cache_write_5m_tokens,
        cache_write_1h_tokens=cache_write_1h_tokens,
        effective_at=effective_at,
    )


def _server_tool_count(usage: dict[str, Any], key: str) -> int | None:
    server_tool_use = usage.get("server_tool_use")
    if not isinstance(server_tool_use, dict):
        return None
    return _int_or_none(server_tool_use.get(key))


def _stable_id(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:32]


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


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
