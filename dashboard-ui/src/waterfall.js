export function layoutWaterfall(trace) {
  const spans = trace.spans || [];
  const byId = new Map(spans.map((span) => [span.id, span]));
  const parents = new Map(spans.map((span) => [span.id, span.parent_id]));
  for (const span of spans) {
    let parent = parents.get(span.id);
    const seen = new Set([span.id]);
    while (byId.has(parent)) {
      if (seen.has(parent)) {
        parents.set(span.id, null);
        break;
      }
      seen.add(parent);
      parent = parents.get(parent);
    }
    if (!byId.has(parents.get(span.id))) parents.set(span.id, null);
  }
  const children = new Map();
  for (const span of spans) {
    const parent = parents.get(span.id) || null;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(span);
  }
  for (const list of children.values()) {
    list.sort((a, b) => a.started_at - b.started_at || a.id.localeCompare(b.id));
  }
  const start = Math.min(trace.started_at, ...spans.map((s) => s.started_at));
  const end = Math.max(
    start,
    trace.ended_at ?? trace.metadata?.last_observed_at ?? start,
    ...spans.map((s) => s.ended_at ?? s.started_at),
  );
  const range = end - start || 1;
  const rows = [];
  function walk(parent, depth, ancestors) {
    for (const span of children.get(parent) || []) {
      const spanEnd = Math.max(span.started_at, span.ended_at ?? span.started_at);
      rows.push({
        span,
        depth,
        ancestors,
        children: (children.get(span.id) || []).length,
        offset: Math.max(0, Math.min(100, ((span.started_at - start) / range) * 100)),
        width: Math.max(0, Math.min(100, ((spanEnd - span.started_at) / range) * 100)),
        unknown: span.metadata?.timing === "unknown",
      });
      walk(span.id, depth + 1, [...ancestors, span.id]);
    }
  }
  walk(null, 0, []);
  return { rows, durationMs: (end - start) * 1000 };
}
