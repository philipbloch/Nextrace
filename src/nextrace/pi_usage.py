from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from nextrace.agent_events import AgentRecording
from nextrace.storage import SQLiteStore
from nextrace.usage import UsageImportStats as PiUsageImportStats
from nextrace.usage import (
    find_jsonl,
    import_files,
    parse_timestamp,
    read_jsonl,
    stable_id,
    string_or_none,
    token_count,
)


def pi_project_slug(project_path: str | Path) -> str:
    return "--" + str(Path(project_path).expanduser()).strip("/").replace("/", "-") + "--"


def find_pi_session_files(
    *,
    pi_home: str | Path,
    project_path: str | Path | None = None,
    latest: int | None = None,
) -> list[Path]:
    root = Path(pi_home).expanduser() / "sessions"
    if project_path:
        root /= pi_project_slug(project_path)
    return find_jsonl(root, "*.jsonl" if project_path else "*/*.jsonl", latest)


def import_pi_usage(
    *, store: SQLiteStore, application: str, files: Iterable[str | Path]
) -> PiUsageImportStats:
    return import_files(files, lambda path: _import_pi_file(store, application, path))


def _import_pi_file(store: SQLiteStore, application: str, file_path: Path) -> int:
    recording = AgentRecording("pi", file_path.stem, application, str(file_path.resolve()))
    event_turns = {}
    for line_number, event in read_jsonl(file_path):
        if event.get("type") == "session":
            recording.session_id = str(event.get("id") or recording.session_id)
            recording.cwd = string_or_none(event.get("cwd")) or recording.cwd
            continue
        if event.get("type") != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        timestamp = _parse_timestamp(event.get("timestamp") or message.get("timestamp"))
        parent_turn = event_turns.get(event.get("parentId"))
        if parent_turn:
            recording.turn_id = parent_turn
        message_id = str(event.get("id") or message.get("responseId") or line_number)
        if message.get("role") == "user":
            recording.begin(message_id, timestamp)
        if recording.turn_id:
            event_turns[message_id] = recording.turn_id
        if timestamp is None:
            continue
        if message.get("role") == "toolResult":
            recording.tool_result(
                str(message.get("toolCallId")), timestamp, message.get("isError") is True
            )
        if message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        provider, model = (
            string_or_none(message.get("provider")),
            string_or_none(message.get("model")),
        )
        parent_span = None
        reason = string_or_none(message.get("stopReason"))
        status = {"aborted": "interrupted", "error": "error"}.get(reason, "ok")
        if provider and model and isinstance(usage, dict):
            input_tokens = token_count(usage.get("input"))
            output_tokens = token_count(usage.get("output"))
            cache_read, cache_write = (
                token_count(usage.get("cacheRead")),
                token_count(usage.get("cacheWrite")),
            )
            parent_span = stable_id("pi-model", recording.session_id, message_id)
            recording.model(
                span_id=parent_span,
                timestamp=timestamp,
                provider=provider,
                model=model,
                input_tokens=input_tokens + cache_read + cache_write,
                output_tokens=output_tokens,
                total_tokens=token_count(usage.get("totalTokens"))
                or input_tokens + cache_read + cache_write + output_tokens,
                status=status,
                error=f"Pi model call ended with {reason}"
                if reason in {"error", "aborted"}
                else None,
                metadata={
                    "cache_read_input_tokens": cache_read,
                    "cache_write_input_tokens": cache_write,
                    "stop_reason": reason,
                    "message_id": message_id,
                },
            )
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "toolCall" and block.get("id"):
                recording.tool(
                    str(block["id"]), str(block.get("name") or "tool"), timestamp, parent_span
                )
        if reason in {"stop", "error", "aborted"}:
            recording.finish(timestamp, status)
    return recording.persist(store)


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if not math.isfinite(timestamp):
            return None
        return timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
    return parse_timestamp(value)
