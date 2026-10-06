import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import nextraceLogoUrl from "./assets/nextrace-logo.svg";

import { dateInputValue, dateRangeWindow, localDateStart, presetDateRange } from "./dates.js";
import { layoutWaterfall } from "./waterfall.js";
import { errorDiagnosis, hasFailure } from "./diagnosis.js";

const TRACE_LIMIT = 100;

function fmtMs(value) {
  const n = Number(value || 0);
  if (n >= 1000) return `${(n / 1000).toFixed(2)}s`;
  return `${n.toFixed(1)}ms`;
}

function fmtPct(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function fmtTime(epoch) {
  if (!epoch) return "";
  return new Date(epoch * 1000).toLocaleString();
}

function JsonBlock({ title, value }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <pre>
      {title ? (
        <strong>
          {title}
          {"\n"}
        </strong>
      ) : null}
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function CalendarIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </svg>
  );
}

function Header({
  applications,
  selectedApplication,
  onApplicationChange,
  selectedSession,
  onSessionChange,
  rangeMode,
  rangeLabel,
  appliedStart,
  appliedEnd,
  onPresetRange,
  onApplyRange,
  onRefresh,
}) {
  const [open, setOpen] = useState(false);
  const [draftStart, setDraftStart] = useState(appliedStart);
  const [draftEnd, setDraftEnd] = useState(appliedEnd);

  useEffect(() => {
    if (!open) {
      setDraftStart(appliedStart);
      setDraftEnd(appliedEnd);
    }
  }, [appliedEnd, appliedStart, open]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (event.target.closest(".range-picker")) return;
      setOpen(false);
    };
    const onKeyDown = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const applyRange = () => {
    let nextEnd = draftEnd;
    if (draftStart && draftEnd && localDateStart(draftEnd) < localDateStart(draftStart)) {
      nextEnd = draftStart;
      setDraftEnd(nextEnd);
    }
    onApplyRange(draftStart, nextEnd);
    setOpen(false);
  };

  return (
    <header>
      <div className="brand">
        <img className="brand-logo" src={nextraceLogoUrl} alt="" aria-hidden="true" />
        <div>
          <h1>Nextrace</h1>
          <div className="tagline">Trace the intelligence. Trace deeper. Build smarter.</div>
        </div>
      </div>

      <div className="toolbar">
        <input
          type="search"
          aria-label="Session filter"
          placeholder="Filter by session ID"
          value={selectedSession}
          onChange={(event) => onSessionChange(event.target.value)}
        />
        <select
          value={selectedApplication}
          onChange={(event) => onApplicationChange(event.target.value)}
          aria-label="Application filter"
        >
          <option value="">All applications</option>
          {applications.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        {["today", "yesterday"].map((preset) => (
          <button
            className={`range-button ${rangeMode === preset ? "active" : ""}`}
            type="button"
            title={`Show ${preset}`}
            aria-pressed={rangeMode === preset}
            onClick={() => {
              setOpen(false);
              onPresetRange(preset);
            }}
            key={preset}
          >
            {preset === "today" ? "Today" : "Yesterday"}
          </button>
        ))}

        <div className="range-picker">
          <button
            className="icon-button"
            type="button"
            title="Choose date range"
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-controls="rangePopover"
            onClick={() => setOpen((value) => !value)}
          >
            <CalendarIcon />
            <span className="sr-only">Choose date range</span>
          </button>

          {open ? (
            <div
              id="rangePopover"
              className="range-popover"
              role="dialog"
              aria-labelledby="rangePopoverTitle"
            >
              <h2 id="rangePopoverTitle">Date range</h2>
              <div className="range-fields">
                <label className="range-field">
                  Start
                  <input
                    type="date"
                    value={draftStart}
                    onChange={(event) => setDraftStart(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") applyRange();
                    }}
                    aria-label="Start date"
                  />
                </label>
                <label className="range-field">
                  End
                  <input
                    type="date"
                    value={draftEnd}
                    onChange={(event) => setDraftEnd(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") applyRange();
                    }}
                    aria-label="End date"
                  />
                </label>
              </div>
              <div className="range-actions">
                <button className="secondary-button" type="button" onClick={() => setOpen(false)}>
                  Cancel
                </button>
                <button className="primary-button" type="button" onClick={applyRange}>
                  Apply
                </button>
              </div>
            </div>
          ) : null}
        </div>

        <button className="refresh-button" type="button" title="Refresh traces" onClick={onRefresh}>
          Refresh
        </button>
        <div className="range-label">{rangeLabel}</div>
      </div>
    </header>
  );
}

function KpiCards({ summary, applicationCount }) {
  const failures = summary?.failure_rates || [];
  const total = failures.reduce((sum, row) => sum + Number(row.total || 0), 0);
  const failed = failures.reduce((sum, row) => sum + Number(row.failures || 0), 0);

  return (
    <section className="kpis" aria-label="Trace metrics">
      <div className="kpi">
        <div className="label">Traces</div>
        <div className="value">{summary?.totals?.traces || 0}</div>
        <div className="kpi-note">
          {summary?.totals?.running || 0} running · {summary?.totals?.interrupted || 0} interrupted
        </div>
      </div>
      <div className="kpi kpi-failures">
        <div className="label">Failure Rate</div>
        <div className="value">{total ? fmtPct(failed / total) : "0%"}</div>
        <div className="kpi-note">Completed runs only</div>
      </div>
      <div className="kpi">
        <div className="label">Sessions</div>
        <div className="value">{summary?.totals?.sessions || 0}</div>
        <div className="kpi-note">{summary?.totals?.spans || 0} recorded steps</div>
      </div>
      <div className="kpi kpi-apps">
        <div className="label">Applications</div>
        <div className="value">{applicationCount}</div>
      </div>
    </section>
  );
}

function DashboardPanel({ title, description, actions, className = "", children }) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <h2 className="panel-title">{title}</h2>
        {actions}
      </div>
      {description ? <p className="panel-note">{description}</p> : null}
      <div className="panel-body">{children}</div>
    </section>
  );
}

const statusLabels = {
  all: "All",
  running: "Running",
  ok: "OK",
  error: "Error",
  interrupted: "Interrupted",
};

function StatusBadge({ status }) {
  return <span className={`status-badge ${status}`}>{statusLabels[status] || status}</span>;
}

function TraceStatusFilter({ value, onChange }) {
  return (
    <div className="segmented" aria-label="Trace status filter">
      {Object.keys(statusLabels).map((status) => (
        <button
          key={status}
          className={`segment-button ${value === status ? "active" : ""}`}
          type="button"
          aria-pressed={value === status}
          onClick={() => onChange(status)}
        >
          {statusLabels[status]}
        </button>
      ))}
    </div>
  );
}

function TraceList({ traces, selectedTraceId, traceStatus, onSelect }) {
  if (!traces.length) {
    const label = traceStatus === "all" ? "" : `${traceStatus} `;
    return <div className="empty">No {label}traces match the current filters.</div>;
  }

  return traces.map((trace) => (
    <button
      className={`trace-row ${trace.id === selectedTraceId ? "active" : ""}`}
      key={trace.id}
      type="button"
      onClick={() => onSelect(trace.id)}
    >
      <div>
        <div className="trace-title">{trace.name || trace.application}</div>
        <div className="meta">
          <span>{trace.application}</span>
          <span>{fmtTime(trace.started_at)}</span>
          <span>{trace.span_count || 0} spans</span>
        </div>
      </div>
      <StatusBadge status={trace.status} />
    </button>
  ));
}

function TraceDetail({ trace }) {
  const [collapsed, setCollapsed] = useState(new Set());
  const [selectedSpanId, setSelectedSpanId] = useState(null);
  const inspectionRef = useRef(null);
  const scrollToInspection = useCallback(() => {
    const section = inspectionRef.current;
    const pane = section?.closest(".panel-body");
    if (!pane) return;
    pane.scrollBy({
      top: section.getBoundingClientRect().top - pane.getBoundingClientRect().top - 8,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    });
  }, []);
  useEffect(() => {
    setCollapsed(new Set());
    setSelectedSpanId(null);
  }, [trace?.id]);
  useEffect(() => {
    if (selectedSpanId) scrollToInspection();
  }, [selectedSpanId, scrollToInspection]);
  if (!trace) return <div className="empty">Select a trace.</div>;
  const layout = layoutWaterfall(trace);
  const selected = trace.spans?.find((span) => span.id === selectedSpanId);
  const inspectSpan = (id) => {
    setSelectedSpanId(id);
    if (id === selectedSpanId) scrollToInspection();
  };
  const toggle = (id) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return (
    <div>
      <TraceRunView trace={trace} />
      <div className="execution-context">
        {trace.turn_id ? <span>Turn {trace.turn_id}</span> : null}
        <span>{trace.correlated_recordings || 0} correlated MCP recordings</span>
        {trace.ended_at != null && trace.status !== "running" ? (
          <a href={`/api/traces/${encodeURIComponent(trace.id)}/otel`} download>
            Export OpenTelemetry
          </a>
        ) : null}
      </div>
      <ErrorDiagnosis record={hasFailure(selected) ? selected : trace} trace={trace} />
      <div className="waterfall" aria-label="Nested timing waterfall">
        <div className="waterfall-axis">
          <span>Step</span>
          <div>
            <span>0</span>
            <span>{fmtMs(layout.durationMs)}</span>
          </div>
        </div>
        {layout.rows.length ? (
          layout.rows
            .filter((row) => !row.ancestors.some((id) => collapsed.has(id)))
            .map((row) => (
              <div
                className={`waterfall-row ${selectedSpanId === row.span.id ? "selected" : ""}`}
                key={row.span.id}
              >
                <div className="waterfall-label" style={{ paddingLeft: `${row.depth * 14}px` }}>
                  {row.children ? (
                    <button
                      className="waterfall-toggle"
                      onClick={() => toggle(row.span.id)}
                      aria-label={`${collapsed.has(row.span.id) ? "Expand" : "Collapse"} ${row.span.name}`}
                      aria-expanded={!collapsed.has(row.span.id)}
                    >
                      {collapsed.has(row.span.id) ? "▸" : "▾"}
                    </button>
                  ) : (
                    <span className="waterfall-toggle" />
                  )}
                  <button
                    className="waterfall-name"
                    title={row.span.name}
                    onClick={() => inspectSpan(row.span.id)}
                  >
                    {row.span.name}
                  </button>
                </div>
                <button
                  className="waterfall-track"
                  onClick={() => inspectSpan(row.span.id)}
                  aria-label={`Inspect ${row.span.name}: ${row.unknown ? "duration unavailable" : fmtMs(row.span.duration_ms)}`}
                >
                  <span
                    className={`waterfall-bar status-${row.span.status} ${row.unknown || !row.width ? "point" : ""}`}
                    style={{ left: `${row.offset}%`, width: `${row.width}%` }}
                  />
                  <span className="waterfall-duration">
                    {row.unknown ? "Timing unavailable" : fmtMs(row.span.duration_ms)}
                    {row.span.metadata?.timing === "log_interval" ? " · log interval" : ""}
                  </span>
                </button>
              </div>
            ))
        ) : (
          <div className="empty">No spans recorded for this trace.</div>
        )}
      </div>
      <section ref={inspectionRef} aria-label="Step inspection">
        <p className="panel-note">
          Select a step to inspect it. Dots mark events with no measured duration.
        </p>
        {selected ? <SpanView span={selected} /> : null}
      </section>
      {(trace.scores || []).length ? <JsonBlock title="Scores" value={trace.scores} /> : null}
    </div>
  );
}

function TraceRunView({ trace }) {
  return (
    <div className="span trace-run">
      <div className="span-head">
        <div>
          <div className="timeline-step-label">Trace run</div>
          <div className="trace-title">{trace.name || trace.application}</div>
          <div className="meta trace-identifiers">
            <span>trace {trace.id}</span>
            <span>session {trace.session_id}</span>
          </div>
          <div className="meta">
            <span>
              {trace.ended_at == null
                ? trace.status === "running"
                  ? "In progress"
                  : "Completion time unavailable"
                : fmtMs(trace.duration_ms)}
            </span>
            <span>
              {trace.spans?.filter((span) => span.kind !== "observation").length || 0} recorded
              steps
            </span>
          </div>
        </div>
        <StatusBadge status={trace.status} />
      </div>
    </div>
  );
}

function SpanView({ span }) {
  const label = [span.kind, span.provider, span.model].filter(Boolean).join(" / ");
  const tokens = span.total_tokens ? `${span.total_tokens} tokens` : "";

  return (
    <div className="span">
      <div className="span-head">
        <div>
          <div className="trace-title">{span.name}</div>
          <div className="meta">
            {label ? <span>{label}</span> : null}
            <span>
              {span.metadata?.timing === "unknown" ? "Timing unavailable" : fmtMs(span.duration_ms)}
            </span>
            {tokens ? <span>{tokens}</span> : null}
            {span.retry_count ? <span>{span.retry_count} retries</span> : null}
          </div>
        </div>
        <StatusBadge status={span.status} />
      </div>
      <JsonBlock title="Prompt/Input" value={span.prompt} />
      <JsonBlock title="Response/Output" value={span.response} />
    </div>
  );
}

function ErrorDiagnosis({ record, trace }) {
  const diagnosis = errorDiagnosis(record, trace);
  if (!diagnosis) return null;
  return (
    <section className="error-diagnosis" aria-label="Error diagnosis">
      <div className="span-head">
        <div>
          <h3>Error diagnosis</h3>
          <div className="meta">{diagnosis.name}</div>
        </div>
        <div className="meta">
          {diagnosis.httpStatus != null ? <span>HTTP {diagnosis.httpStatus}</span> : null}
          {diagnosis.codes.map((code, index) => (
            <span key={index}>Code {code}</span>
          ))}
          {diagnosis.exceptionType ? <span>{diagnosis.exceptionType}</span> : null}
        </div>
      </div>
      <p>{diagnosis.explanation}</p>
      {diagnosis.error && (diagnosis.missingMessage || diagnosis.hasMessage) ? (
        <div className="meta diagnosis-failure">Recorded failure: {diagnosis.error}</div>
      ) : null}
      {diagnosis.details.map((item, index) => (
        <div key={index}>
          {diagnosis.details.length > 1 ? (
            <h4>
              Failed response {index + 1}
              {item.code != null ? ` · Code ${item.code}` : ""}
            </h4>
          ) : null}
          {item.message ? <pre aria-label="Original error message">{item.message}</pre> : null}
          {item.message_truncated ? (
            <p className="panel-note">Message exceeded the capture limit and was truncated.</p>
          ) : null}
        </div>
      ))}
      {!diagnosis.hasMessage && !diagnosis.missingMessage && diagnosis.error ? (
        <pre aria-label="Original error message">{diagnosis.error}</pre>
      ) : null}
      {diagnosis.missingMessage ? (
        <p className="panel-note">
          The original error message was not captured for this recording. Check the original tool
          response or upstream logs; Nextrace cannot reconstruct the cause.
        </p>
      ) : null}
      {diagnosis.traceback ? (
        <details className="diagnosis-traceback">
          <summary>Exception traceback</summary>
          <pre>{diagnosis.traceback}</pre>
        </details>
      ) : null}
      <details className="diagnosis-identifiers">
        <summary>Correlation IDs</summary>
        <dl>
          {diagnosis.identifiers.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </section>
  );
}

function Bars({ rows, labelKey, valueKey, formatter, color }) {
  if (!rows.length) return <div className="empty">No data.</div>;
  const sortedRows = [...rows].sort((a, b) => Number(b[valueKey] || 0) - Number(a[valueKey] || 0));
  const max = Math.max(...sortedRows.map((row) => Number(row[valueKey] || 0)), 0.000001);

  return sortedRows.map((row) => {
    const value = Number(row[valueKey] || 0);
    const width = value <= 0 ? 0 : Math.max(2, (value / max) * 100);
    return (
      <div className="bar-row" key={row[labelKey] || "Unknown"}>
        <div className="bar-name">{row[labelKey] || "Unknown"}</div>
        <div className="bar-track">
          <div className={`bar-fill ${color}`} style={{ width: `${width}%` }} />
        </div>
        <div className="bar-value">{formatter(value, row)}</div>
      </div>
    );
  });
}

function SlowSteps({ rows }) {
  if (!rows.length) return <div className="empty">No span timings.</div>;

  return (
    <div className="table-wrap">
      <table className="metric-table slow-table">
        <colgroup>
          <col />
          <col />
          <col />
          <col />
          <col />
        </colgroup>
        <thead>
          <tr>
            <th>Step</th>
            <th>Kind</th>
            <th>Avg</th>
            <th>Max</th>
            <th>Calls</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.kind}:${row.name}:${row.provider || ""}:${row.model || ""}`}>
              <td>{row.name}</td>
              <td>{row.kind}</td>
              <td>{fmtMs(row.avg_ms)}</td>
              <td>{fmtMs(row.max_ms)}</td>
              <td>{row.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ToolAccuracy({ rows }) {
  if (!rows.length) return <div className="empty">No tool calls.</div>;
  const hasQualityScore = (row) => row.accuracy != null;
  const showQuality = rows.some(hasQualityScore);

  return (
    <div className="table-wrap">
      <table className={`metric-table tool-table ${showQuality ? "" : "tool-table-simple"}`}>
        <colgroup>
          <col />
          <col />
          <col />
          {showQuality ? <col /> : null}
        </colgroup>
        <thead>
          <tr>
            <th>Tool</th>
            <th>Calls</th>
            <th>Success</th>
            {showQuality ? <th>Quality</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
              <td>{row.calls}</td>
              <td>{row.success_rate == null ? "-" : fmtPct(row.success_rate)}</td>
              {showQuality ? <td>{hasQualityScore(row) ? fmtPct(row.accuracy) : "-"}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ModelTable({ rows }) {
  if (!rows.length) return <div className="empty">No model calls.</div>;

  return (
    <div className="table-wrap">
      <table className="metric-table model-table">
        <colgroup>
          <col />
          <col />
          <col />
          <col />
          <col />
        </colgroup>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Model</th>
            <th>Calls</th>
            <th>Latency</th>
            <th>Tokens</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.provider || "unknown"}:${row.model || "unknown"}`}>
              <td>{row.provider || "unknown"}</td>
              <td>{row.model || "unknown"}</td>
              <td>{row.calls}</td>
              <td>{row.avg_latency_ms == null ? "Unavailable" : fmtMs(row.avg_latency_ms)}</td>
              <td>{Number(row.avg_tokens || 0).toFixed(0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Connections({ rows }) {
  if (!rows.length) return <div className="empty">No apps or tools have sent data yet.</div>;

  return rows.map((row) => (
    <div className="connection-row" key={`${row.application}:${row.source}:${row.transport}`}>
      <div>
        <div className="trace-title">{row.source || row.application}</div>
        <div className="meta">
          <span>method: {row.transport}</span>
          <span>app: {row.application}</span>
          <span>last data: {fmtTime(row.last_seen_at)}</span>
        </div>
      </div>
      <span className={row.status === "connected" ? "ok" : "error"}>
        {row.status === "connected" ? "received" : row.status}
      </span>
    </div>
  ));
}

function useJson(url, refreshToken = 0) {
  const [state, setState] = useState({ url: null, data: null, loading: false, error: "" });
  useEffect(() => {
    if (!url) return;
    const controller = new AbortController();
    setState((current) => ({ ...current, url, loading: true, error: "" }));
    fetch(url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Request failed: ${response.status}`);
        return response.json();
      })
      .then((data) => {
        if (!controller.signal.aborted) setState({ url, data, loading: false, error: "" });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setState((current) => ({ ...current, loading: false, error: error.message }));
      });
    return () => controller.abort();
  }, [url, refreshToken]);
  return {
    data: url ? state.data : null,
    loading: Boolean(url) && (state.loading || state.url !== url),
    error: state.url === url ? state.error : "",
  };
}

function RequestContent({ loading, error, loadingLabel, onRetry, children }) {
  if (loading)
    return (
      <div className="empty" role="status">
        {loadingLabel}
      </div>
    );
  if (error)
    return (
      <div className="empty" role="alert">
        {error}
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      </div>
    );
  return children;
}

export default function App() {
  const [selectedTraceId, setSelectedTraceId] = useState(null);
  const [application, setApplication] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [traceStatus, setTraceStatus] = useState("all");
  const [rangeMode, setRangeMode] = useState("today");
  const [appliedStart, setAppliedStart] = useState(() => dateInputValue(new Date()));
  const [appliedEnd, setAppliedEnd] = useState(() => dateInputValue(new Date()));
  const [refreshToken, setRefreshToken] = useState(0);

  const rangeWindow = useMemo(
    () => dateRangeWindow(rangeMode, appliedStart, appliedEnd),
    [appliedStart, appliedEnd, rangeMode, refreshToken],
  );

  const scopeQuery = useMemo(() => {
    const params = new URLSearchParams();
    if (application) params.set("application", application);
    if (sessionId) params.set("session_id", sessionId);
    if (rangeWindow.since != null) params.set("since", String(rangeWindow.since));
    if (rangeWindow.until != null) params.set("until", String(rangeWindow.until));
    return params.toString();
  }, [application, sessionId, rangeWindow.since, rangeWindow.until]);

  const applicationRequest = useJson("/api/applications", refreshToken);
  const summaryRequest = useJson(`/api/summary?${scopeQuery}`, refreshToken);
  const traceParams = new URLSearchParams(scopeQuery);
  if (traceStatus !== "all") traceParams.set("status", traceStatus);
  traceParams.set("limit", String(TRACE_LIMIT));
  const traceRequest = useJson(`/api/traces?${traceParams}`, refreshToken);
  const applications = applicationRequest.data || [];
  const traces = traceRequest.data || [];
  const activeTraceId =
    traceRequest.loading || traceRequest.error
      ? null
      : traces.some((trace) => trace.id === selectedTraceId)
        ? selectedTraceId
        : (traces[0]?.id ?? null);
  const detailRequest = useJson(
    activeTraceId ? `/api/traces/${encodeURIComponent(activeTraceId)}` : null,
  );
  const summary = summaryRequest.data;
  const error = applicationRequest.error || summaryRequest.error;
  const refresh = () => setRefreshToken((value) => value + 1);

  const applyRange = (start, end) => {
    setAppliedStart(start);
    setAppliedEnd(end);
    setRangeMode("custom");
  };

  const applyPresetRange = (preset) => {
    const nextRange = presetDateRange(preset);
    setAppliedStart(nextRange.start);
    setAppliedEnd(nextRange.end);
    setRangeMode(preset);
  };

  return (
    <div className="app-shell">
      <Header
        applications={applications}
        selectedApplication={application}
        selectedSession={sessionId}
        onSessionChange={setSessionId}
        onApplicationChange={setApplication}
        rangeMode={rangeMode}
        rangeLabel={rangeWindow.label}
        appliedStart={rangeWindow.start}
        appliedEnd={rangeWindow.end}
        onPresetRange={applyPresetRange}
        onApplyRange={applyRange}
        onRefresh={refresh}
      />

      <main>
        <KpiCards summary={summary} applicationCount={applications.length} />
        {error ? <div className="error-banner">{error}</div> : null}

        <section className="dashboard-section trace-explorer" aria-label="Trace explorer">
          <div className="section-heading">
            <span>Inspect</span>
            <h2>Trace Explorer</h2>
          </div>
          <div className="trace-workspace">
            <DashboardPanel
              title="Execution Traces"
              description="Agent turns and independent recordings in the selected time range."
              className="trace-list-panel"
              actions={<TraceStatusFilter value={traceStatus} onChange={setTraceStatus} />}
            >
              {sessionId ? (
                <div className="active-trace-filter">
                  <span title={sessionId}>Session: {sessionId}</span>
                  <button
                    type="button"
                    onClick={() => setSessionId("")}
                    aria-label="Clear session filter"
                  >
                    Clear
                  </button>
                </div>
              ) : null}
              <div className="trace-list" aria-busy={traceRequest.loading}>
                <RequestContent
                  loading={traceRequest.loading}
                  error={traceRequest.error}
                  loadingLabel="Loading traces…"
                  onRetry={refresh}
                >
                  <TraceList
                    traces={traces}
                    selectedTraceId={activeTraceId}
                    traceStatus={traceStatus}
                    onSelect={setSelectedTraceId}
                  />
                </RequestContent>
              </div>
            </DashboardPanel>

            <DashboardPanel
              title="Trace Detail"
              description="Nested steps and timings for the selected session and turn."
              className="trace-detail-panel"
            >
              <RequestContent
                loading={traceRequest.loading || detailRequest.loading}
                error={traceRequest.error || detailRequest.error}
                loadingLabel="Loading trace…"
                onRetry={refresh}
              >
                <TraceDetail trace={detailRequest.data} />
              </RequestContent>
            </DashboardPanel>
          </div>
        </section>

        <section className="dashboard-section" aria-label="Analysis">
          <div className="section-heading">
            <span>Analyze</span>
            <h2>Execution and Reliability</h2>
          </div>
          <div className="analysis-grid">
            <DashboardPanel
              title="Failure Rates"
              description="Errors among completed runs. Running and interrupted recordings are counted separately."
              className="failure-panel"
            >
              <div className="bars">
                <Bars
                  rows={summary?.failure_rates || []}
                  labelKey="application"
                  valueKey="failure_rate"
                  formatter={fmtPct}
                  color="red"
                />
              </div>
            </DashboardPanel>

            <DashboardPanel
              title="Models and Token Usage"
              description="Provider, model, observed latency, and token averages."
              className="models-panel"
            >
              <ModelTable rows={summary?.prompt_model_comparisons || []} />
            </DashboardPanel>
          </div>
        </section>

        <section className="dashboard-section" aria-label="Operations">
          <div className="section-heading">
            <span>Operate</span>
            <h2>Bottlenecks and Data Inputs</h2>
          </div>
          <div className="operations-grid">
            <DashboardPanel
              title="Slowest Steps"
              description="Steps with the highest average or max latency."
            >
              <SlowSteps rows={summary?.slowest_steps || []} />
            </DashboardPanel>

            <DashboardPanel
              title="Tool Calls"
              description="Call volume and completion rate by tool."
            >
              <ToolAccuracy rows={summary?.tool_call_accuracy || []} />
            </DashboardPanel>

            <DashboardPanel
              title="Trace Inputs"
              description="Apps and tools that sent traces or usage imports to Nextrace."
              className="connections-panel"
            >
              <div className="connection-list">
                <Connections rows={summary?.connections || []} />
              </div>
            </DashboardPanel>
          </div>
        </section>
      </main>
    </div>
  );
}
