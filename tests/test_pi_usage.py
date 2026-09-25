from __future__ import annotations

import json

from nextrace import SQLiteStore
from nextrace.pi_usage import find_pi_session_files, import_pi_usage, pi_project_slug


def test_find_pi_session_files_uses_project_slug_and_latest(tmp_path):
    pi_home = tmp_path / ".pi" / "agent"
    project_dir = pi_home / "sessions" / pi_project_slug("/Users/me/project")
    project_dir.mkdir(parents=True)
    old_file = project_dir / "old.jsonl"
    new_file = project_dir / "new.jsonl"
    old_file.write_text("", encoding="utf-8")
    new_file.write_text("", encoding="utf-8")

    files = find_pi_session_files(
        pi_home=pi_home,
        project_path="/Users/me/project",
        latest=1,
    )

    assert files == [new_file]


def test_import_pi_usage_records_redacted_model_span(tmp_path):
    session_file = tmp_path / "session-1.jsonl"
    events = [
        {
            "type": "session",
            "id": "pi-session-1",
            "timestamp": "2026-07-14T17:00:00.000Z",
            "cwd": "/repo",
            "version": 3,
        },
        {
            "type": "message",
            "id": "pi-message-1",
            "timestamp": "2026-07-14T17:00:01.000Z",
            "message": {"role": "user", "content": "raw secret prompt"},
        },
        {
            "type": "message",
            "id": "pi-message-2",
            "timestamp": "2026-07-14T17:00:02.000Z",
            "message": {
                "role": "assistant",
                "api": "custom-messages",
                "provider": "custom-provider",
                "model": "custom-model",
                "content": "raw secret response",
                "responseId": "response-1",
                "stopReason": "stop",
                "usage": {
                    "input": 100,
                    "output": 20,
                    "cacheRead": 500,
                    "cacheWrite": 50,
                    "totalTokens": 670,
                    "cost": {"total": 0.0123},
                },
            },
        },
    ]
    session_file.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    store = SQLiteStore(tmp_path / "traces.db")

    stats = import_pi_usage(store=store, application="pi-app", files=[session_file])

    assert stats.imported == 1
    assert stats.files == 1
    traces = store.list_traces()
    assert len(traces) == 1
    detail = store.get_trace(traces[0]["id"])
    assert detail is not None
    span = detail["spans"][0]
    assert span["kind"] == "model"
    assert span["provider"] == "custom-provider"
    assert span["model"] == "custom-model"
    assert span["input_tokens"] == 650
    assert span["output_tokens"] == 20
    assert span["total_tokens"] == 670
    assert span["cost_usd"] == 0.0123
    assert span["prompt"] == {"redacted": True, "source": "pi"}
    assert span["response"] == {"redacted": True, "source": "pi"}
    assert span["metadata"]["cache_read_input_tokens"] == 500
    assert span["metadata"]["cache_write_input_tokens"] == 50
    assert span["metadata"]["cost_source"] == "pi-log"

    rendered = json.dumps(detail)
    assert "raw secret" not in rendered

    stats = import_pi_usage(store=store, application="pi-app", files=[session_file])
    assert stats.imported == 1
    assert len(store.list_traces()) == 1
