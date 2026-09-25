from __future__ import annotations

import json

from nextrace import SQLiteStore
from nextrace.claude_usage import (
    claude_project_slug,
    find_claude_project_files,
    import_claude_usage,
)


def test_find_claude_project_files_uses_project_slug_and_latest(tmp_path):
    claude_home = tmp_path / ".claude"
    project_dir = claude_home / "projects" / claude_project_slug("/Users/me/project")
    project_dir.mkdir(parents=True)
    old_file = project_dir / "old.jsonl"
    new_file = project_dir / "new.jsonl"
    old_file.write_text("", encoding="utf-8")
    new_file.write_text("", encoding="utf-8")

    files = find_claude_project_files(
        claude_home=claude_home,
        project_path="/Users/me/project",
        latest=1,
    )

    assert files == [new_file]


def test_import_claude_usage_records_redacted_model_span(tmp_path):
    session_file = tmp_path / "session-1.jsonl"
    assistant_event = {
        "type": "assistant",
        "timestamp": "2026-07-14T17:00:00.000Z",
        "sessionId": "session-1",
        "uuid": "event-1",
        "cwd": "/repo",
        "requestId": "req-1",
        "message": {
            "id": "msg-1",
            "model": "claude-sonnet-4-6",
            "content": "raw secret response",
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 50,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 300,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 150,
                    "ephemeral_1h_input_tokens": 50,
                },
                "inference_geo": "global",
                "service_tier": "standard",
                "speed": "standard",
                "server_tool_use": {
                    "web_search_requests": 1,
                    "web_fetch_requests": 2,
                },
            },
        },
    }
    user_event = {"type": "user", "message": {"content": "raw secret prompt"}}
    session_file.write_text(
        "\n".join(json.dumps(event) for event in [user_event, assistant_event]),
        encoding="utf-8",
    )
    store = SQLiteStore(tmp_path / "traces.db")

    stats = import_claude_usage(
        store=store,
        application="se-assistant",
        files=[session_file],
    )

    assert stats.imported == 1
    assert stats.files == 1
    traces = store.list_traces()
    assert len(traces) == 1
    detail = store.get_trace(traces[0]["id"])
    assert detail is not None
    span = detail["spans"][0]
    assert span["kind"] == "model"
    assert span["provider"] == "anthropic"
    assert span["model"] == "claude-sonnet-4-6"
    assert span["input_tokens"] == 1500
    assert span["output_tokens"] == 50
    assert span["total_tokens"] == 1550
    assert span["cost_usd"] == 0.0047025
    assert span["prompt"] == {"redacted": True, "source": "claude-code"}
    assert span["response"] == {"redacted": True, "source": "claude-code"}
    assert span["metadata"]["cache_creation_input_tokens"] == 200
    assert span["metadata"]["cache_read_input_tokens"] == 300
    assert span["metadata"]["web_search_requests"] == 1

    rendered = json.dumps(detail)
    assert "raw secret" not in rendered

    stats = import_claude_usage(store=store, application="se-assistant", files=[session_file])
    assert stats.imported == 1
    assert len(store.list_traces()) == 1
