import json
from dataclasses import replace
from datetime import datetime

import pytest

from nextrace import SQLiteStore, ai_trace
from nextrace.agent_events import AgentRecording
from nextrace.claude_usage import import_claude_usage
from nextrace.codex_usage import import_codex_usage
from nextrace.mcp_proxy import MCPTraceRecorder
from nextrace.pi_usage import import_pi_usage
from nextrace.usage import stable_id


def write_log(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events))
    return path


def codex_event(time, payload, kind="event_msg"):
    return {"type": kind, "timestamp": f"2026-10-06T12:00:{time:02d}Z", "payload": payload}


def test_codex_turns_parallel_tools_partial_append_and_reimport(tmp_path):
    events = [
        codex_event(0, {"id": "session", "cwd": "/repo"}, "session_meta"),
        codex_event(0, {"type": "task_started", "turn_id": "one"}),
        codex_event(1, {"model": "test", "turn_id": "one"}, "turn_context"),
        codex_event(
            2,
            {"type": "function_call", "call_id": "a", "name": "search", "arguments": "SECRET"},
            "response_item",
        ),
        codex_event(3, {"type": "function_call", "call_id": "b", "name": "read"}, "response_item"),
        codex_event(
            4,
            {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": 100, "output_tokens": 10},
                    "total_token_usage": {"total_tokens": 110},
                },
            },
        ),
        codex_event(
            4,
            {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": 100, "output_tokens": 10},
                    "total_token_usage": {"total_tokens": 110},
                },
            },
        ),
        codex_event(
            5, {"type": "function_call_output", "call_id": "b", "output": "SECRET"}, "response_item"
        ),
    ]
    path = write_log(tmp_path / "session.jsonl", events)
    store = SQLiteStore(tmp_path / "traces.db")
    assert import_codex_usage(store=store, application="app", files=[path]).imported == 1
    first_id = store.list_traces()[0]["id"]
    first = store.get_trace(first_id)
    assert first["session_id"] == "session" and first["turn_id"] == "one"
    assert first["status"] == "running" and first["ended_at"] is None
    tools = {s["name"]: s for s in first["spans"] if s["kind"] == "tool"}
    assert tools["search"]["status"] == "running"
    assert tools["read"]["duration_ms"] == 2000
    store.record_feedback(trace_id=first_id, rating=5)
    events += [
        codex_event(6, {"type": "function_call_output", "call_id": "a"}, "response_item"),
        codex_event(7, {"type": "task_complete"}),
        codex_event(8, {"type": "task_started", "turn_id": "two"}),
        codex_event(
            9,
            {
                "type": "token_count",
                "info": {"last_token_usage": {"input_tokens": 50, "output_tokens": 5}},
            },
        ),
        codex_event(10, {"type": "turn_aborted"}),
    ]
    write_log(path, events)
    for _ in range(2):
        assert import_codex_usage(store=store, application="app", files=[path]).imported == 2
        assert len(store.list_traces()) == 2
    detail = store.get_trace(first_id)
    assert detail["status"] == "ok" and detail["duration_ms"] == 7000
    assert detail["feedback"][0]["rating"] == 5
    assert len(detail["spans"]) == 3
    assert "SECRET" not in json.dumps(detail)
    assert store.list_traces(status="interrupted")[0]["turn_id"] == "two"
    assert store.summary()["prompt_model_comparisons"][0]["avg_latency_ms"] is None


@pytest.mark.parametrize("source", ["claude", "pi"])
def test_message_agents_keep_tool_results_in_same_turn(tmp_path, source):
    if source == "claude":
        events = [
            {
                "type": "user",
                "uuid": "human",
                "sessionId": "session",
                "timestamp": "2026-10-06T12:00:00Z",
                "message": {"content": "SECRET"},
            },
            {
                "type": "assistant",
                "uuid": "model-event",
                "parentUuid": "human",
                "sessionId": "session",
                "timestamp": "2026-10-06T12:00:01Z",
                "message": {
                    "id": "model",
                    "model": "test",
                    "usage": {"input_tokens": 10},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call",
                            "name": "search",
                            "input": {"secret": "SECRET"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "uuid": "result",
                "parentUuid": "model-event",
                "timestamp": "2026-10-06T12:00:03Z",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call",
                            "is_error": True,
                            "content": "SECRET",
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "parentUuid": "result",
                "timestamp": "2026-10-06T12:00:04Z",
                "message": {
                    "id": "final",
                    "model": "test",
                    "usage": {"input_tokens": 20},
                    "stop_reason": "end_turn",
                },
            },
        ]
        importer = import_claude_usage
    else:
        events = [
            {"type": "session", "id": "session"},
            {
                "type": "message",
                "id": "human",
                "timestamp": "2026-10-06T12:00:00Z",
                "message": {"role": "user", "content": "SECRET"},
            },
            {
                "type": "message",
                "id": "model",
                "parentId": "human",
                "timestamp": "2026-10-06T12:00:01Z",
                "message": {
                    "role": "assistant",
                    "provider": "test",
                    "model": "test",
                    "stopReason": "toolUse",
                    "usage": {"input": 10},
                    "content": [{"type": "toolCall", "id": "call", "name": "search"}],
                },
            },
            {
                "type": "message",
                "id": "result",
                "parentId": "model",
                "timestamp": "2026-10-06T12:00:03Z",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "call",
                    "isError": True,
                    "content": "SECRET",
                },
            },
            {
                "type": "message",
                "id": "final",
                "parentId": "result",
                "timestamp": "2026-10-06T12:00:04Z",
                "message": {
                    "role": "assistant",
                    "provider": "test",
                    "model": "test",
                    "usage": {"input": 20, "cost": {"total": 1000}},
                    "stopReason": "stop",
                },
            },
        ]
        importer = import_pi_usage
    path = write_log(tmp_path / "session.jsonl", events)
    store = SQLiteStore(tmp_path / "traces.db")
    for _ in range(2):
        assert importer(store=store, application="app", files=[path]).imported == 2
    assert len(store.list_traces()) == 1
    detail = store.get_trace(store.list_traces()[0]["id"])
    assert detail["turn_id"] == "human" and detail["status"] == "error"
    tools = [s for s in detail["spans"] if s["kind"] == "tool"]
    assert len(tools) == 1 and tools[0]["duration_ms"] == 2000
    assert tools[0]["parent_id"] in {s["id"] for s in detail["spans"] if s["kind"] == "model"}
    assert "SECRET" not in json.dumps(detail) and "cost" not in json.dumps(detail)


def make_turn(store, session="session", turn="turn", cwd="/repo", start=100, end=110):
    recording = AgentRecording("codex", session, "app", f"/{session}.jsonl")
    recording.cwd = cwd
    recording.begin(turn, start)
    recording.tool("call", "exec", start + 1)
    recording.tool_result("call", start + 8)
    if end is not None:
        recording.finish(end)
    recording.persist(store)
    return next(iter(recording.turns))


def make_proxy(store, session=None, turn=None, cwd="/repo", start=102, end=105, failed=False):
    with ai_trace(
        "app",
        session_id=session,
        turn_id=turn,
        store=store,
        metadata={"server": "gateway", "cwd": cwd},
    ) as trace:
        trace.tool_call(name="lookup", latency_ms=1, success=not failed)
        if failed:
            trace.status = "error"
    record = replace(
        trace.to_record(), started_at=start, ended_at=end, duration_ms=(end - start) * 1000
    )
    store.start_trace(record)
    return trace.trace_id


def test_proxy_correlates_unique_project_window_and_unlinks_ambiguity(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    anchor = make_turn(store)
    child = make_proxy(store)
    assert len(store.list_traces()) == 1
    detail = store.get_trace(anchor)
    observation = next(s for s in detail["spans"] if s["kind"] == "observation")
    assert observation["metadata"]["correlation_method"] == "project_interval"
    assert observation["parent_id"] == stable_id("codex", "session", "tool", "call")
    assert store.summary()["totals"]["traces"] == 1
    # A newly imported concurrent session invalidates the earlier inference.
    make_turn(store, session="other")
    assert len(store.list_traces()) == 3
    assert store.get_trace(anchor)["correlated_recordings"] == 0
    assert store.get_trace(child)["status"] == "ok"


def test_explicit_context_wins_with_overlapping_sessions_and_propagates_errors(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    anchor = make_turn(store)
    make_turn(store, session="other")
    make_proxy(store, session="session", turn="turn", failed=True)
    assert len(store.list_traces()) == 2
    assert store.list_traces(status="error")[0]["id"] == anchor
    assert store.get_trace(anchor)["status"] == "error"
    assert store.summary()["failure_rates"][0]["failure_rate"] == 0.5


def test_open_or_wrong_project_intervals_are_not_inferred(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    make_turn(store, end=None)
    make_proxy(store)
    make_proxy(store, cwd="/other")
    assert len(store.list_traces()) == 3


def test_http_proxy_captures_turn_context_without_mcp_session_confusion(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    recorder = MCPTraceRecorder(application="app", server="gateway", transport="http", store=store)
    pending = recorder.record_http_call(
        request_body=json.dumps(
            {"method": "tools/call", "id": 1, "params": {"name": "lookup"}}
        ).encode(),
        request_headers={
            "X-Nextrace-Session-Id": "agent",
            "X-Nextrace-Turn-Id": "turn",
            "Mcp-Session-Id": "TRANSPORT-SECRET",
            "Authorization": "AUTH-SECRET",
        },
        target_url="https://example.com/mcp",
    )
    recorder.finish_http_call(pending, status_code=200, response_bytes=10)
    detail = store.get_trace(pending.trace.trace_id)
    assert detail["session_id"] == "agent" and detail["turn_id"] == "turn"
    assert "TRANSPORT-SECRET" not in json.dumps(detail) and "AUTH-SECRET" not in json.dumps(detail)


def test_stdio_proxy_captures_meta_context(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    recorder = MCPTraceRecorder(application="app", server="gateway", transport="stdio", store=store)
    recorder.observe_client_json(
        {
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "lookup",
                "_meta": {
                    "nextrace": {"session_id": "agent", "turn_id": "turn", "project_path": "/repo"}
                },
            },
        }
    )
    recorder.observe_server_json({"id": 1, "result": {}})
    detail = store.get_trace(store.list_traces()[0]["id"])
    assert detail["session_id"] == "agent" and detail["turn_id"] == "turn"
    assert detail["metadata"]["cwd"] == "/repo"


def test_snapshot_upgrade_preserves_annotations_and_is_atomic(tmp_path):
    import sqlite3

    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("app", store=store) as old:
        span = old.model_call(provider="test", model="test", prompt=None, response=None)
        old.score("quality", 1, span_id=span.span_id)
        old.feedback(rating=5)
    new_trace = replace(old.to_record(), id="new", turn_id="turn")
    new_span = replace(span.to_record(), trace_id="new")
    with pytest.raises(sqlite3.IntegrityError):
        store.record_agent_snapshot([new_trace], [replace(new_span, trace_id="missing")])
    assert store.get_trace("new") is None
    store.record_agent_snapshot([new_trace], [new_span])
    assert store.get_trace(old.trace_id) is None
    detail = store.get_trace("new")
    assert detail["feedback"][0]["rating"] == 5 and detail["scores"][0]["value"] == 1


def test_missing_turn_timestamp_is_not_used_for_proxy_inference(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    recording = AgentRecording("codex", "session", "app", "/session.jsonl")
    recording.cwd = "/repo"
    recording.begin("turn", None)
    recording.model(
        span_id="model",
        timestamp=100,
        provider="test",
        model="test",
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
        metadata={},
    )
    recording.finish(110)
    recording.persist(store)
    make_proxy(store)
    assert len(store.list_traces()) == 2


def test_session_filters_include_correlated_steps(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    make_turn(store)
    make_turn(store, session="other")
    make_proxy(store, session="session", turn="turn")
    traces = store.list_traces(session_id="session")
    assert len(traces) == 1 and traces[0]["span_count"] == 2
    summary = store.summary(session_id="session")
    assert summary["totals"]["traces"] == summary["totals"]["sessions"] == 1
    assert summary["totals"]["spans"] == 2


def test_upgrade_deduplicates_claude_chunks_without_losing_annotations(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    source_path = str(tmp_path / "session.jsonl")
    span_ids = []
    for _ in range(2):
        with ai_trace("app", store=store) as old:
            span = old.model_call(
                provider="test",
                model="test",
                prompt=None,
                response=None,
                metadata={"claude_session_file": source_path, "claude_message_id": "message"},
            )
            old.score("quality", 1, span_id=span.span_id)
            old.feedback(rating=5)
            span_ids.append(span.span_id)
    recording = AgentRecording("claude-code", "session", "app", source_path)
    recording.begin("turn", 100)
    canonical = recording.model(
        span_id=span_ids[0],
        timestamp=102,
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        metadata={"message_id": "message"},
    )
    recording.finish(105)
    recording.persist(store)
    assert len(store.list_traces()) == 1
    detail = store.get_trace(store.list_traces()[0]["id"])
    assert len(detail["spans"]) == 1 and len(detail["feedback"]) == len(detail["scores"]) == 2
    assert all(s["span_id"] == canonical for s in detail["scores"])


def test_upgrade_repeated_codex_snapshots_preserves_annotations(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    event = codex_event(
        2,
        {
            "type": "token_count",
            "info": {
                "last_token_usage": {"input_tokens": 10, "output_tokens": 2},
                "total_token_usage": {"total_tokens": 12},
            },
        },
    )
    path = write_log(
        tmp_path / "session.jsonl",
        [
            codex_event(0, {"id": "session"}, "session_meta"),
            codex_event(1, {"type": "task_started", "turn_id": "turn"}),
            event,
            event,
            codex_event(3, {"type": "task_complete"}),
        ],
    )
    for line in (3, 4):
        with ai_trace(
            "app",
            trace_id=stable_id("codex-trace", "session", str(line)),
            store=store,
            session_id="session",
            metadata={"source": "codex"},
        ) as old:
            span = old.model_call(
                provider="codex",
                model="unknown",
                prompt=None,
                response=None,
                input_tokens=10,
                output_tokens=2,
            )
            record = replace(
                span.to_record(),
                id=stable_id("codex-span", "session", str(line)),
                started_at=datetime.fromisoformat("2026-10-06T12:00:01+00:00").timestamp(),
                ended_at=datetime.fromisoformat("2026-10-06T12:00:02+00:00").timestamp(),
            )
            with store._connect() as conn:
                conn.execute("DELETE FROM spans WHERE id=?", (span.span_id,))
            store.record_span(record)
            old.score("quality", 1, span_id=record.id)
            old.feedback(rating=5)
    import_codex_usage(store=store, application="app", files=[path])
    assert len(store.list_traces()) == 1
    detail = store.get_trace(store.list_traces()[0]["id"])
    assert len(detail["spans"]) == 1
    assert len(detail["feedback"]) == len(detail["scores"]) == 2
    assert {s["span_id"] for s in detail["scores"]} == {detail["spans"][0]["id"]}


def test_log_rewrite_keeps_model_identity_and_annotations(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    events = [
        codex_event(0, {"id": "session"}, "session_meta"),
        codex_event(1, {"type": "task_started", "turn_id": "turn"}),
        codex_event(
            2,
            {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": 10, "output_tokens": 2},
                    "total_token_usage": {"total_tokens": 12},
                },
            },
        ),
        codex_event(3, {"type": "task_complete"}),
    ]
    path = write_log(tmp_path / "session.jsonl", events)
    import_codex_usage(store=store, application="app", files=[path])
    trace_id = store.list_traces()[0]["id"]
    span_id = store.get_trace(trace_id)["spans"][0]["id"]
    store.record_score(trace_id=trace_id, span_id=span_id, name="quality", value=1)
    # Codex rewrites/prunes logs; line positions change while event identity stays the same.
    events.insert(2, {"type": "ignored"})
    write_log(path, events)
    import_codex_usage(store=store, application="app", files=[path])
    assert len(store.list_traces()) == 1
    detail = store.get_trace(trace_id)
    assert len(detail["spans"]) == 1 and detail["spans"][0]["id"] == span_id
    assert detail["scores"][0]["span_id"] == span_id


def test_legacy_turn_start_timestamp_does_not_hide_event_identity(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("app", session_id="session", metadata={"source": "codex"}, store=store) as old:
        span = old.model_call(
            provider="test",
            model="test",
            prompt=None,
            response=None,
            input_tokens=10,
            output_tokens=5,
        )
        store.record_span(replace(span.to_record(), started_at=100, ended_at=103))
        old.feedback(rating=5)
        old.score("quality", 1, span_id=span.span_id)
    recording = AgentRecording("codex", "session", "app", "/session.jsonl")
    recording.begin("turn", 100)
    recording.model(
        span_id="canonical",
        timestamp=103,
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        metadata={},
    )
    recording.finish(105)
    store.record_import(next(iter(recording.turns.values())), recording.spans["canonical"])
    assert len(store.list_traces()) == 2
    assert store.consolidate_legacy_models() == 1
    assert len(store.list_traces()) == 1
    detail = store.get_trace(store.list_traces()[0]["id"])
    assert detail["scores"][0]["span_id"] == "canonical" and detail["feedback"][0]["rating"] == 5
