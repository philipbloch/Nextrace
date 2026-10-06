from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

from nextrace.ownership import RecordingOwner, owner_alive
from nextrace.types import JsonDict, SpanRecord, TraceRecord


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_json(value: str | None) -> Any:
    if value is None or value == "":
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


class SQLiteStore:
    """Connections stay local to each transaction so workers never share SQLite handles."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._owner_directory = self.path.parent / ".nextrace-owners" / self.path.name
        self._owner: RecordingOwner | None = None
        self._owner_lock = threading.Lock()
        self._init_schema()

    def start_trace(self, trace: TraceRecord) -> None:
        if trace.ended_at is None:
            with self._owner_lock:
                if self._owner is None or self._owner.pid != os.getpid():
                    self._owner = RecordingOwner(self._owner_directory)
                owner_id = self._owner.id
            trace = replace(
                trace,
                status="running",
                metadata={**trace.metadata, "_nextrace_owner": owner_id},
            )
        with self._connect() as conn:
            self._upsert(conn, "traces", trace, ("tags", "metadata"))

    def recover_interrupted_traces(self) -> int:
        with self._connect() as conn:
            owners = conn.execute(
                "SELECT DISTINCT json_extract(metadata, '$._nextrace_owner') AS owner "
                "FROM traces WHERE status = 'running'"
            ).fetchall()
            interrupted = 0
            for row in owners:
                owner_id = row["owner"]
                if owner_alive(self._owner_directory, owner_id) is not False:
                    continue
                result = conn.execute(
                    "UPDATE traces SET status = 'interrupted', error = ?, "
                    "metadata = json_set(metadata, '$.interruption_detected_at', ?) "
                    "WHERE status = 'running' AND json_extract(metadata, '$._nextrace_owner') = ?",
                    ("Recording owner exited before completing this trace", time.time(), owner_id),
                )
                interrupted += result.rowcount
            return interrupted

    def finish_trace(self, trace: TraceRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE traces
                SET ended_at = ?, duration_ms = ?, status = ?, error = ?, metadata = ?
                WHERE id = ?
                """,
                (
                    trace.ended_at,
                    trace.duration_ms,
                    trace.status,
                    trace.error,
                    _json(trace.metadata),
                    trace.id,
                ),
            )

    def record_span(self, span: SpanRecord) -> None:
        with self._connect() as conn:
            self._upsert(conn, "spans", span, ("prompt", "response", "metadata"))

    def record_import(self, trace: TraceRecord, span: SpanRecord) -> None:
        with self._connect() as conn:
            self._upsert(conn, "traces", trace, ("tags", "metadata"))
            self._upsert(conn, "spans", span, ("prompt", "response", "metadata"))

    def record_agent_snapshot(
        self,
        traces: list[TraceRecord],
        spans: list[SpanRecord],
    ) -> None:
        with self._connect() as conn:
            # Keep annotations attached when upgrading the former one-model-call-per-trace imports.
            moves = {}
            for span in spans:
                old = conn.execute("SELECT trace_id FROM spans WHERE id = ?", (span.id,)).fetchone()
                if old and old["trace_id"] != span.trace_id:
                    moves[old["trace_id"]] = span.trace_id
            canonical_traces = {span.id: span.trace_id for span in spans}
            duplicate_spans = self._legacy_span_ids(conn, traces, spans)
            for old_span, new_span in duplicate_spans.items():
                old = conn.execute(
                    "SELECT trace_id FROM spans WHERE id = ?", (old_span,)
                ).fetchone()
                moves[old["trace_id"]] = canonical_traces[new_span]
            for trace in traces:
                self._upsert(conn, "traces", trace, ("tags", "metadata"))
            for span in spans:
                self._upsert(conn, "spans", span, ("prompt", "response", "metadata"))
            self._remap_annotations(conn, duplicate_spans, moves)

    @staticmethod
    def _legacy_span_ids(
        conn: sqlite3.Connection, traces: list[TraceRecord], spans: list[SpanRecord]
    ) -> dict[str, str]:
        duplicate_spans = {}
        files_by_trace = {trace.id: trace.metadata.get("import_file") for trace in traces}
        for span in spans:
            if span.metadata.get("source") != "claude-code" or not span.metadata.get("message_id"):
                continue
            duplicates = conn.execute(
                "SELECT id, trace_id FROM spans WHERE kind = 'model' "
                "AND json_extract(metadata, '$.claude_session_file') = ? "
                "AND json_extract(metadata, '$.claude_message_id') = ?",
                (files_by_trace[span.trace_id], span.metadata["message_id"]),
            ).fetchall()
            for old in duplicates:
                if old["id"] != span.id:
                    duplicate_spans[old["id"]] = span.id
        # Match legacy event records by their observed identity, never their old line position.
        trace_by_id = {trace.id: trace for trace in traces}
        signatures = {}
        for span in spans:
            if span.kind != "model":
                continue
            trace = trace_by_id.get(span.trace_id)
            if trace is None:
                continue
            for timestamp in {span.started_at, *span.metadata.get("usage_snapshot_times", [])}:
                key = (
                    span.metadata.get("source") or trace.metadata.get("source"),
                    trace.session_id,
                    timestamp,
                    span.provider,
                    span.model,
                    span.input_tokens,
                    span.output_tokens,
                )
                signatures.setdefault(key, set()).add(span.id)
        sessions = {key[1] for key in signatures}
        for session in sessions:
            times = [key[2] for key in signatures if key[1] == session]
            old_rows = conn.execute(
                "SELECT s.*, t.metadata AS trace_metadata FROM spans s JOIN traces t ON t.id = s.trace_id "
                "WHERE t.session_id = ? AND s.kind = 'model' AND s.ended_at >= ? AND s.ended_at <= ?",
                (session, min(times), max(times)),
            ).fetchall()
            for old in old_rows:
                span_meta = _load_json(old["metadata"]) or {}
                trace_meta = _load_json(old["trace_metadata"]) or {}
                if not (
                    span_meta.get("source") in {"codex", "claude-code", "pi"}
                    or trace_meta.get("source") in {"codex", "claude-code", "pi"}
                ):
                    continue
                key = (
                    span_meta.get("source") or trace_meta.get("source"),
                    session,
                    old["ended_at"],
                    old["provider"],
                    old["model"],
                    old["input_tokens"],
                    old["output_tokens"],
                )
                candidates = signatures.get(key, set())
                if len(candidates) == 1:
                    canonical = next(iter(candidates))
                    if old["id"] != canonical:
                        duplicate_spans[old["id"]] = canonical
        return duplicate_spans

    @staticmethod
    def _remap_annotations(
        conn: sqlite3.Connection, span_ids: dict[str, str], trace_ids: dict[str, str]
    ) -> None:
        span_pairs = [(new, old) for old, new in span_ids.items()]
        trace_pairs = [(new, old) for old, new in trace_ids.items()]
        for table in ("scores", "events"):
            conn.executemany(f"UPDATE {table} SET span_id = ? WHERE span_id = ?", span_pairs)
        conn.executemany("DELETE FROM spans WHERE id = ?", [(old,) for old in span_ids])
        for table in ("scores", "feedback", "events"):
            conn.executemany(f"UPDATE {table} SET trace_id = ? WHERE trace_id = ?", trace_pairs)
        conn.executemany(
            "DELETE FROM traces WHERE id = ? AND NOT EXISTS (SELECT 1 FROM spans WHERE trace_id = ?)",
            [(old, old) for old in trace_ids],
        )

    def consolidate_legacy_models(self) -> int:
        """Legacy imports stored the event timestamp as ended_at, not started_at."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            matches = conn.execute("""
                SELECT old.id AS old_span, old.trace_id AS old_trace,
                       MIN(new.id) AS new_span, MIN(new.trace_id) AS new_trace
                FROM spans old JOIN traces ot ON ot.id = old.trace_id
                JOIN spans new ON new.started_at = old.ended_at AND new.kind = 'model'
                    AND new.input_tokens IS old.input_tokens AND new.output_tokens IS old.output_tokens
                    AND new.provider IS old.provider AND new.model IS old.model
                JOIN traces nt ON nt.id = new.trace_id AND nt.session_id = ot.session_id
                    AND json_extract(nt.metadata, '$.source') = json_extract(ot.metadata, '$.source')
                WHERE old.kind = 'model' AND old.id != new.id
                    AND json_extract(ot.metadata, '$.source') IN ('codex', 'claude-code', 'pi')
                    AND json_extract(ot.metadata, '$.import_file') IS NULL
                    AND json_extract(nt.metadata, '$.import_file') IS NOT NULL
                    AND (SELECT COUNT(*) FROM spans s WHERE s.trace_id = ot.id) = 1
                GROUP BY old.id HAVING COUNT(DISTINCT new.id) = 1
            """).fetchall()
            self._remap_annotations(
                conn,
                {row["old_span"]: row["new_span"] for row in matches},
                {row["old_trace"]: row["new_trace"] for row in matches},
            )
            return len(matches)

    def correlate_traces(self) -> None:
        with self._connect() as conn:
            # Recompute: a later import may reveal an overlapping session and invalidate a guess.
            conn.execute("DELETE FROM trace_links")
            matches = conn.execute("""
                SELECT p.id AS child_id, a.id AS parent_id, a.ended_at AS anchor_end, 'explicit' AS method
                FROM traces p JOIN traces a ON p.application = a.application
                    AND p.session_id = a.session_id AND p.turn_id = a.turn_id AND p.id != a.id
                WHERE json_extract(p.metadata, '$.server') IS NOT NULL
                    AND json_extract(a.metadata, '$.import_file') IS NOT NULL
                    AND p.turn_id IS NOT NULL
                UNION ALL
                SELECT p.id AS child_id, a.id AS parent_id, a.ended_at AS anchor_end, 'project_interval' AS method
                FROM traces p JOIN traces a ON p.application = a.application AND p.id != a.id
                    AND json_extract(p.metadata, '$.cwd') = json_extract(a.metadata, '$.cwd')
                    AND p.started_at >= a.started_at
                WHERE json_extract(p.metadata, '$.server') IS NOT NULL AND p.turn_id IS NULL
                    AND p.ended_at IS NOT NULL AND json_extract(p.metadata, '$.cwd') IS NOT NULL
                    AND json_extract(a.metadata, '$.import_file') IS NOT NULL
                    AND a.turn_id IS NOT NULL AND json_extract(a.metadata, '$.turn_boundary_known') = 1
                    AND (p.ended_at <= a.ended_at OR (a.ended_at IS NULL
                        AND (json_extract(a.metadata, '$.next_turn_started_at') IS NULL
                            OR p.ended_at <= json_extract(a.metadata, '$.next_turn_started_at'))))
            """).fetchall()
            by_child = {}
            for row in matches:
                by_child.setdefault(row["child_id"], []).append(row)
            for child_id, candidates in by_child.items():
                if len(candidates) != 1:
                    continue
                match = candidates[0]
                if match["method"] != "explicit" and match["anchor_end"] is None:
                    continue
                conn.execute(
                    "INSERT INTO trace_links(child_id, parent_id, method) VALUES(?, ?, ?)",
                    (child_id, match["parent_id"], match["method"]),
                )

    @staticmethod
    def _upsert(
        conn: sqlite3.Connection,
        table: str,
        record: TraceRecord | SpanRecord,
        json_fields: tuple[str, ...],
    ) -> None:
        data = {field.name: getattr(record, field.name) for field in fields(record)}
        for name in json_fields:
            data[name] = _json(data[name])
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        updates = ", ".join(f"{name} = excluded.{name}" for name in data if name != "id")
        # REPLACE deletes the old trace and cascades to its spans, scores, and feedback.
        conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            tuple(data.values()),
        )

    def record_score(
        self,
        *,
        trace_id: str,
        name: str,
        value: float,
        span_id: str | None = None,
        comment: str | None = None,
        metadata: JsonDict | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scores (id, trace_id, span_id, name, value, comment, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    trace_id,
                    span_id,
                    name,
                    value,
                    comment,
                    time.time(),
                    _json(metadata or {}),
                ),
            )

    def record_feedback(
        self,
        *,
        trace_id: str,
        rating: int | None = None,
        comment: str | None = None,
        user_id: str | None = None,
        metadata: JsonDict | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO feedback (id, trace_id, user_id, rating, comment, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    trace_id,
                    user_id,
                    rating,
                    comment,
                    time.time(),
                    _json(metadata or {}),
                ),
            )

    def record_event(
        self,
        *,
        trace_id: str,
        span_id: str | None,
        kind: str,
        name: str,
        payload: JsonDict | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (id, trace_id, span_id, kind, name, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    trace_id,
                    span_id,
                    kind,
                    name,
                    time.time(),
                    _json(payload or {}),
                ),
            )

    def record_connection(
        self,
        *,
        application: str,
        source: str,
        transport: str,
        status: str = "connected",
        metadata: JsonDict | None = None,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO connections (
                    id, application, source, transport, status, first_seen_at, last_seen_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(application, source, transport) DO UPDATE SET
                    status = excluded.status,
                    last_seen_at = excluded.last_seen_at,
                    metadata = excluded.metadata
                """,
                (
                    uuid.uuid4().hex,
                    application,
                    source,
                    transport,
                    status,
                    now,
                    now,
                    _json(metadata or {}),
                ),
            )

    def list_applications(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT application FROM traces UNION SELECT application FROM connections "
                "ORDER BY application"
            ).fetchall()
        return [row["application"] for row in rows]

    def list_connections(self) -> list[JsonDict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM connections
                ORDER BY application ASC, source ASC, transport ASC
                """
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def list_traces(
        self,
        *,
        limit: int | None = 100,
        application: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[JsonDict]:
        self.recover_interrupted_traces()
        self.correlate_traces()
        sql = """
            SELECT
                t.*,
                COUNT(s.id) AS span_count,
                COALESCE(SUM(s.input_tokens), 0) AS input_tokens,
                COALESCE(SUM(s.output_tokens), 0) AS output_tokens
            FROM execution_traces t
            LEFT JOIN spans s ON s.trace_id = t.id OR s.trace_id IN
                (SELECT child_id FROM trace_links WHERE parent_id = t.id)
        """
        filters, params = self._trace_filter_parts(
            application=application,
            session_id=session_id,
            since=since,
            until=until,
            alias="t",
        )
        if status:
            filters.append("t.status = ?")
            params.append(status)
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " GROUP BY t.id ORDER BY t.started_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._decode_row(row) for row in rows]

    def get_trace(self, trace_id: str, *, correlate: bool = True) -> JsonDict | None:
        if correlate:
            self.recover_interrupted_traces()
            self.correlate_traces()
        with self._connect() as conn:
            trace_row = conn.execute(
                "SELECT * FROM execution_traces WHERE id = ?", (trace_id,)
            ).fetchone()
            if trace_row is None:
                trace_row = conn.execute(
                    "SELECT * FROM traces WHERE id = ?", (trace_id,)
                ).fetchone()
            if trace_row is None:
                return None
            children = conn.execute(
                "SELECT t.*, l.method AS correlation_method FROM trace_links l "
                "JOIN traces t ON t.id = l.child_id WHERE l.parent_id = ?",
                (trace_id,),
            ).fetchall()
            child_spans = conn.execute(
                "SELECT s.* FROM spans s JOIN trace_links l ON l.child_id = s.trace_id "
                "WHERE l.parent_id = ? ORDER BY s.started_at",
                (trace_id,),
            ).fetchall()
            spans = conn.execute(
                "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at ASC",
                (trace_id,),
            ).fetchall()
            scores = conn.execute(
                "SELECT * FROM scores WHERE trace_id = ? ORDER BY created_at ASC",
                (trace_id,),
            ).fetchall()
            feedback = conn.execute(
                "SELECT * FROM feedback WHERE trace_id = ? ORDER BY created_at ASC",
                (trace_id,),
            ).fetchall()
            events = conn.execute(
                "SELECT * FROM events WHERE trace_id = ? ORDER BY created_at ASC",
                (trace_id,),
            ).fetchall()
        trace = self._decode_row(trace_row)
        trace["spans"] = [self._decode_row(row) for row in spans]
        spans_by_recording = {}
        for row in child_spans:
            spans_by_recording.setdefault(row["trace_id"], []).append(self._decode_row(row))
        for child in children:
            child = self._decode_row(child)
            root_id = "observation-" + child["id"]
            enclosing = [
                span
                for span in trace["spans"]
                if span["kind"] == "tool"
                and span.get("metadata", {}).get("timing") == "log_interval"
                and child["ended_at"] is not None
                and span["started_at"] <= child["started_at"]
                and span["ended_at"] >= child["ended_at"]
            ]
            parent_id = enclosing[0]["id"] if len(enclosing) == 1 else None
            trace["spans"].append(
                {
                    "id": root_id,
                    "trace_id": trace_id,
                    "parent_id": parent_id,
                    "kind": "observation",
                    "name": child["name"],
                    "status": child["status"],
                    "started_at": child["started_at"],
                    "ended_at": child["ended_at"],
                    "duration_ms": child["duration_ms"],
                    "error": child["error"],
                    "metadata": {
                        "correlation_method": child["correlation_method"],
                        "timing": "measured" if child["ended_at"] is not None else "unknown",
                    },
                }
            )
            for span in spans_by_recording.get(child["id"], []):
                span["parent_id"] = span["parent_id"] or root_id
                trace["spans"].append(span)
        trace["spans"].sort(key=lambda span: (span["started_at"], span["id"]))
        trace["correlated_recordings"] = len(children)
        trace["scores"] = [self._decode_row(row) for row in scores]
        trace["feedback"] = [self._decode_row(row) for row in feedback]
        trace["events"] = [self._decode_row(row) for row in events]
        return trace

    def summary(
        self,
        *,
        application: str | None = None,
        session_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> JsonDict:
        self.recover_interrupted_traces()
        self.correlate_traces()
        trace_clauses, trace_params = self._trace_filter_parts(
            application=application,
            session_id=session_id,
            since=since,
            until=until,
        )
        joined_trace_clauses, joined_trace_params = self._trace_filter_parts(
            application=application,
            session_id=session_id,
            since=since,
            until=until,
            alias="e",
        )
        trace_filter = f"WHERE {' AND '.join(trace_clauses)}" if trace_clauses else ""
        joined_where = f"WHERE {' AND '.join(joined_trace_clauses)}" if joined_trace_clauses else ""
        suffix = " AND " + " AND ".join(joined_trace_clauses) if joined_trace_clauses else ""
        model_where = "WHERE s.kind = 'model'" + suffix
        tool_where = "WHERE s.kind = 'tool'" + suffix
        timed_where = (
            "WHERE COALESCE(json_extract(s.metadata, '$.timing'), 'measured') != 'unknown'" + suffix
        )
        span_owner_join = """
            JOIN traces t ON t.id = s.trace_id
            LEFT JOIN trace_links l ON l.child_id = t.id
            JOIN execution_traces e ON e.id = COALESCE(l.parent_id, t.id)
        """
        connection_filter = "WHERE application = ?" if application else ""
        connection_params: list[Any] = [application] if application else []

        with self._connect() as conn:
            totals = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS traces,
                    COUNT(DISTINCT application) AS applications,
                    COUNT(DISTINCT session_id) AS sessions,
                    COALESCE(SUM(status = 'running'), 0) AS running,
                    COALESCE(SUM(status = 'interrupted'), 0) AS interrupted,
                    COALESCE(SUM(duration_ms), 0) AS total_trace_duration_ms
                FROM execution_traces
                {trace_filter}
                """,
                trace_params,
            ).fetchone()
            span_total = conn.execute(
                f"SELECT COUNT(*) FROM spans s {span_owner_join} {joined_where}",
                joined_trace_params,
            ).fetchone()[0]
            failure_rates = conn.execute(
                f"""
                SELECT
                    application,
                    SUM(CASE WHEN status IN ('ok', 'error') THEN 1 ELSE 0 END) AS total,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS failures
                FROM execution_traces
                {trace_filter}
                GROUP BY application
                ORDER BY (
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) * 1.0 /
                    NULLIF(SUM(CASE WHEN status IN ('ok', 'error') THEN 1 ELSE 0 END), 0)
                ) DESC,
                    failures DESC,
                    total DESC
                """,
                trace_params,
            ).fetchall()
            slowest_steps = conn.execute(
                f"""
                SELECT s.kind, s.name, s.provider, s.model, AVG(s.duration_ms) AS avg_ms, MAX(s.duration_ms) AS max_ms, COUNT(*) AS count
                FROM spans s
                {span_owner_join}
                {timed_where}
                GROUP BY s.kind, s.name, s.provider, s.model
                ORDER BY avg_ms DESC, max_ms DESC, count DESC
                LIMIT 10
                """,
                joined_trace_params,
            ).fetchall()
            tool_accuracy = conn.execute(
                f"""
                WITH tool_rows AS (
                    SELECT
                        s.id,
                        s.name,
                        CASE json_extract(s.metadata, '$.success')
                            WHEN 1 THEN 1.0
                            WHEN 0 THEN 0.0
                            WHEN 'true' THEN 1.0
                            WHEN 'false' THEN 0.0
                            ELSE NULL
                        END AS success_value,
                        CAST(json_extract(s.metadata, '$.accuracy') AS REAL) AS metadata_accuracy,
                        AVG(
                            CASE
                                WHEN lower(sc.name) IN (
                                    'accuracy',
                                    'tool_accuracy',
                                    'tool accuracy',
                                    'quality',
                                    'quality_score',
                                    'quality score',
                                    'correctness'
                                )
                                THEN sc.value
                                ELSE NULL
                            END
                        ) AS score_accuracy
                    FROM spans s
                    {span_owner_join}
                    LEFT JOIN scores sc ON sc.span_id = s.id
                    {tool_where}
                    GROUP BY s.id
                ),
                scored_tool_rows AS (
                    SELECT
                        name,
                        success_value,
                        COALESCE(metadata_accuracy, score_accuracy) AS accuracy
                    FROM tool_rows
                )
                SELECT
                    name,
                    COUNT(*) AS calls,
                    AVG(success_value) AS success_rate,
                    AVG(accuracy) AS accuracy,
                    SUM(CASE WHEN accuracy IS NOT NULL THEN 1 ELSE 0 END) AS scored_calls
                FROM scored_tool_rows
                GROUP BY name
                ORDER BY accuracy IS NULL,
                    accuracy DESC,
                    success_rate IS NULL,
                    success_rate DESC,
                    calls DESC
                LIMIT 20
                """,
                joined_trace_params,
            ).fetchall()
            prompt_model_comparisons = conn.execute(
                f"""
                SELECT
                    s.provider,
                    s.model,
                    COUNT(*) AS calls,
                    AVG(CASE WHEN json_extract(s.metadata, '$.timing') = 'unknown' THEN NULL ELSE s.duration_ms END) AS avg_latency_ms,
                    AVG(s.total_tokens) AS avg_tokens
                FROM spans s
                {span_owner_join}
                {model_where}
                GROUP BY s.provider, s.model
                ORDER BY calls DESC
                LIMIT 20
                """,
                joined_trace_params,
            ).fetchall()
            score_by_model = conn.execute(
                f"""
                SELECT s.provider, s.model, sc.name, AVG(sc.value) AS avg_score, COUNT(*) AS count
                FROM scores sc
                JOIN spans s ON s.id = sc.span_id
                {span_owner_join}
                {model_where}
                GROUP BY s.provider, s.model, sc.name
                ORDER BY count DESC
                LIMIT 20
                """,
                joined_trace_params,
            ).fetchall()
            connections = conn.execute(
                f"""
                SELECT * FROM connections
                {connection_filter}
                ORDER BY application ASC, source ASC, transport ASC
                """,
                connection_params,
            ).fetchall()
        total_trace_duration_ms = totals["total_trace_duration_ms"] or 0
        return {
            "totals": {
                "traces": totals["traces"] or 0,
                "applications": totals["applications"] or 0,
                "sessions": totals["sessions"] or 0,
                "spans": span_total,
                "running": totals["running"],
                "interrupted": totals["interrupted"],
                "total_trace_duration_ms": total_trace_duration_ms,
            },
            "failure_rates": [
                {
                    "application": row["application"],
                    "total": row["total"],
                    "failures": row["failures"] or 0,
                    "failure_rate": ((row["failures"] or 0) / row["total"]) if row["total"] else 0,
                }
                for row in failure_rates
            ],
            "slowest_steps": [dict(row) for row in slowest_steps],
            "tool_call_accuracy": [dict(row) for row in tool_accuracy],
            "prompt_model_comparisons": [dict(row) for row in prompt_model_comparisons],
            "score_by_model": [dict(row) for row in score_by_model],
            "connections": [self._decode_row(row) for row in connections],
        }

    @staticmethod
    def _trace_filter_parts(
        *,
        application: str | None = None,
        session_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        alias: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        prefix = f"{alias}." if alias else ""
        clauses: list[str] = []
        params: list[Any] = []
        if application:
            clauses.append(f"{prefix}application = ?")
            params.append(application)
        if session_id:
            clauses.append(f"{prefix}session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append(f"{prefix}started_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append(f"{prefix}started_at < ?")
            params.append(until)
        return clauses, params

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # sqlite3's transaction context commits/rolls back but does not close the connection.
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT,
                    application TEXT NOT NULL,
                    name TEXT NOT NULL,
                    user_id TEXT,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    duration_ms REAL NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    tags TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS spans (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
                    parent_id TEXT,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    started_at REAL NOT NULL,
                    ended_at REAL NOT NULL,
                    duration_ms REAL NOT NULL,
                    prompt TEXT,
                    response TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error TEXT,
                    metadata TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scores (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
                    span_id TEXT,
                    name TEXT NOT NULL,
                    value REAL NOT NULL,
                    comment TEXT,
                    created_at REAL NOT NULL,
                    metadata TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
                    user_id TEXT,
                    rating INTEGER,
                    comment TEXT,
                    created_at REAL NOT NULL,
                    metadata TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
                    span_id TEXT,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS connections (
                    id TEXT PRIMARY KEY,
                    application TEXT NOT NULL,
                    source TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    status TEXT NOT NULL,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    metadata TEXT NOT NULL,
                    UNIQUE(application, source, transport)
                );

                CREATE TABLE IF NOT EXISTS trace_links (
                    child_id TEXT PRIMARY KEY REFERENCES traces(id) ON DELETE CASCADE,
                    parent_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
                    method TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trace_links_parent ON trace_links(parent_id);
                CREATE INDEX IF NOT EXISTS idx_traces_application ON traces(application);
                CREATE INDEX IF NOT EXISTS idx_traces_started_at ON traces(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status);
                CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);
                CREATE INDEX IF NOT EXISTS idx_spans_event_time ON spans(started_at);
                CREATE INDEX IF NOT EXISTS idx_spans_event_end ON spans(ended_at);
                CREATE INDEX IF NOT EXISTS idx_spans_kind ON spans(kind);
                CREATE INDEX IF NOT EXISTS idx_scores_trace_id ON scores(trace_id);
                CREATE INDEX IF NOT EXISTS idx_connections_application ON connections(application);
                """
            )

            columns = {row[1] for row in conn.execute("PRAGMA table_info(traces)")}
            if "turn_id" not in columns:
                conn.execute("ALTER TABLE traces ADD COLUMN turn_id TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_traces_session_turn ON traces(session_id, turn_id)"
            )

            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_imported_trace_window ON traces(
                    application, json_extract(metadata, '$.cwd'), started_at, ended_at
                ) WHERE json_extract(metadata, '$.import_file') IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_proxy_trace_window ON traces(application, started_at, ended_at)
                    WHERE json_extract(metadata, '$.server') IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_legacy_claude_message ON spans(
                    json_extract(metadata, '$.claude_session_file'), json_extract(metadata, '$.claude_message_id')
                ) WHERE kind = 'model';
                CREATE INDEX IF NOT EXISTS idx_feedback_trace_id ON feedback(trace_id);
                CREATE INDEX IF NOT EXISTS idx_events_trace_id ON events(trace_id);
            """)

            columns = ", ".join(
                f"t.{field.name}" for field in fields(TraceRecord) if field.name != "status"
            )
            conn.execute(f"""
                CREATE VIEW IF NOT EXISTS execution_traces AS SELECT {columns},
                    CASE
                        WHEN t.status = 'running' OR EXISTS (
                            SELECT 1 FROM trace_links l JOIN traces c ON c.id = l.child_id
                            WHERE l.parent_id = t.id AND c.status = 'running') THEN 'running'
                        WHEN t.status = 'error' OR EXISTS (
                            SELECT 1 FROM trace_links l JOIN traces c ON c.id = l.child_id
                            WHERE l.parent_id = t.id AND c.status = 'error') THEN 'error'
                        WHEN t.status = 'interrupted' OR EXISTS (
                            SELECT 1 FROM trace_links l JOIN traces c ON c.id = l.child_id
                            WHERE l.parent_id = t.id AND c.status = 'interrupted') THEN 'interrupted'
                        ELSE t.status
                    END AS status
                FROM traces t WHERE t.id NOT IN (SELECT child_id FROM trace_links)
            """)

            # Older writers labelled unfinished runs OK; their owners are unknown.
            conn.execute(
                "UPDATE traces SET status = 'running' WHERE status = 'ok' AND ended_at IS NULL"
            )
            conn.execute(
                "UPDATE traces SET status = 'interrupted' WHERE status = 'error' "
                "AND json_extract(metadata, '$.interrupted_by') IS NOT NULL"
            )

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> JsonDict:
        data = dict(row)
        data.pop("cost_usd", None)
        for name in ("tags", "metadata", "prompt", "response", "payload"):
            if name in data:
                data[name] = _load_json(data[name])
        if "tags" in data:
            data["tags"] = data["tags"] or []
        for name in ("metadata", "payload"):
            if name in data:
                data[name] = {
                    key: value
                    for key, value in (data[name] or {}).items()
                    if not key.startswith(("cost_", "pricing_", "pi_recorded_cost"))
                }
        return data
