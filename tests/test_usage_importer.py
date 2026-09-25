from __future__ import annotations

import json

from nextrace import SQLiteStore
from nextrace.usage_importer import import_local_usage


def test_import_local_usage_groups_projects_and_tracks_state(tmp_path):
    codex_home = tmp_path / ".codex"
    claude_home = tmp_path / ".claude"
    pi_home = tmp_path / ".pi" / "agent"
    codex_project = tmp_path / "projects" / "codex-app"
    renamed_codex_project = tmp_path / "projects" / "renamed-codex-app"
    claude_project = tmp_path / "projects" / "claude-app"
    pi_project = tmp_path / "projects" / "pi-app"
    codex_file = codex_home / "sessions" / "2026" / "07" / "14" / "session.jsonl"
    claude_file = claude_home / "projects" / "-tmp-projects-claude-app" / "session.jsonl"
    pi_file = pi_home / "sessions" / "--tmp-projects-pi-app--" / "session.jsonl"
    state_path = tmp_path / "state.json"
    codex_file.parent.mkdir(parents=True)
    claude_file.parent.mkdir(parents=True)
    pi_file.parent.mkdir(parents=True)
    codex_file.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "type": "session_meta",
                    "timestamp": "2026-07-14T16:48:39.483Z",
                    "payload": {
                        "id": "codex-session",
                        "cwd": str(codex_project),
                        "model_provider": "shopify-proxy",
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-07-14T16:48:40.461Z",
                    "payload": {
                        "type": "task_started",
                        "turn_id": "turn-1",
                        "started_at": 1784047720,
                    },
                },
                {
                    "type": "turn_context",
                    "timestamp": "2026-07-14T16:48:40.498Z",
                    "payload": {
                        "turn_id": "turn-1",
                        "cwd": str(renamed_codex_project),
                        "model": "gpt-5.5",
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-07-14T16:48:55.612Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 100,
                                "output_tokens": 50,
                                "total_tokens": 1050,
                            }
                        },
                    },
                },
            ]
        ),
        encoding="utf-8",
    )
    claude_file.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-07-14T17:00:00.000Z",
                "sessionId": "claude-session",
                "uuid": "event-1",
                "cwd": str(claude_project),
                "message": {
                    "id": "msg-1",
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 1000, "output_tokens": 50},
                },
            }
        ),
        encoding="utf-8",
    )
    pi_file.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "type": "session",
                    "id": "pi-session",
                    "timestamp": "2026-07-14T18:00:00.000Z",
                    "cwd": str(pi_project),
                },
                {
                    "type": "message",
                    "id": "pi-event-1",
                    "timestamp": "2026-07-14T18:00:01.000Z",
                    "message": {
                        "role": "assistant",
                        "provider": "anthropic",
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input": 1000,
                            "output": 50,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                            "totalTokens": 1050,
                            "cost": {"total": 0.00375},
                        },
                    },
                },
            ]
        ),
        encoding="utf-8",
    )
    store = SQLiteStore(tmp_path / "traces.db")

    first = import_local_usage(
        store=store,
        codex_home=codex_home,
        claude_home=claude_home,
        pi_home=pi_home,
        state_path=state_path,
    )
    second = import_local_usage(
        store=store,
        codex_home=codex_home,
        claude_home=claude_home,
        pi_home=pi_home,
        state_path=state_path,
    )

    assert sorted(first.applications) == ["claude-app", "pi-app", "renamed-codex-app"]
    assert first.codex_files == 1
    assert first.codex_records == 1
    assert first.claude_files == 1
    assert first.claude_records == 1
    assert first.pi_files == 1
    assert first.pi_records == 1
    assert second.codex_files == 0
    assert second.claude_files == 0
    assert second.pi_files == 0
    assert len(store.list_traces()) == 3
    connections = {
        (connection["application"], connection["source"], connection["transport"])
        for connection in store.list_connections()
    }
    assert ("renamed-codex-app", "codex", "jsonl-auto-import") in connections
    assert ("claude-app", "claude-code", "jsonl-auto-import") in connections
    assert ("pi-app", "pi", "jsonl-auto-import") in connections
