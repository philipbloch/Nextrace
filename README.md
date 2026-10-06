<p>
  <img src="assets/nextrace-logo.svg" alt="Nextrace logo" width="72" height="72" />
</p>

# Nextrace

Trace the intelligence.

Nextrace is a local-first Python toolkit for tracing AI workflows. It records execution steps, latency, token usage, errors, tool calls, retrieval, handoffs, scores, and feedback in SQLite, then presents them in a local dashboard.

Use Nextrace with instrumented Python code, MCP servers, or local Codex, Claude Code, and Pi usage logs.

## Install

Requires Python 3.10 or later.

```bash
git clone https://github.com/philipbloch/Nextrace.git
cd Nextrace
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dashboard]"
```

Install only the dependency-free core package with `python -m pip install -e .`.

## Quick start

```python
from nextrace import ai_trace

with ai_trace("support-bot", session_id="ticket-123") as trace:
    result = agent.run(message)

    trace.model_call(
        provider="openai",
        model="gpt-4.1-mini",
        prompt=message,
        response=result,
    )
    trace.tool_call(
        name="lookup_order",
        arguments={"order_id": "1001"},
        result={"status": "shipped"},
        success=True,
    )
    trace.score("helpfulness", 0.92)
```

Traces are written to `~/.nextrace/traces.db` by default.

Start the dashboard:

```bash
nextrace dashboard
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765).

## Connect data

### Python integrations

Nextrace includes helpers for:

- OpenAI: `traced_openai_chat` and `async_traced_openai_chat`
- Anthropic: `traced_anthropic_messages` and `async_traced_anthropic_messages`
- Gemini: `traced_gemini_generate`
- JSON HTTP endpoints: `traced_http_json`
- Local sync or async functions: `traced_model_call`

Provider clients remain optional. Install convenience extras when needed:

```bash
python -m pip install -e ".[openai,anthropic,gemini]"
```

### Local coding agents

Import changed Codex, Claude Code, and Pi usage logs with one command:

```bash
nextrace import-local-usage
```

The importer groups model and tool events by project, session, and turn. It tracks changed files and records token counts without raw prompts, responses, tool arguments, or tool outputs. Use `--source codex`, `--source claude`, or `--source pi` to select one source.

### MCP

Proxy a stdio MCP server:

```bash
nextrace mcp-proxy \
  --application auto \
  --server my-server \
  -- uvx my-mcp-server
```

Proxy an HTTP MCP endpoint:

```bash
nextrace mcp-http-proxy \
  --application auto \
  --server my-server \
  --target https://mcp.example.com/mcp
```

MCP proxies record tool names, timing, status, response size, and redacted argument
shapes. Successful tool output and full request/response headers are not stored.

HTTP connection operations use transport spans. An optional event-stream `GET` or
session-cleanup `DELETE` returning `405` keeps its HTTP status and records an
unsupported operation without failing the trace. Other HTTP and connection errors
still fail the trace.

The HTTP proxy inspects JSON and SSE responses, including gzip and deflate, for
matching JSON-RPC errors and tool results with `isError: true`. It forwards response
bytes unchanged. Failed responses retain explanatory text, error codes, and selected
correlation IDs from JSON-RPC, error data, structured content, and HTTP headers.
Failed HTTP responses can also capture JSON or plain-text error messages. Messages
are capped at 16,384 characters; obvious credential assignments, bearer tokens, and
URL credentials/query values are redacted. Other text in an error message is retained.
A final SSE result is recorded as soon as it arrives.

Select a failed trace or waterfall step to open **Error diagnosis**. It distinguishes
HTTP failures from MCP operation failures, shows the original captured message and
codes, and exposes correlation IDs and recorded exception tracebacks. Historical
recordings without error text show that the original tool response or upstream logs
are needed; omitted messages cannot be recovered. Diagnostics stay in the local
database and are not included in OpenTelemetry exports.

Traces use four states:

| State | Meaning |
| --- | --- |
| `running` | Recording is still active. |
| `ok` | A completed result was recorded without an error. |
| `error` | A completed failure was recorded. |
| `interrupted` | Recording ended without a complete result, or cancellation was requested. |

Failure rates use completed `ok` and `error` runs. Running and interrupted records
have separate counts and filters. Python callers can explicitly stop a recording
with `trace.interrupt("reason")`; cancellation exceptions also produce interrupted
traces and spans.

Recording owners hold local OS locks under `.nextrace-owners/` beside the database.
Dashboard reads recover unfinished traces whose owner has exited, without imposing
a timeout on long-running calls. If the actual completion time is unknown, it stays
unset. Older unfinished records without ownership metadata are shown as running
until their writer can be verified to have stopped.

Response inspection is bounded to 8 MiB of buffered data. Truncated responses,
unsupported encodings, and inspection-limit failures produce interrupted
recordings, while forwarding continues. An interrupted HTTP recording does not
imply that the upstream server cancelled the operation. SSE reconnects are separate
HTTP recordings.

## Commands

| Command | Purpose |
| --- | --- |
| `nextrace dashboard` | Run the local dashboard |
| `nextrace import-local-usage` | Import changed Codex, Claude Code, and Pi logs |
| `nextrace codex-import` | Import selected Codex sessions |
| `nextrace claude-import` | Import selected Claude Code transcripts |
| `nextrace pi-import` | Import selected Pi sessions |
| `nextrace mcp-proxy` | Proxy a stdio MCP server |
| `nextrace mcp-http-proxy` | Proxy an HTTP MCP endpoint |
| `nextrace export-otel` | Export completed executions as OTLP/HTTP JSON |
| `nextrace set-project` | Set the project used by proxies in auto mode |

Run `nextrace <command> --help` for command options.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `NEXTRACE_DB` | `~/.nextrace/traces.db` | SQLite database path |
| `NEXTRACE_APPLICATION` | Inferred | Application name used in auto mode |
| `NEXTRACE_PROJECT_STATE` | `~/.nextrace/current-project.json` | Active-project state file |
| `NEXTRACE_SESSION_ID` | Unset | Agent session context for a dedicated MCP proxy |
| `NEXTRACE_TURN_ID` | Unset | Agent turn context for a dedicated MCP proxy |
| `NEXTRACE_OTLP_HEADERS` | `{}` | JSON object of headers for explicit OTLP sends |

## Sessions, turns, and timings

Codex imports use task/turn IDs and completion events. Claude Code and Pi imports
use human-message IDs as turn boundaries and associate tool results by call ID.
Claude Code uses `parentUuid` and Pi uses `parentId` to follow recorded branches.
One turn contains its model events, tool calls, and results. Reimports update stable
IDs and preserve scores, feedback, and events. Model IDs use message IDs or Codex
event timestamps and usage counters, so log rewrites that shift line positions do
not duplicate model calls. Legacy records are consolidated only when their IDs
or unique timestamp/token identities agree; unmatched history stays independent.
The importer state version changes
once so existing logs are rebuilt automatically on the next import.

Model usage events have no measured latency and appear as dots labelled **Timing
unavailable**. Tool call/result timestamps provide **log intervals**, which include
agent scheduling and transport overhead. Python instrumentation and MCP proxies
measure elapsed time directly. The waterfall displays parent-child nesting and
parallel work on one time axis. Select a step for its details; collapse a parent to
hide its children. Missing or cyclic parents are displayed without dropping steps.

MCP recordings are correlated with an imported turn when either:

- The request includes matching agent session and turn IDs.
- Exactly one recorded turn contains the entire call in the same project and
  application, with observed turn start and completion timestamps.

Overlapping or unfinished turns prevent inference. Correlation is recomputed as
new logs arrive; ambiguous calls remain independent. The active project state supplies
the proxy's project path, so keep it current with `nextrace set-project`. This fallback
cannot identify a caller's project across simultaneous projects sharing a global proxy;
use per-request context for that setup. Existing MCP records without a project path
remain independent.

For HTTP clients, send `X-Nextrace-Session-Id`, `X-Nextrace-Turn-Id`, and optionally
`X-Nextrace-Project-Path`. Stdio and HTTP clients can instead include MCP metadata:

```json
{
  "method": "tools/call",
  "id": 1,
  "params": {
    "name": "lookup_order",
    "arguments": {"order_id": "1001"},
    "_meta": {
      "nextrace": {
        "session_id": "agent-session-id",
        "turn_id": "agent-turn-id",
        "project_path": "/path/to/project"
      }
    }
  }
}
```

The MCP transport session ID is distinct from the agent session ID. Correlated
proxy observations remain separate stored records and appear nested inside their
turn. If exactly one logged tool interval encloses the observation, it is nested
under that tool. Log events and proxy observations may describe the same operation;
Nextrace retains both observations instead of treating them as distinct business actions.

Missing completion events stay unknown. A new turn without the previous turn's
completion marks that earlier recording interrupted without inventing an end time.
Cost estimation, pricing configuration, and repricing have been removed. Token
counts remain; use Shopify's native tools for cost reporting. Existing database
cost columns are ignored, and historical monetary metadata is omitted from API
responses. Existing trace data is preserved.

## OpenTelemetry export

Download a completed execution with **Export OpenTelemetry** in the dashboard,
or write a batch to a local file:

```bash
nextrace export-otel --application support-bot --output traces.otel.json
nextrace export-otel --trace-id TRACE_ID --output one-trace.otel.json
nextrace export-otel --since 2026-10-06 --until 2026-10-07
```

The output is an [OTLP/HTTP JSON ExportTraceServiceRequest](https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding),
with service, session, turn, parent-child relationships, observed timestamps,
statuses, and token attributes. Raw prompts, responses, arbitrary metadata,
credentials, and exception messages are excluded. Unknown model timings remain
zero-length spans with a timing attribute; running recordings and records with
unknown completion timestamps are omitted.

Send explicitly to an OTLP/HTTP JSON collector using the full traces endpoint:

```bash
nextrace export-otel --output traces.otel.json --endpoint http://127.0.0.1:4318/v1/traces
```

For authentication, provide `NEXTRACE_OTLP_HEADERS` as a JSON object through your
normal secret configuration. Export writes the local file before sending, reports
partial acceptance or HTTP errors, and never deletes traces. Sends are manual
batches; no background delivery, retry queue, or collector deduplication is implied.
Repeated exports send the same deterministic span IDs again. To use Shopify Observe,
configure an approved OTLP collector/route and its required authentication; the
Observe browser URL is not an OTLP endpoint.

## Optional macOS services

The core library, dashboard, proxies, and importers are platform-independent. On macOS, the included installer can run the dashboard, HTTP MCP proxy, and recurring local importer as LaunchAgents:

```bash
scripts/install-launch-agents.sh
```

Defaults are dashboard port `8765`, proxy port `8766`, and a five-minute import interval. Override them with `NEXTRACE_DASHBOARD_PORT`, `NEXTRACE_TOOL_GATEWAY_PORT`, and `NEXTRACE_IMPORT_INTERVAL`.

## Data and privacy

- Nextrace stores data locally in SQLite using WAL mode so dashboard reads can run alongside imports.
- Python instrumentation stores the prompt, response, and metadata supplied by the caller.
- Local log importers and MCP proxies store redacted metadata instead of raw prompts, responses, tool output, or credentials.

## Development

```bash
python -m pytest
python -m ruff check .
npm --prefix dashboard-ui test
```

Rebuild the dashboard UI:

```bash
cd dashboard-ui
npm ci
npm run build
```

Build the product site:

```bash
cd quick-site
npm ci
npm run build
```

## License

[MIT](LICENSE)
