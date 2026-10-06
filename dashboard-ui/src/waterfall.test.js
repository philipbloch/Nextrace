import test from "node:test";
import assert from "node:assert/strict";
import { layoutWaterfall } from "./waterfall.js";

const span = (id, parent_id, started_at, ended_at, metadata = {}) => ({
  id,
  parent_id,
  started_at,
  ended_at,
  metadata,
});
test("nested and overlapping work shares one time axis", () => {
  const layout = layoutWaterfall({
    started_at: 10,
    ended_at: 20,
    spans: [
      span("child", "root", 14, 16),
      span("root", null, 12, 18),
      span("parallel", null, 13, 19),
    ],
  });
  assert.equal(layout.durationMs, 10000);
  assert.deepEqual(
    layout.rows.map((r) => [r.span.id, r.depth]),
    [
      ["root", 0],
      ["child", 1],
      ["parallel", 0],
    ],
  );
  assert.equal(layout.rows[1].offset, 40);
  assert.equal(layout.rows[1].width, 20);
  assert.deepEqual(layout.rows[1].ancestors, ["root"]);
});
test("orphaned parents and cycles preserve every step exactly once", () => {
  const { rows } = layoutWaterfall({
    started_at: 10,
    ended_at: 20,
    spans: [span("a", "b", 10, 12), span("b", "a", 11, 13), span("c", "missing", 12, 14)],
  });
  assert.equal(rows.length, 3);
  assert.equal(new Set(rows.map((r) => r.span.id)).size, 3);
  assert.equal(rows.find((r) => r.span.id === "c").depth, 0);
});
test("unknown timings stay as points and live roots use observed bounds", () => {
  const layout = layoutWaterfall({
    started_at: 10,
    ended_at: null,
    metadata: { last_observed_at: 16 },
    spans: [span("usage", null, 15, 15, { timing: "unknown" })],
  });
  assert.equal(layout.durationMs, 6000);
  assert.equal(layout.rows[0].width, 0);
  assert.equal(layout.rows[0].unknown, true);
  assert.ok(Number.isFinite(layout.rows[0].offset));
});
