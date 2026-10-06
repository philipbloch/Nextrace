from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from nextrace.agent_events import AgentRecording
from nextrace.storage import SQLiteStore
from nextrace.usage import UsageImportStats as ClaudeUsageImportStats
from nextrace.usage import (
    find_jsonl,
    import_files,
    int_or_none,
    parse_timestamp,
    read_jsonl,
    stable_id,
    string_or_none,
    token_count,
)


def claude_project_slug(project_path: str | Path) -> str:
    return "-" + str(Path(project_path).expanduser()).strip("/").replace("/", "-")


def find_claude_project_files(
    *,
    claude_home: str | Path,
    project_path: str | Path | None = None,
    latest: int | None = None,
) -> list[Path]:
    root = Path(claude_home).expanduser() / "projects"
    if project_path:
        root /= claude_project_slug(project_path)
    return find_jsonl(root, "*.jsonl" if project_path else "*/*.jsonl", latest)


def import_claude_usage(
    *,
    store: SQLiteStore,
    application: str,
    files: Iterable[str | Path],
    provider: str = "anthropic",
) -> ClaudeUsageImportStats:
    return import_files(files, lambda path: _import_claude_file(store, application, path, provider))


def _import_claude_file(
    store: SQLiteStore, application: str, file_path: Path, provider: str
) -> int:
    recording = AgentRecording("claude-code", file_path.stem, application, str(file_path.resolve()))
    event_turns: dict[str, str] = {}
    for line_number, event in read_jsonl(file_path):
        recording.session_id = str(
            event.get("sessionId") or event.get("session_id") or recording.session_id
        )
        recording.cwd = string_or_none(event.get("cwd")) or recording.cwd
        timestamp = parse_timestamp(event.get("timestamp"))
        parent = event_turns.get(event.get("parentUuid"))
        if parent:
            recording.turn_id = parent
        message = event.get("message")
        if not isinstance(message, dict):
            if event.get("type") == "system" and event.get("subtype") == "turn_duration":
                recording.finish(timestamp)
            continue
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        tool_results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
        if event.get("type") == "user" and not tool_results:
            # Synthetic/meta user messages are context updates, not a new human turn.
            if event.get("isMeta") or event.get("isCompactSummary"):
                continue
            recording.begin(
                str(
                    event.get("uuid")
                    or (f"started-{timestamp}" if timestamp is not None else "unattributed-user")
                ),
                timestamp,
            )
        event_id = event.get("uuid")
        if event_id and recording.turn_id:
            event_turns[event_id] = recording.turn_id
        if timestamp is None:
            continue
        for result in tool_results:
            if result.get("tool_use_id"):
                recording.tool_result(
                    str(result["tool_use_id"]), timestamp, result.get("is_error") is True
                )
        if event.get("type") != "assistant":
            continue
        model = string_or_none(message.get("model"))
        usage = message.get("usage")
        parent_span = None
        if model and model != "<synthetic>" and isinstance(usage, dict):
            message_id = str(message.get("id") or event.get("uuid") or line_number)
            parent_span = stable_id("claude-model", recording.session_id, message_id)
            input_tokens = token_count(usage.get("input_tokens"))
            output_tokens = token_count(usage.get("output_tokens"))
            cache_write = token_count(usage.get("cache_creation_input_tokens"))
            cache_read = token_count(usage.get("cache_read_input_tokens"))
            recording.model(
                span_id=parent_span,
                timestamp=timestamp,
                provider=provider,
                model=model,
                input_tokens=input_tokens + cache_read + cache_write,
                output_tokens=output_tokens,
                total_tokens=input_tokens + cache_read + cache_write + output_tokens,
                metadata={
                    "message_id": message_id,
                    "request_id": string_or_none(event.get("requestId")),
                    "cache_creation_input_tokens": cache_write,
                    "cache_read_input_tokens": cache_read,
                    "web_search_requests": _server_tool_count(usage, "web_search_requests"),
                    "web_fetch_requests": _server_tool_count(usage, "web_fetch_requests"),
                },
            )
        tool_uses = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        for tool in tool_uses:
            if tool.get("id"):
                recording.tool(
                    str(tool["id"]), str(tool.get("name") or "tool"), timestamp, parent_span
                )
        if message.get("stop_reason") in {"end_turn", "stop_sequence"}:
            recording.finish(timestamp)
    return recording.persist(store)


def _server_tool_count(usage, key):
    value = usage.get("server_tool_use")
    return int_or_none(value.get(key)) if isinstance(value, dict) else None
