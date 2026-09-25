"""SQLite persistence for traces and dashboard metrics."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

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
    """SQLite-backed trace store.

    The store opens short-lived connections per operation. That keeps the class
    simple and safe to use from background workers or web apps.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def start_trace(self, trace: TraceRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO traces (
                    id, session_id, application, name, user_id, started_at, ended_at,
                    duration_ms, status, error, tags, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.id,
                    trace.session_id,
                    trace.application,
                    trace.name,
                    trace.user_id,
                    trace.started_at,
                    trace.ended_at,
                    trace.duration_ms,
                    trace.status,
                    trace.error,
                    _json(trace.tags),
                    _json(trace.metadata),
                ),
            )

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
            conn.execute(
                """
                INSERT OR REPLACE INTO spans (
                    id, trace_id, parent_id, kind, name, provider, model, started_at,
                    ended_at, duration_ms, prompt, response, input_tokens, output_tokens,
                    total_tokens, cost_usd, retry_count, status, error, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    span.id,
                    span.trace_id,
                    span.parent_id,
                    span.kind,
                    span.name,
                    span.provider,
                    span.model,
                    span.started_at,
                    span.ended_at,
                    span.duration_ms,
                    _json(span.prompt),
                    _json(span.response),
                    span.input_tokens,
                    span.output_tokens,
                    span.total_tokens,
                    span.cost_usd,
                    span.retry_count,
                    span.status,
                    span.error,
                    _json(span.metadata),
                ),
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
                (uuid.uuid4().hex, trace_id, span_id, name, value, comment, time.time(), _json(metadata or {})),
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
                (uuid.uuid4().hex, trace_id, user_id, rating, comment, time.time(), _json(metadata or {})),
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
                (uuid.uuid4().hex, trace_id, span_id, kind, name, time.time(), _json(payload or {})),
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

    def list_connections(self) -> list[JsonDict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM connections
                ORDER BY application ASC, source ASC, transport ASC
                """
            ).fetchall()
        return [self._connection_row(row) for row in rows]

    def list_traces(
        self,
        *,
        limit: int = 100,
        application: str | None = None,
        status: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[JsonDict]:
        sql = """
            SELECT
                t.*,
                COUNT(s.id) AS span_count,
                COALESCE(SUM(s.cost_usd), 0) AS cost_usd,
                COALESCE(SUM(s.input_tokens), 0) AS input_tokens,
                COALESCE(SUM(s.output_tokens), 0) AS output_tokens
            FROM traces t
            LEFT JOIN spans s ON s.trace_id = t.id
        """
        params: list[Any] = []
        filters: list[str] = []
        if application:
            filters.append("t.application = ?")
            params.append(application)
        if status:
            filters.append("t.status = ?")
            params.append(status)
        if since is not None:
            filters.append("t.started_at >= ?")
            params.append(since)
        if until is not None:
            filters.append("t.started_at < ?")
            params.append(until)
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " GROUP BY t.id ORDER BY t.started_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._trace_row(row) for row in rows]

    def get_trace(self, trace_id: str) -> JsonDict | None:
        with self._connect() as conn:
            trace_row = conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
            if trace_row is None:
                return None
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
        trace = self._trace_row(trace_row)
        trace["spans"] = [self._span_row(row) for row in spans]
        trace["scores"] = [self._score_row(row) for row in scores]
        trace["feedback"] = [self._feedback_row(row) for row in feedback]
        trace["events"] = [self._event_row(row) for row in events]
        return trace

    def summary(
        self,
        *,
        application: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> JsonDict:
        trace_clauses, trace_params = self._trace_filter_parts(
            application=application,
            since=since,
            until=until,
        )
        joined_trace_clauses, joined_trace_params = self._trace_filter_parts(
            application=application,
            since=since,
            until=until,
            alias="t",
        )
        trace_filter = f"WHERE {' AND '.join(trace_clauses)}" if trace_clauses else ""
        joined_where = f"WHERE {' AND '.join(joined_trace_clauses)}" if joined_trace_clauses else ""
        model_where = "WHERE s.kind = 'model'"
        model_params: list[Any] = []
        tool_where = "WHERE s.kind = 'tool'"
        tool_params: list[Any] = []
        score_where = "WHERE s.kind = 'model'"
        score_params: list[Any] = []
        connection_filter = "WHERE application = ?" if application else ""
        connection_params: list[Any] = [application] if application else []
        if joined_trace_clauses:
            model_where += " AND " + " AND ".join(joined_trace_clauses)
            model_params.extend(joined_trace_params)
            tool_where += " AND " + " AND ".join(joined_trace_clauses)
            tool_params.extend(joined_trace_params)
            score_where += " AND " + " AND ".join(joined_trace_clauses)
            score_params.extend(joined_trace_params)

        with self._connect() as conn:
            totals = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS traces,
                    COUNT(DISTINCT application) AS applications,
                    COALESCE(SUM(duration_ms), 0) AS total_trace_duration_ms
                FROM traces
                {trace_filter}
                """,
                trace_params,
            ).fetchone()
            cost_by_app = conn.execute(
                f"""
                SELECT t.application, COALESCE(SUM(s.cost_usd), 0) AS cost_usd
                FROM traces t
                LEFT JOIN spans s ON s.trace_id = t.id
                {joined_where}
                GROUP BY t.application
                ORDER BY cost_usd DESC
                """,
                joined_trace_params,
            ).fetchall()
            cost_totals = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(s.cost_usd), 0) AS estimated_cost_usd,
                    COALESCE(
                        SUM(CASE WHEN s.provider = 'shopify-proxy' THEN s.cost_usd ELSE 0 END),
                        0
                    ) AS shopify_proxy_cost_usd
                FROM traces t
                LEFT JOIN spans s ON s.trace_id = t.id
                {joined_where}
                """,
                joined_trace_params,
            ).fetchone()
            shopify_cost_by_app = conn.execute(
                f"""
                SELECT
                    t.application,
                    COALESCE(
                        SUM(CASE WHEN s.provider = 'shopify-proxy' THEN s.cost_usd ELSE 0 END),
                        0
                    ) AS cost_usd
                FROM traces t
                LEFT JOIN spans s ON s.trace_id = t.id
                {joined_where}
                GROUP BY t.application
                HAVING COALESCE(
                    SUM(CASE WHEN s.provider = 'shopify-proxy' THEN s.cost_usd ELSE 0 END),
                    0
                ) > 0
                ORDER BY cost_usd DESC
                """,
                joined_trace_params,
            ).fetchall()
            failure_rates = conn.execute(
                f"""
                SELECT
                    application,
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS failures
                FROM traces
                {trace_filter}
                GROUP BY application
                ORDER BY (SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) * 1.0 / COUNT(*)) DESC,
                    failures DESC,
                    total DESC
                """,
                trace_params,
            ).fetchall()
            slowest_steps = conn.execute(
                f"""
                SELECT s.kind, s.name, s.provider, s.model, AVG(s.duration_ms) AS avg_ms, MAX(s.duration_ms) AS max_ms, COUNT(*) AS count
                FROM spans s
                JOIN traces t ON t.id = s.trace_id
                {joined_where}
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
                    JOIN traces t ON t.id = s.trace_id
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
                tool_params,
            ).fetchall()
            prompt_model_comparisons = conn.execute(
                f"""
                SELECT
                    s.provider,
                    s.model,
                    COUNT(*) AS calls,
                    AVG(s.duration_ms) AS avg_latency_ms,
                    COALESCE(SUM(s.cost_usd), 0) AS cost_usd,
                    AVG(s.total_tokens) AS avg_tokens
                FROM spans s
                JOIN traces t ON t.id = s.trace_id
                {model_where}
                GROUP BY s.provider, s.model
                ORDER BY calls DESC
                LIMIT 20
                """,
                model_params,
            ).fetchall()
            score_by_model = conn.execute(
                f"""
                SELECT s.provider, s.model, sc.name, AVG(sc.value) AS avg_score, COUNT(*) AS count
                FROM scores sc
                JOIN spans s ON s.id = sc.span_id
                JOIN traces t ON t.id = s.trace_id
                {score_where}
                GROUP BY s.provider, s.model, sc.name
                ORDER BY count DESC
                LIMIT 20
                """,
                score_params,
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
                "total_trace_duration_ms": total_trace_duration_ms,
            },
            "cost_totals": {
                "estimated_cost_usd": cost_totals["estimated_cost_usd"] or 0,
                "shopify_proxy_cost_usd": cost_totals["shopify_proxy_cost_usd"] or 0,
            },
            "cost_by_application": [dict(row) for row in cost_by_app],
            "shopify_cost_by_application": [dict(row) for row in shopify_cost_by_app],
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
            "connections": [self._connection_row(row) for row in connections],
        }

    @staticmethod
    def _trace_filter_parts(
        *,
        application: str | None = None,
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
        if since is not None:
            clauses.append(f"{prefix}started_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append(f"{prefix}started_at < ?")
            params.append(until)
        return clauses, params

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS traces (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
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
                        cost_usd REAL,
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

                    CREATE INDEX IF NOT EXISTS idx_traces_application ON traces(application);
                    CREATE INDEX IF NOT EXISTS idx_traces_started_at ON traces(started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);
                    CREATE INDEX IF NOT EXISTS idx_spans_kind ON spans(kind);
                    CREATE INDEX IF NOT EXISTS idx_scores_trace_id ON scores(trace_id);
                    CREATE INDEX IF NOT EXISTS idx_connections_application ON connections(application);
                    """
                )

    @staticmethod
    def _trace_row(row: sqlite3.Row) -> JsonDict:
        data = dict(row)
        data["tags"] = _load_json(data.get("tags")) or []
        data["metadata"] = _load_json(data.get("metadata")) or {}
        return data

    @staticmethod
    def _span_row(row: sqlite3.Row) -> JsonDict:
        data = dict(row)
        data["prompt"] = _load_json(data.get("prompt"))
        data["response"] = _load_json(data.get("response"))
        data["metadata"] = _load_json(data.get("metadata")) or {}
        return data

    @staticmethod
    def _score_row(row: sqlite3.Row) -> JsonDict:
        data = dict(row)
        data["metadata"] = _load_json(data.get("metadata")) or {}
        return data

    @staticmethod
    def _feedback_row(row: sqlite3.Row) -> JsonDict:
        data = dict(row)
        data["metadata"] = _load_json(data.get("metadata")) or {}
        return data

    @staticmethod
    def _event_row(row: sqlite3.Row) -> JsonDict:
        data = dict(row)
        data["payload"] = _load_json(data.get("payload")) or {}
        return data

    @staticmethod
    def _connection_row(row: sqlite3.Row) -> JsonDict:
        data = dict(row)
        data["metadata"] = _load_json(data.get("metadata")) or {}
        return data
