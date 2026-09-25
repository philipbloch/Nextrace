import { useEffect, useRef, useState } from "react";
import nextraceLogoUrl from "./assets/nextrace-logo.svg";

const metrics = [
  ["OK / Error", "Execution trace filters"],
  ["Cost", "By project and model"],
  ["Redaction", "MCP-safe capture"],
  ["Local logs", "Codex, Claude Code, Pi, and more"],
];

const flowSteps = [
  "AI coding agents connect through local usage adapters or MCP.",
  "Nextrace proxies stdio MCPs and tool-gateway HTTP traffic.",
  "Dashboard groups traces, cost, latency, and status by application.",
];

const signals = [
  [
    "Execution traces",
    "Filter successful and failed runs, inspect steps, and spot slow tool calls.",
  ],
  [
    "Cost by application",
    "See which projects drive model spend across Codex, Claude Code, Pi, and MCP usage.",
  ],
  [
    "Prompt and model comparison",
    "Compare providers, models, token volume, latency, and estimated cost side by side.",
  ],
  [
    "Connected sources",
    "Verify active imports and MCP proxies before chasing missing data.",
  ],
];

const previewTerminalLines = [
  ["nextrace@local", "codex-import --latest 1"],
  ["trace", "codex:model:gpt-5.5 captured"],
  ["span", "shopify-proxy/gpt-5.5 redacted"],
  ["cost", "$0.12 estimated"],
  ["mcp", "tool-gateway initialize returned HTTP 401"],
  ["dashboard", "summary refreshed on port 8765"],
];

const previewTraces = [
  {
    name: "codex:model:gpt-5.5",
    app: "se-assistant",
    cost: "$0.12",
    status: "ok",
    duration: "0.0ms",
    tokens: "92,806",
  },
  {
    name: "mcp:tool-gateway:initialize",
    app: "nextrace",
    cost: "$0.00",
    status: "error",
    duration: "618.4ms",
    tokens: "0",
  },
  {
    name: "codex:model:gpt-5.5",
    app: "nextrace",
    cost: "$0.38",
    status: "ok",
    duration: "0.0ms",
    tokens: "188,412",
  },
];

const installCommand =
  'git clone https://github.com/philipbloch/Nextrace.git && cd Nextrace && python3 -m venv .venv && . .venv/bin/activate && python -m pip install -e ".[dashboard]" && scripts/install-launch-agents.sh';

const installSteps = [
  {
    title: "Prepare the local workspace",
    body: "Clone Nextrace, create an isolated Python environment, and activate it before installing.",
    command: `git clone https://github.com/philipbloch/Nextrace.git
cd Nextrace
python3 -m venv .venv
. .venv/bin/activate`,
  },
  {
    title: "Install the dashboard build",
    body: "Install the Python package with the dashboard extra so FastAPI and Uvicorn are available locally.",
    command: `python -m pip install -e ".[dashboard]"`,
  },
  {
    title: "Register local services (optional)",
    body: "On macOS, start the dashboard, MCP HTTP proxy, and recurring importer as LaunchAgents. Other platforms can run the same CLI services directly.",
    command: `scripts/install-launch-agents.sh`,
  },
  {
    title: "Open the dashboard",
    body: "Visit the local dashboard after the installer reports the LaunchAgents were installed.",
    command: `open http://127.0.0.1:8765`,
  },
];

const serviceFacts = [
  ["Dashboard", "http://127.0.0.1:8765"],
  ["MCP proxy", "http://127.0.0.1:8766/mcp"],
  ["Importer", "Runs every 5 minutes"],
];

const requirements = ["Python 3.10+", "git", "an AI agent or MCP client"];

function TerminalIcon() {
  return (
    <svg aria-hidden="true" className="section-icon" viewBox="0 0 24 24">
      <path d="m7 11 2-2-2-2" />
      <path d="M11 13h4" />
      <rect width="18" height="18" x="3" y="3" rx="2" />
    </svg>
  );
}

function HarnessIcon() {
  return (
    <svg aria-hidden="true" className="section-icon" viewBox="0 0 24 24">
      <circle cx="5" cy="12" r="2" />
      <circle cx="19" cy="6" r="2" />
      <circle cx="19" cy="18" r="2" />
      <path d="M7 12h4c3 0 3-6 6-6" />
      <path d="M11 12c3 0 3 6 6 6" />
    </svg>
  );
}

function SignalsIcon() {
  return (
    <svg aria-hidden="true" className="section-icon" viewBox="0 0 24 24">
      <path d="M3 12h4l2.5-6 4 12 2.5-6h5" />
      <path d="M4 4h16v16H4z" />
    </svg>
  );
}

function TraceIcon() {
  return (
    <svg aria-hidden="true" className="section-icon hero-icon" viewBox="0 0 24 24">
      <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
      <circle cx="12" cy="12" r="2.5" />
      <path d="M12 3v3M5.6 5.6l2.1 2.1M18.4 5.6l-2.1 2.1" />
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <rect width="14" height="14" x="8" y="8" rx="2" />
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path
        fill="currentColor"
        d="M12 .5A11.5 11.5 0 0 0 .5 12a11.5 11.5 0 0 0 7.86 10.92c.575.106.785-.25.785-.556 0-.274-.01-1-.015-1.964-3.196.695-3.87-1.54-3.87-1.54-.523-1.33-1.277-1.684-1.277-1.684-1.044-.714.08-.699.08-.699 1.154.081 1.761 1.185 1.761 1.185 1.026 1.758 2.693 1.25 3.35.955.104-.743.401-1.25.73-1.538-2.552-.29-5.235-1.276-5.235-5.68 0-1.255.448-2.28 1.183-3.084-.119-.29-.513-1.459.113-3.042 0 0 .965-.309 3.163 1.178a11 11 0 0 1 2.88-.388c.977.005 1.96.132 2.88.388 2.196-1.487 3.16-1.178 3.16-1.178.627 1.583.233 2.752.114 3.042.737.804 1.182 1.829 1.182 3.084 0 4.415-2.687 5.387-5.247 5.671.413.355.78 1.056.78 2.128 0 1.537-.014 2.776-.014 3.154 0 .308.207.667.79.554A11.5 11.5 0 0 0 23.5 12 11.5 11.5 0 0 0 12 .5Z"
      />
    </svg>
  );
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  try {
    textarea.select();
    if (!document.execCommand("copy")) throw new Error("Copy command failed");
  } finally {
    textarea.remove();
  }
}

function Header() {
  return (
    <header className="site-header">
      <a className="brand-mark" href="#top" aria-label="Nextrace home">
        <img className="brand-logo" src={nextraceLogoUrl} alt="" aria-hidden="true" />
        <span>Nextrace</span>
      </a>
      <nav className="site-nav" aria-label="Primary navigation">
        <a href="#connect">Connect</a>
        <a href="#signals">Signals</a>
        <a href="#run">Run</a>
      </nav>
      <a
        className="nav-github"
        href="https://github.com/philipbloch/Nextrace"
        target="_blank"
        rel="noreferrer"
      >
        <GitHubIcon />
        <span>GitHub</span>
      </a>
    </header>
  );
}

function Hero() {
  return (
    <section className="hero">
      <div className="hero-copy band">
        <TraceIcon />
        <p className="eyebrow">AI workflow tracing</p>
        <h1>Trace the intelligence.</h1>
        <p className="tagline">See every step your AI takes.</p>
        <p className="tagline tagline-secondary">Trace deeper. Build smarter.</p>
        <p className="lede">
          Nextrace follows Codex, Claude Code, Pi, other AI agents, MCP tools, local usage,
          and model cost from execution to outcome. Sensitive payloads stay out. Every AI
          workflow becomes visible enough to trace, measure, and improve.
        </p>
        <div className="hero-actions" aria-label="Primary actions">
          <a className="button primary" href="#run">
            Start locally
          </a>
          <a className="button secondary" href="#connect">
            See the harness
          </a>
        </div>
      </div>

      <ProductPreview />
    </section>
  );
}

function ProductPreview() {
  const [activeTrace, setActiveTrace] = useState(0);
  const trace = previewTraces[activeTrace];

  useEffect(() => {
    const id = window.setInterval(() => {
      setActiveTrace((current) => (current + 1) % previewTraces.length);
    }, 3200);

    return () => window.clearInterval(id);
  }, []);

  return (
    <figure className="product-visual" aria-label="Animated Nextrace dashboard preview">
      <div className="preview-window">
        <div className="preview-chrome" aria-hidden="true">
          <span className="chrome-dot pink" />
          <span className="chrome-dot cyan" />
          <span className="chrome-dot white" />
          <span className="chrome-path">~/nextrace</span>
          <span className="chrome-separator">/</span>
          <span>localhost:8765</span>
          <span className="chrome-live">live</span>
        </div>

        <div className="preview-stage">
          <div className="preview-terminal" aria-label="Local import stream">
            <div className="preview-panel-head">
              <span>Ingest</span>
              <strong>Local activity stream</strong>
            </div>
            <div className="terminal-feed">
              {previewTerminalLines.map(([label, body], index) => (
                <div className="terminal-line" key={`${label}:${body}`} style={{ "--delay": `${index * 0.36}s` }}>
                  <span>{label}</span>
                  <p>{body}</p>
                </div>
              ))}
            </div>
            <div className="packet-rail" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
          </div>

          <div className="preview-connector" aria-hidden="true">
            <span />
          </div>

          <div className="preview-dashboard" aria-label="Nextrace dashboard">
            <div className="preview-dashboard-top">
              <div>
                <span className="preview-eyebrow">Nextrace</span>
                <strong>Trace Explorer</strong>
              </div>
              <div className="preview-refresh">last 24h</div>
            </div>

            <div className="preview-kpis" aria-label="Dashboard summary">
              <div>
                <span>Traces</span>
                <strong>188</strong>
              </div>
              <div>
                <span>Failure Rate</span>
                <strong className="pink-text">3.2%</strong>
              </div>
              <div>
                <span>Cost</span>
                <strong className="cyan-text">$27.93</strong>
              </div>
            </div>

            <div className="preview-workspace">
              <section className="preview-panel trace-list-preview" aria-label="Execution traces">
                <div className="preview-panel-head">
                  <span>Inspect</span>
                  <strong>Execution Traces</strong>
                </div>
                <div className="trace-filter-row" aria-label="Trace filters">
                  <button type="button" className="trace-filter active">
                    All
                  </button>
                  <button type="button" className="trace-filter">
                    OK
                  </button>
                  <button type="button" className="trace-filter">
                    Error
                  </button>
                </div>
                <div className="preview-trace-list">
                  {previewTraces.map((row, index) => (
                    <button
                      className={`preview-trace-row ${activeTrace === index ? "active" : ""} ${row.status}`}
                      key={`${row.name}:${row.app}`}
                      type="button"
                      onClick={() => setActiveTrace(index)}
                    >
                      <span className="trace-pip" />
                      <span>
                        <strong>{row.name}</strong>
                        <small>
                          {row.app} / {row.duration} / {row.cost}
                        </small>
                      </span>
                      <em>{row.status}</em>
                    </button>
                  ))}
                </div>
              </section>

              <section className="preview-panel trace-detail-preview" aria-label="Trace detail">
                <div className="preview-panel-head">
                  <span>Detail</span>
                  <strong>{trace.name}</strong>
                </div>
                <div className={`detail-status ${trace.status}`}>{trace.status}</div>
                <div className="trace-detail-card">
                  <div>
                    <span>provider</span>
                    <strong>shopify-proxy</strong>
                  </div>
                  <div>
                    <span>tokens</span>
                    <strong>{trace.tokens}</strong>
                  </div>
                  <div>
                    <span>redaction</span>
                    <strong>enabled</strong>
                  </div>
                </div>
                <pre>{`{
  "prompt": "redacted",
  "source": "${trace.app}",
  "status": "${trace.status}"
}`}</pre>
              </section>
            </div>

            <div className="preview-insights" aria-label="Spend and reliability">
              <div className="mini-chart">
                <span>Cost by application</span>
                <i style={{ "--fill": "88%" }} />
                <i style={{ "--fill": "34%" }} />
              </div>
              <div className="mini-chart pink-chart">
                <span>Failure rates</span>
                <i style={{ "--fill": "72%" }} />
                <i style={{ "--fill": "7%" }} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </figure>
  );
}

function MetricStrip() {
  return (
    <section className="metric-strip" aria-label="Nextrace focus areas">
      {metrics.map(([label, detail]) => (
        <div key={label}>
          <strong>{label}</strong>
          <span>{detail}</span>
        </div>
      ))}
    </section>
  );
}

function ConnectSection() {
  return (
    <section id="connect" className="band split-section">
      <div className="connect-copy">
        <HarnessIcon />
        <p className="eyebrow">The harness</p>
        <h2>No new system of record. Just one operating lens.</h2>
        <p>
          Nextrace sits beside the tools engineers already use. It observes the execution path,
          records durable metadata, and leaves prompts, auth headers, and raw tool output behind.
        </p>
      </div>
      <div className="flow-list" aria-label="Connection flow">
        {flowSteps.map((step, index) => (
          <div className="flow-item" key={step}>
            <span>{index + 1}</span>
            <p>{step}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function SignalsSection() {
  return (
    <section id="signals" className="band">
      <div className="section-heading">
        <SignalsIcon />
        <p className="eyebrow">Signals</p>
        <h2>Turn AI behaviour into insight.</h2>
      </div>
      <div className="signal-grid">
        {signals.map(([title, body]) => (
          <article className="signal-card" key={title}>
            <h3>{title}</h3>
            <p>{body}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function RunSection() {
  const [copyState, setCopyState] = useState("idle");
  const mounted = useRef(true);
  const resetTimer = useRef(null);
  const copyLabel = copyState === "copied" ? "Copied" : copyState === "failed" ? "Try again" : "Copy";

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      window.clearTimeout(resetTimer.current);
    };
  }, []);

  async function handleCopyInstall() {
    let nextState;
    try {
      await copyText(installCommand);
      nextState = "copied";
    } catch {
      nextState = "failed";
    }

    if (!mounted.current) return;
    setCopyState(nextState);
    window.clearTimeout(resetTimer.current);
    resetTimer.current = window.setTimeout(() => setCopyState("idle"), 1800);
  }

  return (
    <section id="run" className="band run-section">
      <div className="run-copy install-heading">
        <TerminalIcon />
        <p className="eyebrow">Run it locally</p>
        <h2>Install once. Let the agents report back.</h2>
        <p>
          Clone Nextrace, install the dashboard extra, and register the local services that keep
          usage, traces, cost, and MCP activity flowing into the dashboard.
        </p>
      </div>

      <div className="install-command-shell" aria-label="One-command installation">
        <div className="install-command-copy">
          <span>Quick install</span>
          <button
            className="copy-command"
            type="button"
            onClick={handleCopyInstall}
            aria-label="Copy install command"
          >
            <CopyIcon />
            <span aria-live="polite">{copyLabel}</span>
          </button>
        </div>
        <div className="install-command-row">
          <span className="prompt">$</span>
          <code>{installCommand}</code>
        </div>
      </div>

      <div className="install-content">
        <ol className="install-steps">
          {installSteps.map((step, index) => (
            <li className="install-step" key={step.title}>
              <span>{index + 1}</span>
              <div>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
                <pre>
                  <code>{step.command}</code>
                </pre>
              </div>
            </li>
          ))}
        </ol>

        <aside className="install-notes" aria-label="Install details">
          <div>
            <p className="eyebrow">What starts</p>
            <ul className="service-list">
              {serviceFacts.map(([label, value]) => (
                <li key={label}>
                  <strong>{label}</strong>
                  <span>{value}</span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h3>Requirements</h3>
            <ul className="requirement-list">
              {requirements.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>

          <div className="install-actions">
            <a
              className="button primary repo-button"
              href="https://github.com/philipbloch/Nextrace"
              target="_blank"
              rel="noreferrer"
            >
              <GitHubIcon />
              View on GitHub
            </a>
            <span>Defaults to dashboard port 8765 and MCP proxy port 8766.</span>
          </div>
        </aside>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="site-footer">
      <p>Nextrace</p>
      <p>Trace. measure. improve.</p>
    </footer>
  );
}

export default function App() {
  return (
    <>
      <Header />
      <main id="top">
        <Hero />
        <MetricStrip />
        <ConnectSection />
        <SignalsSection />
        <RunSection />
      </main>
      <Footer />
    </>
  );
}
