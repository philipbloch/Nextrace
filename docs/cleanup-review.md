# Cleanup review — October 6, 2026

Reviewed the Python tracing/storage/import/export code, provider adapters, MCP transports,
both React applications, CSS, installer, and regression coverage before committing.

## Changes

- Removed declaration-repeating docstrings. Kept rationale for ownership locks,
  atomic writes, protocol framing, redaction, and uncertain completion timestamps.
- Consolidated annotation remapping into one transaction helper shared by both
  migration paths. Matching legacy spans is separate from persisting a snapshot.
- Reused the execution-owner SQL join in summaries and grouped correlated spans
  once when building details, instead of scanning them for each observation.
- Consolidated importer accounting, file discovery, token normalization, and CLI
  connection reporting. Removed the deterministic Claude message-ID cache.
- Made imported turn creation explicit about whether a boundary was observed.
  Turn finalisation now gathers span statuses in one pass instead of scanning all
  spans once per turn.
- Consolidated dashboard request cancellation/loading/error handling in one hook.
  Active selection is derived from returned traces; the detail-version counter and
  selection-synchronisation effects are gone. Status tabs still load independently
  of summaries.
- Used outcome lookups instead of repeated nested status ternaries. Normalized HTTP
  headers once per proxy request.
- Replaced three copied XML templates with one plistlib writer. The installer shrank
  from 190 to 92 lines and handles paths/URLs containing XML-special characters.
- Made JSONL reads tolerate an incomplete UTF-8 tail without hiding earlier valid
  events. Added a regression that appends the remaining bytes and verifies recovery.
- Removed unused dashboard selectors and formatted JavaScript/JSX/CSS consistently.

The cleanup adds no runtime dependencies or framework layers. Public command names,
provider adapters, trace IDs, stored data shapes, and annotation-preservation behavior
remain supported. Formatting expands some frontend lines; line count is not used as
an improvement claim.

## Error diagnosis and spacing review

The follow-up review covered the diagnosis capture and presentation changes, then
checked the remaining Python modules, provider adapters, CLI, installer, both React
applications, stylesheets, and regression coverage. The scan included 61 source and
test files. The referenced Rass package was not installed; this repository has no
`api/` or `mobile/` tree, so the review used its Python and React conventions.

### Shared diagnostic capture

HTTP and MCP failures repeated message truncation, redaction, code extraction, and
correlation-ID collection. They now use `_error_details` in the existing protocol
module. HTTP JSON bodies are decoded once. The message limit remains independent
of the transient inspection buffer, and successful payloads remain omitted.

Before:

```python
body = json.loads(payload)
self._read_message(payload)
```

After:

```python
self._read_message(bytes(self._buffer))
```

That parsing path handles matching RPC responses and then uses the shared builder
for ordinary HTTP error bodies. A guard stops processing message blocks once the
capture limit is reached.

### Diagnosis control flow

Explanation selection previously assigned and overwrote the same variable through
several conditionals. Guard clauses now express the priority of interruption, HTTP,
JSON-RPC, MCP, and exception outcomes. A mixed batch now uses the JSON-RPC failure's
own code; previously it could look up the first MCP error's code instead.

Before:

```javascript
RPC_FAILURES[codes[0]]
diagnosis.details.some((item) => item.message)
```

After:

```javascript
RPC_FAILURES[rpc?.code ?? legacyCode]
diagnosis.hasMessage
```

`hasMessage` is computed once. `hasFailure` supplies one predicate for selecting a
failed step and resolving its diagnosis. The trace/session rows and the gap below
Recorded failure retain the spacing verified in the browser.

### Proxy and CLI simplification

Removed two stdio thread functions that only forwarded to another function. Threads
now target `copy_json_lines` and `copy_bytes` directly. Their loops read until EOF
with assignment expressions instead of a separate read/check/break sequence.

Malformed UTF-8 and excessively nested JSON now leave the forwarding loop intact.
Regression cases verify exact forwarded bytes and that the following valid message
is still observed.

Before:

```python
except (UnicodeDecodeError, json.JSONDecodeError):
    return None
```

After:

```python
except (ValueError, RecursionError):
    return None
```

Shared MCP application, server, and session flags now use argparse's existing
parent-parser mechanism. The HTTP and stdio commands retain their separate
transport options.

### Comments, scope, and documentation

Removed six declaration-repeating docstrings. Kept explanations for incomplete log
tails, unknown timing, stream framing, owner locks, annotation migration, redaction,
and safe export behavior. The legacy migration description now explains why old
`ended_at` values match new event timestamps.

The source scan found no TODO/FIXME implementation placeholders or unused stylesheet
class selectors. Provider entry points retain their explicit sync/async semantics;
no provider dispatch framework or new runtime layer was introduced. Landing-page
copy now describes failure-message capture and successful-output omission accurately.

## Inspection scroll follow-up

Removed the redundant `trace-detail` wrapper and its ineffective flex/overflow
rules. The existing panel body owns scrolling; its right padding preserves the
card spacing. `scrollBy` now uses the section's offset directly, without adding
and then reassigning the pane's current scroll position.

Before:

```javascript
pane.scrollTo({ top: pane.scrollTop + sectionTop - paneTop - 8, behavior });
```

After:

```javascript
pane.scrollBy({ top: sectionTop - paneTop - 8, behavior });
```

The post-render scroll effect handles selections that change the displayed content.
The click handler scrolls existing content for repeat selections that cause no
state change. Both use one callback and respect reduced-motion preferences.

A redaction regression demonstrated that truncation could cut off a credential's
closing quote, leaving part of its value in a diagnostic. Quoted-value matching
now handles end-of-input as well as a closing quote. Single- and double-quoted
regressions confirm that truncated messages retain no credential fragments.

Removed one redundant correlation docstring; the adjacent comment explains why
later imports require recomputing ambiguous links.

## Validation

- 160 Python tests and 10 JavaScript tests pass.
- Ruff checks/formatting and git diff whitespace checks pass.
- Both frontend production builds and the Python wheel build succeed.
- All three generated plist definitions parse, including escaped paths and URLs.
- Browser checks verify all five status tabs, repeated Error-tab clicks, failed-step
  selection, and diagnosis content after rebuilding. Running and OK inspection
  scrolling is verified through step names, timing bars, and repeat selections.

The Python suite reports one existing FastAPI/Starlette test-client deprecation
warning. It has no failing checks.

Validation uses provider fixtures and a local HTTP receiver; no paid model requests
or production Observe delivery are part of this cleanup. Review completed before committing.
