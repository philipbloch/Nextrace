import sqlite3
from dataclasses import replace

import pytest

from nextrace import SQLiteStore, ai_trace


def test_reimport_preserves_annotations_and_rolls_back_failed_span(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("app", store=store) as trace:
        span = trace.model_call(provider="test", model="test", prompt=None, response=None)
        trace.score("quality", 1, span_id=span.span_id)
        trace.feedback(rating=5)
        trace.event("reviewed")
    original = store.get_trace(trace.trace_id)

    store.record_import(trace.to_record(), replace(span.to_record(), duration_ms=150))
    updated = store.get_trace(trace.trace_id)
    assert updated["spans"][0]["duration_ms"] == 150
    for field in ("scores", "feedback", "events"):
        assert updated[field] == original[field]

    with pytest.raises(sqlite3.IntegrityError):
        store.record_import(
            replace(trace.to_record(), application="incorrect"),
            replace(span.to_record(), trace_id="missing"),
        )
    assert store.get_trace(trace.trace_id) == updated


@pytest.mark.parametrize("fail", [False, True])
def test_connections_close_after_success_or_failure(tmp_path, fail):
    store = SQLiteStore(tmp_path / "traces.db")
    try:
        with store._connect() as connection:
            connection.execute("SELECT 1")
            if fail:
                raise RuntimeError("failed operation")
    except RuntimeError:
        pass
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_application_list_includes_traces_and_connections(tmp_path):
    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("trace-only", store=store):
        pass
    store.record_connection(application="connection-only", source="test", transport="http")
    store.record_connection(application="trace-only", source="test", transport="http")
    assert store.list_applications() == ["connection-only", "trace-only"]


def test_dashboard_snapshot_does_not_block_recording_commits(tmp_path):
    import threading

    store = SQLiteStore(tmp_path / "traces.db")
    with ai_trace("first", store=store):
        pass
    errors, finished = [], threading.Event()

    def record():
        try:
            with ai_trace("second", store=store):
                pass
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    with store._connect() as reader:
        reader.execute("BEGIN")
        assert reader.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 1
        worker = threading.Thread(target=record)
        worker.start()
        try:
            assert finished.wait(3), "A reader held up the recorder's commit"
            assert not errors
            assert reader.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 1
        finally:
            reader.rollback()
            worker.join(5)
    assert len(store.list_traces()) == 2
