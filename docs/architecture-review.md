# Nextrace architecture review

Historical review of the October 5 implementation. The October 6 execution-view
update removes pricing and repricing, imports full turn event groups, adds a nested
waterfall, and supports OTLP export. See README.md for current behavior.

Reviewed October 5, 2026. Scope: Python tracing, storage, importers, pricing,
integrations, MCP proxies, CLI, dashboard API, both React applications, CSS,
packaging, and the macOS service installer.

The existing architecture fits a local tracing tool: a dependency-free core,
SQLite persistence, optional provider adapters, and a small HTTP dashboard.
The main problems were duplicated lifecycle code and weak persistence guarantees.
A framework migration, ORM, plugin registry, or frontend state library would add
complexity without addressing those problems.

## Changes made

| Area | Finding | Result |
| --- | --- | --- |
| Storage | `INSERT OR REPLACE` deleted existing traces and cascaded into annotations during re-import. Trace and span writes were separate transactions. | Upserts preserve attached records; imported traces and spans commit together or roll back together. |
| SQLite lifecycle | Transaction contexts did not close connections. | Connections now close explicitly after commit or rollback. |
| Storage queries | Repeated JSON decoders and filter construction could diverge. | Shared decoding and filter construction retain the existing response shapes. |
| Usage imports | Three copies of ID generation, JSONL parsing, coercion, and trace construction. Claude parsing could fail on a valid JSON scalar. | Shared ingestion primitives preserve physical line numbers and stable IDs; source-specific token accounting remains in each adapter. |
| Import state | Fingerprints taken after an import could mark a concurrent append as already processed. State writes could leave partial JSON. | Importers save the pre-import fingerprint and replace state files atomically. |
| Provider integrations | Success, failure, timing, and persistence logic was copied between sync and async wrappers. | Adapters use the core span context, including exception handling; only provider-specific invocation and usage extraction remain. |
| HTTP integration | Retry branches duplicated persistence; malformed JSON could escape without a failed span. | One span covers the request and retries; decoding failures are recorded. |
| Async tracing | Parallel child spans shared a mutable parent pointer. | Parent tracking is scoped to the async context. |
| MCP proxy | Recorder failures could interrupt HTTP forwarding; response recording was duplicated. Stdio `isError` results could be counted as successes. | Recorder failures are isolated, completion recording uses `finally`, and stdio tool errors are classified. The earlier optional-405 fix is preserved. |
| Redaction | Uppercase credential keys and URL user information could escape redaction. | Key normalization handles uppercase/acronym forms and URL user information is removed. |
| Repricing | Claude cache writes without a duration breakdown were omitted; local dates could disagree with import-time UTC dates. | Repricing preserves those cache writes and uses UTC dates. Negative cache-write counts cannot reduce estimated cost. |
| CLI | A large dispatch ladder and proxy wrappers repeated argument forwarding. Dashboard reload used an app instance that Uvicorn cannot reload. | Argparse binds handlers directly; reload uses an importable app factory. |
| Dashboard | Day ranges assumed 24 hours, Refresh did not reload selected details, and every filter change requested a second full-history summary just to populate the application selector. | Calendar-day boundaries handle DST, details refresh, and a dedicated application-list query replaces the extra summary. |
| Product site | Preview filter buttons were inert; privacy copy overstated what Python instrumentation redacts. | Filters work and the copy distinguishes redacted import/proxy metadata from caller-supplied Python payloads. |
| Code hygiene | Boilerplate module comments, unused theme tokens, misleading color aliases, and redundant frontend sorting. | Removed or consolidated; responsive layouts and public command names are preserved. |

At the end of the cleanup pass, application source decreased from 6,661 to 6,282 lines, including the new shared
helpers, after formatting. This comparison excludes tests, CSS, and generated
bundles and uses the working tree at the start of this review. The functional
follow-up below adds new behavior and regression coverage after that comparison.

## Functional follow-up

Both tracing gaps identified in the review are now addressed:

- The HTTP proxy matches JSON-RPC response IDs in JSON and SSE bodies and records
  JSON-RPC errors and `isError` tool results even when HTTP status is 200. It handles
  split UTF-8, CR/LF/CRLF framing, multiline SSE data, gzip, and deflate. Notifications
  and unrelated request IDs do not count as results. Response bytes remain unchanged.
- Traces start as running and finish as OK, error, or interrupted. Cancellation,
  interrupted forwarding, and proxy shutdown are distinct from completed failures.
  Local OS ownership locks allow recovery after a killed process without guessing
  whether a long-running call has timed out. Unknown completion timestamps remain
  unknown. Running/interrupted records are excluded from completed-run failure rates
  and have their own dashboard counts and filters.

Protocol references: [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[SSE framing](https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream).

## Validation

- 129 Python tests pass, including persistence rollback, annotation preservation,
  concurrent appends, async parent isolation, credential redaction, provider
  exceptions, retries, malformed inputs, CLI dispatch, and reload configuration.
  Follow-up tests cover byte-preserving JSON/SSE forwarding, early SSE completion,
  request/session correlation, cancellation, graceful shutdown, and recovery after
  killing one writer while a second writer remains active.
- Two JavaScript date-range tests pass, covering both DST transitions, midnight,
  invalid dates, and reversed ranges.
- Ruff checks and formatting checks pass; `git diff --check` and shell syntax
  validation pass.
- Both Vite production builds succeed. The rebuilt dashboard assets are included
  in the Python package.
- The wheel builds successfully; its extracted package records a trace and
  contains the dashboard assets in an isolated Python process.
- Browser checks verified dashboard loading, filtering, refresh, trace detail,
  application selection data, and the product preview's OK/Error filters. No
  browser errors were observed in those checks.
- Follow-up browser checks verified Running/Interrupted filters, separate counts,
  and unknown completion-time labels. A live Tool Gateway call was matched to its
  JSON-RPC response and persisted with `mcp_response_complete: true`. The follow-up
  rollout waited for an idle proxy; no unowned running traces remain in the local database.

## Remaining boundaries

1. **Inspection is bounded.** The HTTP inspector buffers at most 8 MiB and supports
   identity, gzip, and deflate encodings. If inspection cannot observe a complete
   result, the recording is interrupted rather than falsely successful; forwarding
   continues. Each HTTP connection, including an SSE reconnect, remains its own trace.
2. **Legacy ownership cannot be invented.** Unfinished records created before owner
   tracking are shown as running. They can be marked interrupted only after their
   original writer is known to have stopped. Existing explicit interruption annotations
   are migrated automatically. Recovery stores a detection timestamp, not a fabricated
   completion timestamp.
3. **Validation used provider fixtures.** No paid model requests or live provider
   pricing verification were performed. Token pricing remains a configurable
   estimate; streaming SDK responses have not been validated by this review.
4. **Platform checks ran on macOS with Python 3.13 and Node 24.** The project still
   declares Python 3.10+, but this pass did not execute a multi-version or Windows
   test matrix.

During the cleanup pass, the local dashboard and proxy were reloaded after the backend checks. That proxy
reload interrupted two in-flight Observe calls. Their trace records were backed
up and marked as interrupted rather than left looking successful; their unknown
completion times were preserved. Subsequent calls and dashboard requests worked.
The temporary product-site preview was used only for validation; no site was
published.
