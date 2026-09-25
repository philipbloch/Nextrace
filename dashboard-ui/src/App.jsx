import { useEffect, useMemo, useState } from "react";
import nextraceLogoUrl from "./assets/nextrace-logo.svg";

const DAY_MS = 24 * 60 * 60 * 1000;
const TRACE_LIMIT = 100;

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function dateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function localDateStart(value) {
  if (!value) return null;
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
}

function presetDateRange(preset) {
  const today = new Date();
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  if (preset === "yesterday") start.setDate(start.getDate() - 1);
  const end = new Date(start);
  return {
    start: dateInputValue(start),
    end: dateInputValue(end),
  };
}

function fmtMoney(value) {
  const n = Number(value || 0);
  if (Math.abs(n) > 0 && Math.abs(n) < 0.01) return `$${n.toFixed(4)}`;
  return usd.format(n);
}

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

function spendView(summary) {
  const costTotals = summary?.cost_totals || {};
  const allCost =
    costTotals.estimated_cost_usd ??
    (summary?.cost_by_application || []).reduce((sum, row) => sum + Number(row.cost_usd || 0), 0);
  const shopifyCost = Number(costTotals.shopify_proxy_cost_usd || 0);
  const hasShopifyCost = shopifyCost > 0;
  return {
    cost: hasShopifyCost ? shopifyCost : Number(allCost || 0),
    rows: hasShopifyCost ? summary?.shopify_cost_by_application || [] : summary?.cost_by_application || [],
    label: hasShopifyCost ? "Shopify Cost" : "Estimated Cost",
    note: hasShopifyCost ? "Proxy billing estimate" : "All tracked providers",
    panelTitle: hasShopifyCost ? "Shopify Cost by Application" : "Cost by Application",
    panelDescription: hasShopifyCost
      ? "Shopify-proxy spend grouped by app."
      : "Estimated spend grouped by app.",
  };
}

function JsonBlock({ title, value }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <pre>
      {title ? <strong>{title}{"\n"}</strong> : null}
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
            <div id="rangePopover" className="range-popover" role="dialog" aria-labelledby="rangePopoverTitle">
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
  const spend = spendView(summary);

  return (
    <section className="kpis" aria-label="Trace metrics">
      <div className="kpi">
        <div className="label">Traces</div>
        <div className="value">{summary?.totals?.traces || 0}</div>
      </div>
      <div className="kpi kpi-failures">
        <div className="label">Failure Rate</div>
        <div className="value">{total ? fmtPct(failed / total) : "0%"}</div>
      </div>
      <div className="kpi kpi-cost">
        <div className="label">{spend.label}</div>
        <div className="value">{fmtMoney(spend.cost)}</div>
        <div className="kpi-note">{spend.note}</div>
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

function TraceStatusFilter({ value, onChange }) {
  return (
    <div className="segmented" aria-label="Trace status filter">
      {["all", "ok", "error"].map((status) => (
        <button
          key={status}
          className={`segment-button ${value === status ? "active" : ""}`}
          type="button"
          aria-pressed={value === status}
          onClick={() => onChange(status)}
        >
          {status === "all" ? "All" : status === "ok" ? "OK" : "Error"}
        </button>
      ))}
    </div>
  );
}

function TraceList({ traces, selectedTraceId, traceStatus, onSelect }) {
  if (!traces.length) {
    const label = traceStatus === "all" ? "" : `${traceStatus} `;
    return <div className="empty">No {label}traces recorded.</div>;
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
          <span>{fmtMoney(trace.cost_usd)}</span>
        </div>
      </div>
      <span className={trace.status === "error" ? "error" : "ok"}>{trace.status}</span>
    </button>
  ));
}

function TraceDetail({ trace }) {
  if (!trace) return <div className="empty">Select a trace.</div>;
  const spans = trace.spans || [];

  return (
    <div>
      <div className="timeline">
        <TraceRunView trace={trace} />
        {spans.length ? (
          spans.map((span) => <SpanView key={span.id} span={span} />)
        ) : (
          <div className="empty">No spans recorded for this trace.</div>
        )}
      </div>
      {(trace.scores || []).length ? (
        <>
          <h2 className="detail-heading">Scores</h2>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Value</th>
                <th>Comment</th>
              </tr>
            </thead>
            <tbody>
              {trace.scores.map((score) => (
                <tr key={score.id}>
                  <td>{score.name}</td>
                  <td>{Number(score.value).toFixed(3)}</td>
                  <td>{score.comment || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
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
          <div className="meta">
            <span>trace {trace.id}</span>
            <span>session {trace.session_id}</span>
            <span>{fmtMs(trace.duration_ms)}</span>
            <span>{trace.spans?.length || 0} spans</span>
          </div>
        </div>
        <span className={trace.status === "error" ? "error" : "ok"}>{trace.status}</span>
      </div>
      {trace.error ? <pre>{trace.error}</pre> : null}
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
            <span>{fmtMs(span.duration_ms)}</span>
            <span>{fmtMoney(span.cost_usd)}</span>
            {tokens ? <span>{tokens}</span> : null}
            {span.retry_count ? <span>{span.retry_count} retries</span> : null}
          </div>
        </div>
        <span className={span.status === "error" ? "error" : "ok"}>{span.status}</span>
      </div>
      {span.error ? <pre>{span.error}</pre> : null}
      <JsonBlock title="Prompt/Input" value={span.prompt} />
      <JsonBlock title="Response/Output" value={span.response} />
    </div>
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
        <div className="bar-value">{formatter(value)}</div>
      </div>
    );
  });
}

function SlowSteps({ rows }) {
  if (!rows.length) return <div className="empty">No span timings.</div>;
  const sortedRows = [...rows].sort(
    (a, b) =>
      Number(b.avg_ms || 0) - Number(a.avg_ms || 0) ||
      Number(b.max_ms || 0) - Number(a.max_ms || 0) ||
      Number(b.count || 0) - Number(a.count || 0),
  );

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
          {sortedRows.map((row) => (
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
  const sortedRows = [...rows].sort(
    (a, b) =>
      Number(hasQualityScore(b)) - Number(hasQualityScore(a)) ||
      Number(b.accuracy ?? -1) - Number(a.accuracy ?? -1) ||
      Number(b.success_rate ?? -1) - Number(a.success_rate ?? -1) ||
      Number(b.calls || 0) - Number(a.calls || 0),
  );
  const showQuality = sortedRows.some(hasQualityScore);

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
          {sortedRows.map((row) => (
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
          <col />
        </colgroup>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Model</th>
            <th>Calls</th>
            <th>Latency</th>
            <th>Cost</th>
            <th>Tokens</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.provider || "unknown"}:${row.model || "unknown"}`}>
              <td>{row.provider || "unknown"}</td>
              <td>{row.model || "unknown"}</td>
              <td>{row.calls}</td>
              <td>{fmtMs(row.avg_latency_ms)}</td>
              <td>{fmtMoney(row.cost_usd)}</td>
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

async function fetchJson(url, signal) {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

export default function App() {
  const [summary, setSummary] = useState(null);
  const [appListSummary, setAppListSummary] = useState(null);
  const [traces, setTraces] = useState([]);
  const [selectedTraceId, setSelectedTraceId] = useState(null);
  const [selectedTrace, setSelectedTrace] = useState(null);
  const [application, setApplication] = useState("");
  const [traceStatus, setTraceStatus] = useState("all");
  const [rangeMode, setRangeMode] = useState("today");
  const [appliedStart, setAppliedStart] = useState(() => dateInputValue(new Date()));
  const [appliedEnd, setAppliedEnd] = useState(() => dateInputValue(new Date()));
  const [refreshToken, setRefreshToken] = useState(0);
  const [error, setError] = useState("");

  const rangeWindow = useMemo(() => {
    if (rangeMode === "today" || rangeMode === "yesterday") {
      const presetRange = presetDateRange(rangeMode);
      const start = localDateStart(presetRange.start);
      const end = localDateStart(presetRange.end);
      const until = end ? new Date(end.getTime() + DAY_MS) : null;
      return {
        since: start ? start.getTime() / 1000 : null,
        until: until ? until.getTime() / 1000 : null,
        label: `Showing ${rangeMode === "today" ? "Today" : "Yesterday"}`,
      };
    }

    const start = localDateStart(appliedStart);
    let end = localDateStart(appliedEnd);
    if (start && end && end < start) end = start;
    const until = end ? new Date(end.getTime() + DAY_MS) : null;
    return {
      since: start ? start.getTime() / 1000 : null,
      until: until ? until.getTime() / 1000 : null,
      label: `Showing ${start ? start.toLocaleDateString() : "Beginning"} to ${
        end ? end.toLocaleDateString() : "Now"
      }`,
    };
  }, [appliedEnd, appliedStart, rangeMode]);

  const applications = useMemo(() => {
    const costApps = (appListSummary?.cost_by_application || []).map((row) => row.application);
    const connectionApps = (appListSummary?.connections || []).map((row) => row.application);
    return Array.from(new Set([...costApps, ...connectionApps])).filter(Boolean).sort();
  }, [appListSummary]);

  const spend = spendView(summary);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setError("");
      const summaryParams = new URLSearchParams();
      if (application) summaryParams.set("application", application);
      if (rangeWindow.since != null) summaryParams.set("since", String(rangeWindow.since));
      if (rangeWindow.until != null) summaryParams.set("until", String(rangeWindow.until));
      const traceParams = new URLSearchParams(summaryParams);
      if (traceStatus !== "all") traceParams.set("status", traceStatus);
      traceParams.set("limit", String(TRACE_LIMIT));
      const summaryUrl = summaryParams.toString() ? `/api/summary?${summaryParams}` : "/api/summary";
      const traceUrl = traceParams.toString() ? `/api/traces?${traceParams}` : "/api/traces";
      const [nextSummary, nextAppListSummary, nextTraces] = await Promise.all([
        fetchJson(summaryUrl, controller.signal),
        fetchJson("/api/summary", controller.signal),
        fetchJson(traceUrl, controller.signal),
      ]);
      setSummary(nextSummary);
      setAppListSummary(nextAppListSummary);
      setTraces(nextTraces);
      setSelectedTraceId((current) =>
        nextTraces.some((trace) => trace.id === current) ? current : (nextTraces[0]?.id ?? null),
      );
    }

    load().catch((loadError) => {
      if (loadError.name !== "AbortError") setError(loadError.message);
    });
    return () => controller.abort();
  }, [application, rangeWindow.since, rangeWindow.until, refreshToken, traceStatus]);

  useEffect(() => {
    if (!selectedTraceId) {
      setSelectedTrace(null);
      return;
    }
    const controller = new AbortController();
    fetchJson(`/api/traces/${encodeURIComponent(selectedTraceId)}`, controller.signal)
      .then(setSelectedTrace)
      .catch((detailError) => {
        if (detailError.name !== "AbortError") setError(detailError.message);
      });
    return () => controller.abort();
  }, [selectedTraceId]);

  const applyRange = (start, end) => {
    setAppliedStart(start);
    setAppliedEnd(end);
    setRangeMode("custom");
    setSelectedTraceId(null);
  };

  const applyPresetRange = (preset) => {
    const nextRange = presetDateRange(preset);
    setAppliedStart(nextRange.start);
    setAppliedEnd(nextRange.end);
    setRangeMode(preset);
    setSelectedTraceId(null);
  };

  return (
    <div className="app-shell">
      <Header
        applications={applications}
        selectedApplication={application}
        onApplicationChange={(value) => {
          setApplication(value);
          setSelectedTraceId(null);
        }}
        rangeMode={rangeMode}
        rangeLabel={rangeWindow.label}
        appliedStart={appliedStart}
        appliedEnd={appliedEnd}
        onPresetRange={applyPresetRange}
        onApplyRange={applyRange}
        onRefresh={() => setRefreshToken((value) => value + 1)}
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
              description="Runs captured in the selected time range."
              className="trace-list-panel"
              actions={
                <TraceStatusFilter
                  value={traceStatus}
                  onChange={(value) => {
                    setTraceStatus(value);
                    setSelectedTraceId(null);
                  }}
                />
              }
            >
              <div className="trace-list">
                <TraceList
                  traces={traces}
                  selectedTraceId={selectedTraceId}
                  traceStatus={traceStatus}
                  onSelect={setSelectedTraceId}
                />
              </div>
            </DashboardPanel>

            <DashboardPanel
              title="Trace Detail"
              description="Spans, prompts, outputs, and errors for the selected run."
              className="trace-detail-panel"
            >
              <div className="trace-detail">
                <TraceDetail trace={selectedTrace} />
              </div>
            </DashboardPanel>
          </div>
        </section>

        <section className="dashboard-section" aria-label="Analysis">
          <div className="section-heading">
            <span>Analyze</span>
            <h2>Spend and Reliability</h2>
          </div>
          <div className="analysis-grid">
            <DashboardPanel
              title={spend.panelTitle}
              description={spend.panelDescription}
              className="cost-panel"
            >
              <div className="bars">
                <Bars
                  rows={spend.rows}
                  labelKey="application"
                  valueKey="cost_usd"
                  formatter={fmtMoney}
                  color="green"
                />
              </div>
            </DashboardPanel>

            <DashboardPanel
              title="Failure Rates"
              description="Error share by app for the selected range."
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
              title="Prompt and Model Comparisons"
              description="Provider, model, latency, cost, and token averages."
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
