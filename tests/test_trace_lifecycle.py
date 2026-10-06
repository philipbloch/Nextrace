import asyncio
import json
import subprocess
import sys

import pytest

from nextrace import SQLiteStore, ai_trace


def test_running_trace_becomes_ok_and_is_excluded_from_failure_rate(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("app", store=store) as trace:
        assert trace.status == "running"
        assert store.get_trace(trace.trace_id)["status"] == "running"
        assert store.list_traces(status="ok") == []
        summary = store.summary()
        assert summary["totals"]["running"] == 1
        assert summary["failure_rates"][0]["total"] == 0
    assert trace.status == "ok"
    assert store.list_traces(status="running") == []
    assert store.summary()["failure_rates"][0]["total"] == 1


@pytest.mark.parametrize("failure", [asyncio.CancelledError, InterruptedError, KeyboardInterrupt])
def test_cancelled_trace_and_span_are_interrupted(tmp_path, failure):
    store = SQLiteStore(tmp_path / "traces.db")
    with pytest.raises(failure), ai_trace("app", store=store) as trace:
        with trace.step("tool", "call"):
            raise failure("stopped")
    detail = store.get_trace(trace.trace_id)
    assert detail["status"] == detail["spans"][0]["status"] == "interrupted"
    assert detail["ended_at"] is not None
    assert store.list_traces(status="error") == []
    assert store.summary()["totals"]["interrupted"] == 1


def test_explicit_interruption_is_not_overwritten_on_context_exit(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("app", store=store) as trace:
        trace.interrupt("user stopped recording")
        ended_at = trace.ended_at
    assert store.get_trace(trace.trace_id)["status"] == "interrupted"
    assert trace.ended_at == ended_at


def test_killed_owner_is_recovered_without_interrupting_another_live_writer(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    program = """
import sys
from nextrace import SQLiteStore, ai_trace
with ai_trace('app', store=SQLiteStore(sys.argv[1])) as trace:
    print(trace.trace_id, flush=True)
    sys.stdin.read(1)
"""
    children = []
    try:
        for _ in range(2):
            child = subprocess.Popen(
                [sys.executable, "-u", "-c", program, str(store.path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            children.append(child)
        ids = [child.stdout.readline().strip() for child in children]
        assert all(len(trace_id) == 32 for trace_id in ids)
        assert len(store.list_traces(status="running")) == 2
        children[0].kill()
        children[0].wait(timeout=5)
        stopped = store.get_trace(ids[0])
        assert stopped["status"] == "interrupted"
        assert stopped["ended_at"] is None
        assert "interruption_detected_at" in stopped["metadata"]
        assert store.get_trace(ids[1])["status"] == "running"
        children[1].communicate("q", timeout=5)
        assert store.get_trace(ids[1])["status"] == "ok"
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


def test_legacy_unfinished_trace_is_running_until_its_owner_can_be_verified(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("app", store=store) as trace:
        with store._connect() as connection:
            connection.execute(
                "UPDATE traces SET status='ok',metadata=? WHERE id=?",
                (json.dumps({}), trace.trace_id),
            )
        reader = SQLiteStore(store.path)
        assert reader.get_trace(trace.trace_id)["status"] == "running"
        assert reader.recover_interrupted_traces() == 0
