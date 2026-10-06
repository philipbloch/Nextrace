from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path

from nextrace.agent_events import AgentRecording
from nextrace.storage import SQLiteStore
from nextrace.usage import UsageImportStats as CodexUsageImportStats
from nextrace.usage import (
    import_files,
    int_or_none,
    parse_timestamp,
    read_jsonl,
    stable_id,
    string_or_none,
)


def find_codex_session_files(
    *,
    codex_home: str | Path,
    session_ids: Iterable[str] | None = None,
    latest: int | None = None,
) -> list[Path]:
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
    provider_override: str | None = None,
    model_override: str | None = None,
) -> CodexUsageImportStats:
    return import_files(
        files,
        lambda path: _import_codex_file(
            store, application, path, provider_override, model_override
        ),
    )


def _import_codex_file(
    store: SQLiteStore,
    application: str,
    file_path: Path,
    provider_override: str | None,
    model_override: str | None,
) -> int:
    recording = AgentRecording(
        "codex", _session_id_from_path(file_path), application, str(file_path.resolve())
    )
    provider, model, previous_total = (
        provider_override or "codex",
        model_override or "unknown",
        None,
    )
    last_model_span = None
    for _, event in read_jsonl(file_path):
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        timestamp = parse_timestamp(event.get("timestamp"))
        if event.get("type") == "session_meta":
            recording.session_id = str(
                payload.get("id") or payload.get("session_id") or recording.session_id
            )
            provider = (
                provider_override or string_or_none(payload.get("model_provider")) or provider
            )
            recording.cwd = string_or_none(payload.get("cwd")) or recording.cwd
        elif event.get("type") == "turn_context":
            model = model_override or string_or_none(payload.get("model")) or model
            recording.cwd = string_or_none(payload.get("cwd")) or recording.cwd
            if payload.get("turn_id"):
                recording.begin(str(payload["turn_id"]), timestamp)
        elif payload.get("type") == "task_started":
            started = payload.get("started_at")
            if (
                isinstance(started, (int, float))
                and not isinstance(started, bool)
                and math.isfinite(started)
            ):
                timestamp = float(started)
            recording.begin(
                str(
                    payload.get("turn_id")
                    or (f"started-{timestamp}" if timestamp is not None else "unattributed-start")
                ),
                timestamp,
            )
        elif payload.get("type") in {"task_complete", "task_completed", "turn_aborted"}:
            recording.finish(
                timestamp, "interrupted" if payload["type"] == "turn_aborted" else "ok"
            )
        elif event.get("type") == "response_item" and timestamp is not None:
            kind = payload.get("type")
            call_id = string_or_none(payload.get("call_id"))
            if call_id and kind in {"function_call", "custom_tool_call"}:
                recording.tool(call_id, str(payload.get("name") or "tool"), timestamp)
            elif call_id and kind in {"function_call_output", "custom_tool_call_output"}:
                recording.tool_result(call_id, timestamp, payload.get("isError") is True)
        elif payload.get("type") == "token_count" and timestamp is not None:
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            cumulative = info.get("total_token_usage")
            if isinstance(cumulative, dict) and cumulative:
                if cumulative == previous_total:
                    if last_model_span:
                        recording.spans[last_model_span].metadata.setdefault(
                            "usage_snapshot_times", []
                        ).append(timestamp)
                    continue
                previous_total = cumulative
            usage = info.get("last_token_usage")
            if not isinstance(usage, dict):
                continue
            if usage.get("input_tokens") == 0 and usage.get("output_tokens") == 0:
                continue
            last_model_span = recording.model(
                span_id=stable_id(
                    "codex-model",
                    recording.session_id,
                    str(timestamp),
                    json.dumps(cumulative or usage, sort_keys=True),
                ),
                timestamp=timestamp,
                provider=provider,
                model=_normalize_codex_model(model),
                input_tokens=int_or_none(usage.get("input_tokens")),
                output_tokens=int_or_none(usage.get("output_tokens")),
                total_tokens=int_or_none(usage.get("total_tokens")),
                metadata={
                    "cached_input_tokens": int_or_none(usage.get("cached_input_tokens")),
                    "cache_write_input_tokens": int_or_none(usage.get("cache_write_input_tokens")),
                    "reasoning_output_tokens": int_or_none(usage.get("reasoning_output_tokens")),
                },
            )
    return recording.persist(store)


def _session_id_from_path(path: Path) -> str:
    stem = path.stem
    if "-" not in stem:
        return stem
    return stem.rsplit("-", 1)[-1]


def _normalize_codex_model(model: str) -> str:
    if model.startswith("openai-org-") and ":" in model:
        return model.split(":", 1)[1]
    return model
